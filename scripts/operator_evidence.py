"""Bind worker reports to independently recorded execution facts and original inventories."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.evidence_acceptance import digest
from scripts.evidence_envelope import MAX_ENVELOPE_BYTES, decode_json
from scripts.operator_files import read_bytes, write_json
from scripts.portal_store import MAX_BUNDLE_BYTES, EvidenceStore

ORIGINAL = ("catalogue", "stories", "expectations", "selection")


def _original(directory: Path) -> dict[str, bytes]:
    remaining = MAX_BUNDLE_BYTES - MAX_ENVELOPE_BYTES
    result = {}
    for name in ORIGINAL:
        result[name] = read_bytes(directory / "bundle" / f"{name}.json", remaining)
        remaining -= len(result[name])
    return result


def bind_original(directory: Path, commit: str) -> None:
    original = _original(directory)
    binding = {"commit": commit, "digests": {name: digest(raw) for name, raw in original.items()}}
    write_json(directory / "binding.json", binding)


def _bound_original(job: dict[str, Any], directory: Path) -> dict[str, bytes]:
    original = _original(directory)
    expected = {
        "commit": job["commit"],
        "digests": {name: digest(raw) for name, raw in original.items()},
    }
    binding = decode_json(read_bytes(directory / "binding.json", MAX_ENVELOPE_BYTES))
    if binding != expected:
        raise ValueError("Original validation inventory changed during execution")
    report = directory / "bundle/report.json"
    if report.exists() or report.is_symlink():
        remaining = MAX_BUNDLE_BYTES - MAX_ENVELOPE_BYTES - sum(map(len, original.values()))
        original["report"] = read_bytes(report, remaining)
    return original


def _envelope(job: dict[str, Any], result: dict[str, Any], bundle: dict[str, bytes]) -> dict:
    report = bundle.get("report")
    state = result["state"]
    if state == "failed" and result["exit_code"] in (0, None):
        state = "interrupted"
    return {
        "schema": 1,
        "access": "maintainer-admin",
        "run_id": job["run_id"],
        "kind": "acceptance" if job["profile"] == "acceptance-http-fixture" else "benchmark",
        "runner_commit": job["commit"],
        **{name + "_sha256": digest(bundle[name]) for name in ORIGINAL},
        "started_at": job["started_at"],
        "finished_at": datetime.now(UTC).isoformat(),
        "state": state,
        "exit_code": result["exit_code"],
        "report_sha256": digest(report) if report is not None else None,
        "server_commit": None if job["profile"] == "benchmark-http-remote" else job["commit"],
    }


def finish_evidence(
    job: dict[str, Any],
    directory: Path,
    result: dict[str, Any],
    store: EvidenceStore,
) -> dict[str, Any]:
    try:
        bundle = _bound_original(job, directory)
        if result["state"] == "completed" and "report" not in bundle:
            result = result | {"state": "unavailable", "reason": "missing_report"}
        envelope = _envelope(job, result, bundle)
        path = directory / "bundle/envelope.json"
        write_json(path, envelope)
        store.import_bundle(bundle | {"envelope": read_bytes(path, MAX_ENVELOPE_BYTES)})
    except ValueError:
        return result | {"state": "failed", "reason": "invalid_evidence"}
    except FileNotFoundError:
        if result["state"] in {"cancelled", "interrupted"}:
            return result
        return result | {"state": "unavailable", "reason": "missing_inventory"}
    return result
