"""Safe native acceptance projections bound to the run's original catalogue snapshots."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from scripts.evidence_envelope import EvidenceError, decode_json, validate_envelope

from nci_si_acceptance.report import FAILED, RANK, UNRUN, complete, completion_problem, tool_outcome

_REPORT_FIELDS = {
    "mode",
    "transport",
    "suite",
    "failed_gates",
    "unrun_gates",
    "tools_list_bytes",
    "tools",
    "tests",
}
_TOOL_FIELDS = {"group", "outcome", "gates_only", "counts"}
# The 29 reports recorded before the harness dropped the field (docs/evidence/phase-5) carry
# `implemented_as`, which a report has no schema number to version; they stay valid as recorded.
_RECORDED_TOOL_FIELD = "implemented_as"


def require(condition: bool) -> None:
    """Reject contradictions without including private values in diagnostics."""
    if not condition:
        raise EvidenceError("Invalid evidence report or original inventory binding")


def fields(value: Any, expected: set[str]) -> None:
    require(isinstance(value, dict) and set(value) == expected)


def identifier(value: Any) -> None:
    require(isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,99}", value) is not None)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _catalogue(value: Any) -> None:
    fields(value, {"schema", "suite_digest", "tools", "cases"})
    require(type(value["schema"]) is int and value["schema"] == 1)
    require(isinstance(value["suite_digest"], str))
    require(re.fullmatch(r"[a-f0-9]{64}", value["suite_digest"]) is not None)
    require(isinstance(value["tools"], dict) and isinstance(value["cases"], dict))
    for name, group in value["tools"].items():
        identifier(name)
        identifier(group)
    for name, case in value["cases"].items():
        _catalogue_case(name, case, value["tools"])


def _catalogue_case(name: str, case: Any, tools: dict[str, str]) -> None:
    # Node IDs are used for exact equality and hashed for display, never as paths or markup.
    require(bool(name))
    fields(case, {"tool", "gate"})
    require(type(case["gate"]) is bool)
    require(case["tool"] is None or isinstance(case["tool"], str))
    require(case["tool"] is None or case["tool"] in tools)


def _context(record: dict[str, Any], snapshots: dict[str, bytes]) -> dict[str, Any]:
    result = {}
    for name, raw in snapshots.items():
        require(digest(raw) == record[name + "_sha256"])
        result[name] = decode_json(raw)
    _catalogue(result["catalogue"])
    cases = result["catalogue"]["cases"]
    for name in ("stories", "expectations"):
        fields(result[name], set(cases))
    for story in result["stories"].values():
        identifier(story)
    require(all(isinstance(v, str) and v in RANK for v in result["expectations"].values()))
    _selection(result["selection"], cases)
    return result


def _selection(selected: Any, cases: dict[str, Any]) -> None:
    require(isinstance(selected, list))
    require(all(isinstance(name, str) for name in selected))
    require(len(set(selected)) == len(selected))
    require(bool(selected) and set(selected) <= set(cases))


def _native_header(report: dict[str, Any], catalogue: dict[str, Any]) -> None:
    require(isinstance(report, dict) and set(report) - {"run"} == _REPORT_FIELDS)
    require(report["mode"] in ("fixture", "live"))
    require(report["transport"] in ("stdio", "streamable-http"))
    fields(report["suite"], {"version", "fixture_set", "digest"})
    require(all(isinstance(value, str) for value in report["suite"].values()))
    require(report["suite"]["digest"] == catalogue["suite_digest"])
    size = report["tools_list_bytes"]
    require(size is None or (type(size) is int and size >= 0))
    require(isinstance(report["tests"], dict))
    fields(report["tools"], set(catalogue["tools"]))


def _native_cases(report: dict[str, Any], context: dict[str, Any]) -> None:
    require(set(report["tests"]) <= set(context["selection"]))
    for name, row in report["tests"].items():
        fields(row, {"tool", "gate", "outcome", "unmatched"})
        expected = context["catalogue"]["cases"][name]
        require(type(row["gate"]) is bool and row["gate"] == expected["gate"])
        require(row["tool"] == expected["tool"])
        require(isinstance(row["outcome"], str) and row["outcome"] in RANK)
        require(isinstance(row["unmatched"], list))
        require(all(isinstance(value, str) for value in row["unmatched"]))


def _gates(report: dict[str, Any], field: str, outcomes: tuple[str, ...]) -> list[str]:
    expected = sorted(
        name for name, row in report["tests"].items() if row["gate"] and row["outcome"] in outcomes
    )
    require(report[field] == expected)
    return expected


def _tool(row: Any, counts: Counter[str], group: str, failed: bool) -> dict[str, Any]:
    require(isinstance(row, dict) and set(row) - {_RECORDED_TOOL_FIELD} == _TOOL_FIELDS)
    require(row["group"] == group)
    _counts(row["counts"], counts)
    availability = _availability(row)
    require(isinstance(row["outcome"], str))
    require(row["outcome"] in {tool_outcome(counts, failed, value) for value in availability})
    expected = failed and not (counts["failed"] or counts["no_fixture"])
    require(type(row["gates_only"]) is bool and row["gates_only"] == expected)
    return {
        "group": group,
        "outcome": row["outcome"],
        "gates_only": row["gates_only"],
        "counts": dict(counts),
    }


def _availability(row: dict[str, Any]) -> tuple[bool | None, ...]:
    """The availability verdicts the row's outcome may rest on.

    A recorded row names the tool it was served as. The native report cannot distinguish an
    unstarted server from a missing tool if every alias is null, and a current row has no
    alias: preserve any valid harness verdict instead of inventing availability.
    """

    if _RECORDED_TOOL_FIELD not in row:
        return (None, False, True)
    alias = row[_RECORDED_TOOL_FIELD]
    require(alias is None or isinstance(alias, str))
    return (None, False) if alias is None else (True,)


def _counts(actual: Any, expected: Counter[str]) -> None:
    require(isinstance(actual, dict))
    require(all(type(value) is int and value > 0 for value in actual.values()))
    require(actual == dict(expected))


def _native(raw: bytes, context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    report = decode_json(raw)
    _native_header(report, context["catalogue"])
    _native_cases(report, context)
    failed = bool(_gates(report, "failed_gates", FAILED))
    _gates(report, "unrun_gates", UNRUN)
    tools = {}
    for name, group in context["catalogue"]["tools"].items():
        counts = Counter(row["outcome"] for row in report["tests"].values() if row["tool"] == name)
        tools[name] = _tool(report["tools"][name], counts, group, failed)
    return report, tools


def _case_rows(context: dict[str, Any], tests: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": digest(name.encode()),
            "story": context["stories"][name],
            "expected": context["expectations"][name],
            "outcome": tests.get(name, {}).get("outcome"),
            **context["catalogue"]["cases"][name],
        }
        for name in context["selection"]
    ]


def _inventory_complete(native: dict[str, Any], missing: list[str], state: str) -> bool:
    return (
        not missing
        and state in ("completed", "failed")
        and ("run" not in native or complete(native))
    )


def project_acceptance(
    envelope: bytes,
    report: bytes | None,
    *,
    catalogue: bytes,
    stories: bytes,
    expectations: bytes,
    selection: bytes,
) -> dict[str, Any]:
    """Validate a native report against bound original snapshots; return only safe fields.

    This is a local projection, not an authentication or public publication decision. The
    caller supplies snapshots from the recorded run, never substitutes the current checkout.
    A historical import without these snapshots remains unverified outside this adapter.
    Reports with run metadata must also pass the harness's completion predicate. Older
    imports without it retain the independent selection-bound completeness check alone;
    no historical completion fields are invented. Partial evidence remains displayable.
    """
    record = validate_envelope(envelope, report)
    require(record["kind"] == "acceptance")
    context = _context(
        record,
        {
            "catalogue": catalogue,
            "stories": stories,
            "expectations": expectations,
            "selection": selection,
        },
    )
    native, tools = _native(report, context) if report is not None else ({"tests": {}}, None)
    tests = native["tests"]
    missing = [digest(name.encode()) for name in context["selection"] if name not in tests]
    return record | {
        "mode": native.get("mode"),
        "transport": native.get("transport"),
        "inventory_complete": _inventory_complete(native, missing, record["state"]),
        "completion_problem": completion_problem(native) if "run" in native else None,
        "missing": missing,
        "cases": _case_rows(context, tests),
        "tools": tools,
        "counts": dict(Counter(row["outcome"] for row in tests.values()))
        if report is not None
        else None,
    }
