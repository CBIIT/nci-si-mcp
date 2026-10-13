"""Shared benchmark selection bindings and comparison rules across transports."""

from __future__ import annotations

from typing import Any

from scripts.benchmark_report import FINGERPRINT_FIELDS


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
