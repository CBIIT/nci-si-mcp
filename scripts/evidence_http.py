"""Validate versioned HTTP benchmark evidence without promoting declared server telemetry."""

from __future__ import annotations

import re
from typing import Any, get_args

from scripts.benchmark_limits import Limits
from scripts.evidence_acceptance import digest, fields, identifier, require
from scripts.evidence_benchmark_common import (
    FINGERPRINT_FIELDS,
    MAX_MEASUREMENT,
    _fingerprint_dimension,
    _selected_cases,
    comparable,
    fingerprint_value,
)
from scripts.evidence_envelope import _time, decode_json
from scripts.http_measurement import summarize_http

from nci_si_mcp.errors import ErrorCode

MAX_CONDITION_TEXT = 512

_FIELDS = {
    "schema",
    "transport",
    "mode",
    "createdAt",
    "state",
    "complete",
    "stopReason",
    "httpRequests",
    "limits",
    "targetSha256",
    "expectedCases",
    "conditions",
    "cases",
}
_SAMPLE = {
    "elapsedMs",
    "resultBytes",
    "httpRequests",
    "responseCode",
    "error",
    "correlationId",
    "outboundRequests",
    "cacheState",
    "serverCommit",
    "replica",
}
_FAILURES = {
    "target",
    "redirect",
    "response_limit",
    "content_encoding",
    "deadline",
    "request_budget",
    "cancelled",
    "timeout",
    "invalid_response",
    "transport_error",
    "runner_error",
}
PHASE_LABELS = {
    "cold": "First call in new client session",
    "warm": "Warmed client session",
    "warmups": "Warm-up (excluded from measured phases)",
}


def _hex(value: Any, length: int) -> None:
    require(isinstance(value, str) and re.fullmatch(rf"[a-f0-9]{{{length}}}", value) is not None)


def _failure(value: Any) -> None:
    require(isinstance(value, str))
    require(value in _FAILURES or re.fullmatch(r"http_[45][0-9]{2}", value) is not None)


def _header(native: Any, record: dict[str, Any]) -> Limits:
    fields(native, _FIELDS)
    require(type(native["schema"]) is int and native["schema"] == 1)
    require(native["transport"] == "streamable-http" and native["mode"] in ("fixture", "live"))
    fields(native["limits"], {"max_requests", "seconds", "timeout", "repetitions", "warmups"})
    limits = Limits(**native["limits"])
    require(
        type(native["httpRequests"]) is int and 0 <= native["httpRequests"] <= limits.max_requests
    )
    _hex(native["targetSha256"], 64)
    _time(native["createdAt"])
    _termination(native, record)
    _conditions(native["conditions"])
    require(isinstance(native["cases"], list))
    return limits


def _conditions(conditions: Any) -> None:
    labels = {"cold", "warm", "timing", "remoteTermination", "serverTelemetry"}
    fields(conditions, labels | {"concurrency"})
    require(type(conditions["concurrency"]) is int and conditions["concurrency"] == 1)
    for key in labels:
        value = conditions[key]
        require(isinstance(value, str) and 0 < len(value) <= MAX_CONDITION_TEXT)


def _termination(native: dict[str, Any], record: dict[str, Any]) -> None:
    state = native["state"]
    require(state in ("running", "completed", "failed", "cancelled"))
    require(type(native["complete"]) is bool and native["complete"] == (state == "completed"))
    if state in ("running", "completed"):
        require(native["stopReason"] is None)
    else:
        _failure(native["stopReason"])
        require((state == "cancelled") == (native["stopReason"] == "cancelled"))
    if record["state"] == "completed":
        require(state == "completed")


def _number(value: Any) -> None:
    require(type(value) in (int, float) and 0 <= value <= MAX_MEASUREMENT)


def _sample(sample: Any, limits: Limits) -> None:
    fields(sample, _SAMPLE)
    _number(sample["elapsedMs"])
    require(
        type(sample["httpRequests"]) is int and 0 <= sample["httpRequests"] <= limits.max_requests
    )
    _hex(sample["correlationId"], 32)
    require(
        all(
            sample[key] is None
            for key in ("outboundRequests", "cacheState", "serverCommit", "replica")
        )
    )
    _result(sample)


def _result(sample: dict[str, Any]) -> None:
    code = sample["responseCode"]
    if code not in ("ok", *get_args(ErrorCode)):
        _failure(code)
    require(type(sample["error"]) is bool and sample["error"] == (code != "ok"))
    size = sample["resultBytes"]
    if size is None:
        require(sample["error"])
    else:
        require(type(size) is int and 0 <= size <= MAX_MEASUREMENT)


def _phase(phase: Any, count: int, limits: Limits) -> dict[str, Any]:
    fields(phase, {"samples", "summary"})
    require(isinstance(phase["samples"], list) and len(phase["samples"]) <= count)
    for sample in phase["samples"]:
        _sample(sample, limits)
    summary = summarize_http(phase["samples"])
    fields(phase["summary"], set(summary))
    require(
        all(value is None or type(value) in (int, float) for value in phase["summary"].values())
    )
    require(phase["summary"] == summary)
    return {
        "summary": summary,
        "errors": sum(row["error"] for row in phase["samples"]),
        "samples": [row["elapsedMs"] for row in phase["samples"]],
    }


def _cases(native: dict[str, Any], limits: Limits) -> tuple[list[dict[str, Any]], list[str]]:
    expected = native["expectedCases"]
    require(isinstance(expected, list) and bool(expected))
    for name in expected:
        identifier(name)
    require(len(set(expected)) == len(expected))
    result, missing = [], []
    for case in native["cases"]:
        projected, complete = _case(case, limits)
        result.append(projected)
        if not complete:
            missing.append(case["tool"])
    require([row["tool"] for row in result] == expected)
    require(not native["complete"] or not missing)
    _totals(native)
    return result, missing


def _totals(native: dict[str, Any]) -> None:
    samples = [
        sample
        for case in native["cases"]
        for phase in PHASE_LABELS
        for sample in case[phase]["samples"]
    ]
    correlations = [sample["correlationId"] for sample in samples]
    require(len(set(correlations)) == len(correlations))
    require(sum(sample["httpRequests"] for sample in samples) <= native["httpRequests"])


def _case(case: Any, limits: Limits) -> tuple[dict[str, Any], bool]:
    fields(case, {"tool", "arguments", "scenario", "cold", "warm", "warmups"})
    identifier(case["tool"])
    require(isinstance(case["arguments"], dict))
    require(case["scenario"] is None or isinstance(case["scenario"], str))
    result: dict[str, Any] = {"tool": case["tool"]}
    complete = True
    for phase in PHASE_LABELS:
        count = limits.warmups if phase == "warmups" else limits.repetitions
        result[phase] = _phase(case[phase], count, limits)
        complete &= len(case[phase]["samples"]) == count
    return result, complete


def http_fingerprint(native: dict[str, Any]) -> dict[str, str | None]:
    """Comparable planned facts; unattested server and execution conditions remain unknown."""
    measured: dict[str, str | None] = dict.fromkeys(FINGERPRINT_FIELDS)
    for key in ("mode", "transport"):
        measured[key] = fingerprint_value(native[key])
    measured["definitions"] = fingerprint_value(native["conditions"])
    measured["samples"] = fingerprint_value(native["limits"]["repetitions"])
    measured["warmup"] = fingerprint_value(native["limits"]["warmups"])
    measured["concurrency"] = fingerprint_value(1)
    measured["timeouts"] = fingerprint_value(
        {key: native["limits"][key] for key in ("seconds", "timeout", "max_requests")}
    )
    measured["workload"] = fingerprint_value(
        [{key: row[key] for key in ("tool", "arguments", "scenario")} for row in native["cases"]]
    )
    return measured


def _selection(measured: dict, native: dict, record: dict, selection: bytes | None) -> dict:
    if selection is None:
        return measured
    manifest = decode_json(selection)
    require(digest(selection) == record["selection_sha256"])
    fields(manifest, {"schema", "cases", "fingerprint"})
    require(type(manifest["schema"]) is int and manifest["schema"] == 1)
    _selected_cases(manifest["cases"], native)
    fields(manifest["fingerprint"], FINGERPRINT_FIELDS)
    for key, value in manifest["fingerprint"].items():
        _fingerprint_dimension(value, measured[key])
    return manifest["fingerprint"]


def project_http(
    record: dict[str, Any], native: dict[str, Any], selection: bytes | None
) -> dict[str, Any]:
    limits = _header(native, record)
    cases, missing = _cases(native, limits)
    result = record | {
        "mode": native["mode"],
        "transport": native["transport"],
        "cases": cases,
        "missing": missing,
        "phase_labels": PHASE_LABELS,
        "inventory_complete": native["complete"] and record["state"] == "completed",
        "selection_verified": selection is not None,
        "fingerprint": _selection(http_fingerprint(native), native, record, selection),
    }
    result["comparison_ready"] = comparable(result, result)[0]
    return result
