"""Project native stdio benchmark evidence without inventing deployment telemetry."""

from __future__ import annotations

import re
from typing import Any, get_args

from scripts.benchmark import summarize
from scripts.evidence_acceptance import digest, fields, identifier, require
from scripts.evidence_benchmark_common import (
    FINGERPRINT_FIELDS,
    MAX_MEASUREMENT,
    _fingerprint_dimension,
    _selected_cases,
    comparable,
    fingerprint_value,
)
from scripts.evidence_envelope import decode_json, validate_envelope
from scripts.evidence_http import project_http

from nci_si_mcp.errors import ErrorCode

_FIELDS = {
    "mode",
    "transport",
    "createdAt",
    "serverVersion",
    "sourceSha256",
    "python",
    "platform",
    "suite",
    "repetitions",
    "expectedCases",
    "complete",
    "conditions",
    "cases",
}
_SAMPLE_FIELDS = {
    "elapsedMs",
    "resultBytes",
    "outboundRequests",
    "responseCode",
    "error",
    "release",
}
MAX_REPETITIONS = 10_000


def _header(report: Any) -> None:
    fields(report, _FIELDS)
    require(report["mode"] in ("fixture", "live"))
    # HTTP has no local audit telemetry; #194 adds its own explicitly versioned format.
    require(report["transport"] == "stdio")
    require(type(report["complete"]) is bool)
    require(type(report["repetitions"]) is int and 0 < report["repetitions"] <= MAX_REPETITIONS)
    for key in ("createdAt", "serverVersion", "sourceSha256", "python", "platform"):
        require(isinstance(report[key], str))
    require(re.fullmatch(r"[a-f0-9]{64}", report["sourceSha256"]) is not None)
    fields(report["suite"], {"version", "fixture_set", "digest"})
    require(all(isinstance(value, str) for value in report["suite"].values()))
    require(re.fullmatch(r"[a-f0-9]{64}", report["suite"]["digest"]) is not None)
    require(isinstance(report["conditions"], dict) and isinstance(report["cases"], list))


def _sample(sample: Any) -> None:
    fields(sample, _SAMPLE_FIELDS)
    elapsed = sample["elapsedMs"]
    require(type(elapsed) in (float, int) and 0 <= elapsed <= MAX_MEASUREMENT)
    for key in ("resultBytes", "outboundRequests"):
        require(type(sample[key]) is int and 0 <= sample[key] <= MAX_MEASUREMENT)
    require(type(sample["error"]) is bool)
    require(sample["responseCode"] in ("ok", *get_args(ErrorCode)))
    require(sample["error"] == (sample["responseCode"] != "ok"))
    require(isinstance(sample["release"], dict))


def _phase(phase: Any, repetitions: int) -> dict[str, Any]:
    fields(phase, {"summary", "samples"})
    require(isinstance(phase["samples"], list) and len(phase["samples"]) == repetitions)
    for sample in phase["samples"]:
        _sample(sample)
    summary = summarize(phase["samples"])
    fields(phase["summary"], set(summary))
    require(all(type(value) in (float, int) for value in phase["summary"].values()))
    require(phase["summary"] == summary)
    return {
        "summary": summary,
        "errors": sum(sample["error"] for sample in phase["samples"]),
        "samples": [sample["elapsedMs"] for sample in phase["samples"]],
    }


def _expected_cases(expected: Any) -> None:
    require(isinstance(expected, list))
    for name in expected:
        identifier(name)
    require(bool(expected) and len(set(expected)) == len(expected))


def _cases(report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    expected = report["expectedCases"]
    _expected_cases(expected)
    result = []
    for case in report["cases"]:
        result.append(_case(case, report["repetitions"]))
    found = [row["tool"] for row in result]
    require(len(set(found)) == len(found) and set(found) <= set(expected))
    missing = [name for name in expected if name not in found]
    require(not report["complete"] or not missing)
    return result, missing


def _case(case: Any, repetitions: int) -> dict[str, Any]:
    fields(case, {"tool", "arguments", "scenario", "cold", "warm"})
    identifier(case["tool"])
    require(isinstance(case["arguments"], dict))
    require(case["scenario"] is None or isinstance(case["scenario"], str))
    return {
        "tool": case["tool"],
        "cold": _phase(case["cold"], repetitions),
        "warm": _phase(case["warm"], repetitions),
    }


def _fingerprint(report: dict[str, Any]) -> dict[str, str | None]:
    result: dict[str, str | None] = dict.fromkeys(FINGERPRINT_FIELDS)
    for key in ("mode", "transport"):
        result[key] = fingerprint_value(report[key])
    result["definitions"] = fingerprint_value(report["conditions"])
    result["samples"] = fingerprint_value(report["repetitions"])
    result["workload"] = fingerprint_value(
        [{key: row[key] for key in ("tool", "arguments", "scenario")} for row in report["cases"]]
    )
    # Native historical reports do not independently attest environment, cache/index/model,
    # client placement, hardware or requested profile. Unknowns deliberately block comparison.
    return result


def _selection_fingerprint(
    selection: bytes | None,
    record: dict[str, Any],
    native: dict[str, Any],
) -> dict[str, str | None]:
    measured = _fingerprint(native)
    if selection is None:
        return measured
    manifest = decode_json(selection)
    require(digest(selection) == record["selection_sha256"])
    fields(manifest, {"schema", "cases", "fingerprint"})
    require(type(manifest["schema"]) is int and manifest["schema"] == 1)
    _selected_cases(manifest["cases"], native)
    # An interrupted report contains completed cases only; the wrapper retains the full plan.
    measured["workload"] = fingerprint_value(manifest["cases"])
    fingerprint = manifest["fingerprint"]
    fields(fingerprint, FINGERPRINT_FIELDS)
    for key, value in fingerprint.items():
        _fingerprint_dimension(value, measured[key])
    return fingerprint


def project_benchmark(
    envelope: bytes,
    report: bytes | None,
    *,
    selection: bytes | None = None,
) -> dict[str, Any]:
    """Validate native measured samples and summaries; keep absent operating facts unknown.

    Without a bound wrapper selection, the declared case inventory is checked internally only.
    Neither matching hashes nor a complete fingerprint establish producer trust.
    """
    record = validate_envelope(envelope, report)
    require(record["kind"] == "benchmark")
    if report is None:
        return record | {
            "mode": None,
            "transport": None,
            "cases": None,
            "missing": None,
            "inventory_complete": False,
            "selection_verified": False,
            "comparison_ready": False,
            "fingerprint": dict.fromkeys(FINGERPRINT_FIELDS),
        }
    native = decode_json(report)
    if isinstance(native, dict) and native.get("transport") == "streamable-http":
        return project_http(record, native, selection)
    _header(native)
    cases, missing = _cases(native)
    result = record | {
        "mode": native["mode"],
        "transport": native["transport"],
        "cases": cases,
        "missing": missing,
        "inventory_complete": native["complete"] and record["state"] == "completed",
        "selection_verified": selection is not None,
        "fingerprint": _selection_fingerprint(selection, record, native),
    }
    result["comparison_ready"] = comparable(result, result)[0]
    return result
