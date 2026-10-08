"""Atomic local evidence history; validated content is not authenticated provenance."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scripts.evidence_acceptance import project_acceptance
from scripts.evidence_benchmark import project_benchmark
from scripts.evidence_envelope import validate_envelope

MAX_BUNDLE_BYTES = 24 * 1024 * 1024
MAX_RETENTION = 1000
_FILES = {"envelope", "report", "catalogue", "stories", "expectations", "selection"}


class EvidenceNotFoundError(KeyError):
    """A requested retained run or route does not exist."""


def _stored(raw: str) -> dict[str, Any]:
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise sqlite3.DatabaseError("Stored evidence is malformed") from exc
    if not isinstance(result, dict):
        raise sqlite3.DatabaseError("Stored evidence is not a record")
    return result


def _bound_bundle(bundle: dict[str, bytes]) -> bytes:
    if not set(bundle) <= _FILES or "envelope" not in bundle:
        raise ValueError("Evidence bundle contains unexpected or missing filenames")
    if sum(len(value) for value in bundle.values()) > MAX_BUNDLE_BYTES:
        raise ValueError("Evidence bundle exceeds 24 MiB")
    encoded = {name: base64.b64encode(raw).decode("ascii") for name, raw in bundle.items()}
    return json.dumps(encoded, sort_keys=True, separators=(",", ":")).encode()


def load_bundle(directory: Path) -> dict[str, bytes]:
    """Read only fixed names, with a total bound; never follow an imported file symlink."""
    if directory.is_symlink():
        raise ValueError("Evidence bundle cannot be a symlink")
    result = {}
    remaining = MAX_BUNDLE_BYTES
    for name in sorted(_FILES):
        path = directory / (name + ".json")
        if path.is_symlink():
            raise ValueError("Evidence files cannot be symlinks")
        if not path.exists():
            continue
        with path.open("rb") as stream:
            raw = stream.read(remaining + 1)
        remaining -= len(raw)
        if remaining < 0:
            raise ValueError("Evidence bundle exceeds 24 MiB")
        result[name] = raw
    _bound_bundle(result)
    return result


def _project(bundle: dict[str, bytes]) -> dict[str, Any]:
    envelope, report = bundle["envelope"], bundle.get("report")
    metadata = validate_envelope(envelope, report)
    if metadata["kind"] == "benchmark":
        return project_benchmark(envelope, report, selection=bundle.get("selection"))
    required = {"catalogue", "stories", "expectations", "selection"}
    if not required <= set(bundle):
        raise ValueError("Acceptance evidence needs its original inventory snapshots")
    return project_acceptance(envelope, report, **{name: bundle[name] for name in required})


def _summary(evidence: dict[str, Any]) -> dict[str, Any]:
    names = ("run_id", "kind", "state", "mode", "transport", "inventory_complete")
    return {name: evidence.get(name) for name in names}


class EvidenceStore:
    """Keep bounded terminal imports in local arrival order, independent of supplied clocks."""

    def __init__(self, path: Path, *, retention: int = 100):
        if type(retention) is not int or not 1 <= retention <= MAX_RETENTION:
            raise ValueError("Retention must be 1 to 1000 records")
        self.path, self.retention = path, retention
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS runs (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL,
                summary TEXT NOT NULL, projection TEXT NOT NULL, bundle BLOB NOT NULL)""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def import_bundle(self, bundle: dict[str, bytes]) -> str:
        raw = _bound_bundle(bundle)
        evidence = _project(bundle)
        return self._insert(evidence, raw)

    def import_legacy(self, raw: bytes, kind: str) -> str:
        if kind not in {"acceptance", "benchmark"} or len(raw) > MAX_BUNDLE_BYTES:
            raise ValueError("Legacy evidence needs a supported kind and at most 24 MiB")
        evidence = {
            "run_id": uuid.uuid4().hex,
            "kind": kind,
            "state": "unverified",
            "inventory_complete": False,
            "report_sha256": hashlib.sha256(raw).hexdigest(),
        }
        return self._insert(evidence, raw)

    def _insert(self, evidence: dict[str, Any], raw: bytes) -> str:
        checksum = hashlib.sha256(raw).hexdigest()
        run_id = evidence["run_id"]
        projection = json.dumps(evidence)
        if len(projection.encode()) > MAX_BUNDLE_BYTES:
            raise ValueError("Projected evidence exceeds 24 MiB")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._existing(connection, run_id, checksum):
                return run_id
            connection.execute(
                "INSERT INTO runs(run_id,checksum,summary,projection,bundle) VALUES(?,?,?,?,?)",
                (run_id, checksum, json.dumps(_summary(evidence)), projection, raw),
            )
            connection.execute(
                """DELETE FROM runs WHERE sequence NOT IN (
                SELECT sequence FROM runs ORDER BY sequence DESC LIMIT ?)""",
                (self.retention,),
            )
        return run_id

    def _existing(self, connection: sqlite3.Connection, run_id: str, checksum: str) -> bool:
        row = connection.execute("SELECT checksum FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is not None and row["checksum"] != checksum:
            raise ValueError("Run ID already names different evidence")
        return row is not None

    def history(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT sequence,summary FROM runs ORDER BY sequence DESC")
            return [_stored(row["summary"]) | {"sequence": row["sequence"]} for row in rows]

    def latest(self) -> dict[str, Any]:
        history = self.history()
        complete = next((row for row in history if row["inventory_complete"]), None)
        return {"attempt": history[0] if history else None, "complete": complete}

    def get(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise EvidenceNotFoundError("Evidence record not found")
        return {
            "sequence": row["sequence"],
            "checksum": row["checksum"],
            "origin": "local-import-unverified",
            "evidence": _stored(row["projection"]),
        }
