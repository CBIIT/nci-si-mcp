"""Shared benchmark selection bindings and comparison rules across transports."""

from __future__ import annotations

import json
import re
from typing import Any

from scripts.evidence_acceptance import digest, fields, identifier, require

FINGERPRINT_FIELDS = {
    "mode",
    "transport",
    "definitions",
    "workload",
    "profile",
    "release",
    "index",
    "model",
    "hardware",
    "environment",
    "placement",
    "warmup",
    "samples",
    "concurrency",
    "timeouts",
}


MAX_MEASUREMENT = 2**63 - 1


def fingerprint_value(value: Any) -> str:
    """Fingerprint structured values; never expose raw arguments, URLs or machine labels."""
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _fingerprint_dimension(value: Any, measured: str | None) -> None:
    if value is not None:
        require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None)
    if measured is not None:
        require(value == measured)


def _selected_cases(cases: Any, native: dict[str, Any]) -> None:
    require(isinstance(cases, list))
    for case in cases:
        fields(case, {"tool", "arguments", "scenario"})
        identifier(case["tool"])
        require(isinstance(case["arguments"], dict))
        require(case["scenario"] is None or isinstance(case["scenario"], str))
    require([case["tool"] for case in cases] == native["expectedCases"])
    selected = {case["tool"]: case for case in cases}
    for row in native["cases"]:
        require(
            {key: row[key] for key in ("tool", "arguments", "scenario")} == selected[row["tool"]]
        )


def _comparison_status(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    reasons = []
    if not (left["selection_verified"] and right["selection_verified"]):
        reasons.append("unverified-selection")
    if not (left["inventory_complete"] and right["inventory_complete"]):
        reasons.append("incomplete")
    return reasons


def comparable(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, list[str]]:
    """Compare validated projections; unknown or differing dimensions forbid speedup claims."""
    reasons = _comparison_status(left, right)
    for key in sorted(FINGERPRINT_FIELDS):
        value = left["fingerprint"].get(key)
        if value is None or value != right["fingerprint"].get(key):
            reasons.append(key)
    return not reasons, reasons
