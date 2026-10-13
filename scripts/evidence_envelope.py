"""Restricted run evidence; validation alone never grants publication or operator authority."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any, Literal, NoReturn

from scripts.formats import JOB_ID, SHA1_HEX, SHA256_HEX
from scripts.vocabulary import TERMINAL, TerminalJobState

MAX_ENVELOPE_BYTES = 65_536
MAX_REPORT_BYTES = 16_777_216
MAX_EXIT_CODE = 255

_TIMESTAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"


class EvidenceError(ValueError):
    """An invalid evidence envelope, without echoing potentially private input."""


def _reject(path: str = "envelope") -> NoReturn:
    raise EvidenceError(f"Invalid envelope field: {path}") from None


@dataclass(frozen=True)
class Envelope:
    schema: Literal[1]
    access: Literal["maintainer-admin"]
    run_id: str
    kind: Literal["acceptance", "benchmark"]
    runner_commit: str
    catalogue_sha256: str
    stories_sha256: str
    expectations_sha256: str
    selection_sha256: str
    started_at: str
    finished_at: str
    state: TerminalJobState
    exit_code: int | None
    report_sha256: str | None
    server_commit: str | None

    @classmethod
    def from_dict(cls, value: Any, report: bytes | None) -> Envelope:
        if not isinstance(value, dict):
            _reject()
        for name in sorted(item.name for item in fields(cls)):
            if name not in value:
                _reject(name)
        if set(value) != {item.name for item in fields(cls)}:
            _reject()
        _classification(value)
        _identity(value)
        _report(value, report)
        _termination(value, report is not None)
        return cls(**value)


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


def _hex(value: Any, pattern: str, path: str) -> None:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        _reject(path)


def timestamp(value: Any, path: str = "timestamp") -> datetime:
    if not isinstance(value, str) or re.fullmatch(_TIMESTAMP, value) is None:
        _reject(path)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        _reject(path)


def _identity(record: dict[str, Any]) -> None:
    _hex(record["run_id"], JOB_ID, "run_id")
    _hex(record["runner_commit"], SHA1_HEX, "runner_commit")
    for name in ("catalogue_sha256", "stories_sha256", "expectations_sha256", "selection_sha256"):
        _hex(record[name], SHA256_HEX, name)
    if record["server_commit"] is not None:
        _hex(record["server_commit"], SHA1_HEX, "server_commit")
    if timestamp(record["finished_at"], "finished_at") < timestamp(
        record["started_at"], "started_at"
    ):
        _reject("finished_at")


def _classification(record: dict[str, Any]) -> None:
    if type(record["schema"]) is not int or record["schema"] != 1:
        _reject("schema")
    if record["access"] != "maintainer-admin":
        _reject("access")
    if record["kind"] not in ("acceptance", "benchmark"):
        _reject("kind")
    if not isinstance(record["state"], str) or record["state"] not in TERMINAL:
        _reject("state")


def _report(record: dict[str, Any], report: bytes | None) -> None:
    if report is None:
        if record["report_sha256"] is not None:
            _reject("report_sha256")
        return
    if len(report) > MAX_REPORT_BYTES:
        _reject("report")
    _hex(record["report_sha256"], SHA256_HEX, "report_sha256")
    if hashlib.sha256(report).hexdigest() != record["report_sha256"]:
        _reject("report_sha256")


def _exit_code(code: Any) -> None:
    if code is not None and (type(code) is not int or not 0 <= code <= MAX_EXIT_CODE):
        _reject("exit_code")


def _termination(record: dict[str, Any], has_report: bool) -> None:
    code = record["exit_code"]
    _exit_code(code)
    state = record["state"]
    if state == "completed" and code != 0:
        _reject("exit_code")
    if state == "failed" and code in (None, 0):
        _reject("exit_code")
    allowed = {"completed": has_report, "unavailable": not has_report}
    if not allowed.get(state, True):
        _reject("state")


def validate_envelope(raw: bytes, report: bytes | None) -> dict[str, Any]:
    """Validate recorded execution metadata, not test verdicts, identity attestations or access.

    The caller separately establishes the producer's authority and supplies the exact report
    bytes. Matching hashes establish binding only. A completed process is not a passing test run.
    Report schemas and catalogue/selection digests need their own validation before comparison.
    """
    return asdict(Envelope.from_dict(decode_json(raw, MAX_ENVELOPE_BYTES), report))
