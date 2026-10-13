"""Bind worker reports to independently recorded execution facts and original inventories."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.evidence_envelope import MAX_ENVELOPE_BYTES, Envelope
from scripts.inventory_records import ORIGINAL, Binding, decode_inventory, digest
from scripts.operator_files import read_bytes, write_json
from scripts.portal_store import MAX_BUNDLE_BYTES, EvidenceStore
from scripts.vocabulary import JobOutcome, JobReason, TerminalJobState


def _original(directory: Path) -> dict[str, bytes]:
    remaining = MAX_BUNDLE_BYTES - MAX_ENVELOPE_BYTES
    result = {}
    for name in ORIGINAL:
        result[name] = read_bytes(directory / "bundle" / f"{name}.json", remaining)
        remaining -= len(result[name])
    return result


def bind_original(directory: Path, commit: str) -> None:
    original = _original(directory)
    binding = Binding(commit, {name: digest(raw) for name, raw in original.items()})
    write_json(directory / "binding.json", asdict(binding))


def _bound_original(job: dict[str, Any], directory: Path) -> dict[str, bytes]:
    original = _original(directory)
    expected = Binding(job["commit"], {name: digest(raw) for name, raw in original.items()})
    binding = decode_inventory(
        read_bytes(directory / "binding.json", MAX_ENVELOPE_BYTES), "binding"
    )
    Binding.from_dict(binding, expected)
    report = directory / "bundle/report.json"
    if report.exists() or report.is_symlink():
        remaining = MAX_BUNDLE_BYTES - MAX_ENVELOPE_BYTES - sum(map(len, original.values()))
        original["report"] = read_bytes(report, remaining)
    return original


def _envelope(job: dict[str, Any], result: JobOutcome, bundle: dict[str, bytes]) -> dict:
    report = bundle.get("report")
    state = result["state"]
    if state == "failed" and result["exit_code"] in (0, None):
        state = "interrupted"
    return asdict(
        Envelope(
            schema=1,
            access="maintainer-admin",
            run_id=job["run_id"],
            kind="acceptance" if job["profile"] == "acceptance-http-fixture" else "benchmark",
            runner_commit=job["commit"],
            catalogue_sha256=digest(bundle["catalogue"]),
            stories_sha256=digest(bundle["stories"]),
            expectations_sha256=digest(bundle["expectations"]),
            selection_sha256=digest(bundle["selection"]),
            started_at=job["started_at"],
            finished_at=datetime.now(UTC).isoformat(),
            state=state,
            exit_code=result["exit_code"],
            report_sha256=digest(report) if report is not None else None,
            server_commit=None if job["profile"] == "benchmark-http-remote" else job["commit"],
        )
    )


def _ended(result: JobOutcome, state: TerminalJobState, reason: JobReason) -> JobOutcome:
    return {"state": state, "exit_code": result["exit_code"], "reason": reason}


def finish_evidence(
    job: dict[str, Any],
    directory: Path,
    result: JobOutcome,
    store: EvidenceStore,
) -> JobOutcome:
    try:
        bundle = _bound_original(job, directory)
        if result["state"] == "completed" and "report" not in bundle:
            result = _ended(result, "unavailable", "missing_report")
        envelope = _envelope(job, result, bundle)
        path = directory / "bundle/envelope.json"
        write_json(path, envelope)
        store.import_bundle(bundle | {"envelope": read_bytes(path, MAX_ENVELOPE_BYTES)})
    except ValueError:
        return _ended(result, "failed", "invalid_evidence")
    except FileNotFoundError:
        if result["state"] in {"cancelled", "interrupted"}:
            return result
        return _ended(result, "unavailable", "missing_inventory")
    return result
