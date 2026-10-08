"""Restricted run evidence; validation alone never grants publication or operator authority."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any, NoReturn

MAX_ENVELOPE_BYTES = 65_536
MAX_REPORT_BYTES = 16_777_216
MAX_EXIT_CODE = 255

_DIGEST_LENGTHS = {
    "run_id": 32,
    "runner_commit": 40,
    "catalogue_sha256": 64,
    "stories_sha256": 64,
    "expectations_sha256": 64,
    "selection_sha256": 64,
}
_FIELDS = set(_DIGEST_LENGTHS) | {
    "schema",
    "access",
    "kind",
    "started_at",
    "finished_at",
    "state",
    "exit_code",
    "report_sha256",
    "server_commit",
}
_TIMESTAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"


class EvidenceError(ValueError):
    """An invalid evidence envelope, without echoing potentially private input."""


def _reject() -> NoReturn:
    raise EvidenceError(
        "Invalid restricted run evidence; check the versioned envelope contract"
    ) from None


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            _reject()
        record[key] = value
    return record


def _constant(_value: str) -> NoReturn:
    _reject()


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _reject()
    return parsed


def decode_json(raw: bytes, maximum: int = MAX_REPORT_BYTES) -> Any:
    """Read bounded strict JSON without echoing report content on parse failure."""
    if len(raw) > maximum:
        _reject()
    try:
        record = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_constant,
            parse_float=_finite_float,
        )
    except ValueError, UnicodeError, RecursionError:
        _reject()
    return record


def _hex(value: Any, length: int) -> None:
    if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        _reject()


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or re.fullmatch(_TIMESTAMP, value) is None:
        _reject()
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        _reject()


def _identity(record: dict[str, Any]) -> None:
    for field, length in _DIGEST_LENGTHS.items():
        _hex(record[field], length)
    if record["server_commit"] is not None:
        _hex(record["server_commit"], 40)
    if _time(record["finished_at"]) < _time(record["started_at"]):
        _reject()


def _classification(record: dict[str, Any]) -> None:
    if type(record["schema"]) is not int or record["schema"] != 1:
        _reject()
    if record["access"] != "maintainer-admin":
        _reject()
    if record["kind"] not in ("acceptance", "benchmark"):
        _reject()
    if record["state"] not in ("completed", "failed", "cancelled", "interrupted", "unavailable"):
        _reject()


def _report(record: dict[str, Any], report: bytes | None) -> None:
    if report is None:
        if record["report_sha256"] is not None:
            _reject()
        return
    if len(report) > MAX_REPORT_BYTES:
        _reject()
    _hex(record["report_sha256"], 64)
    if hashlib.sha256(report).hexdigest() != record["report_sha256"]:
        _reject()


def _exit_code(code: Any) -> None:
    if code is not None and (type(code) is not int or not 0 <= code <= MAX_EXIT_CODE):
        _reject()


def _termination(record: dict[str, Any], has_report: bool) -> None:
    code = record["exit_code"]
    _exit_code(code)
    allowed = {
        "completed": code == 0 and has_report,
        "failed": code not in (None, 0),
        "unavailable": not has_report,
        "cancelled": True,
        "interrupted": True,
    }
    if not allowed[record["state"]]:
        _reject()


def validate_envelope(raw: bytes, report: bytes | None) -> dict[str, Any]:
    """Validate recorded execution metadata, not test verdicts, identity attestations or access.

    The caller separately establishes the producer's authority and supplies the exact report
    bytes. Matching hashes establish binding only. A completed process is not a passing test run.
    Report schemas and catalogue/selection digests need their own validation before comparison.
    """
    record = decode_json(raw, MAX_ENVELOPE_BYTES)
    if not isinstance(record, dict) or set(record) != _FIELDS:
        _reject()
    _classification(record)
    _identity(record)
    _report(record, report)
    _termination(record, report is not None)
    return record
