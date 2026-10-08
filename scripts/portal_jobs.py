"""One local owner, one active validation worker and a bounded durable job queue."""

from __future__ import annotations

import fcntl
import json
import logging
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from contextlib import ExitStack
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from scripts.operator_files import read_bytes, write_json

from nci_si_mcp.audit import emit

PROFILES = ("acceptance-http-fixture", "benchmark-http-fixture", "benchmark-http-remote")
ACTIVE = {"pending", "running"}
TERMINAL = {"completed", "failed", "cancelled", "interrupted", "unavailable"}
REASONS = {
    None,
    "cancelled",
    "deadline",
    "output_limit",
    "invalid_evidence",
    "worker_failed",
    "worker_error",
    "missing_report",
    "missing_inventory",
    "owner_restart",
    "owner_shutdown",
}
MAX_ADMITTED = 3
MAX_RETENTION = 1000
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_TIMESTAMP_LENGTH = 40
MAX_EXIT_CODE = 255
FIELDS = {
    "run_id",
    "profile",
    "state",
    "sequence",
    "commit",
    "submitted_at",
    "started_at",
    "finished_at",
    "exit_code",
    "reason",
}
Executor = Callable[[dict[str, Any], Path, threading.Event], dict[str, Any]]


class JobConflictError(ValueError):
    """A retained submission ID already identifies a different intent."""


class QueueFullError(RuntimeError):
    """The local worker and queue have reached their bounded admission count."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _identifier(value: str) -> None:
    if re.fullmatch(r"[a-f0-9]{32}", value) is None:
        raise ValueError("Invalid local job identifier")


def _validate_state(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"next", "jobs"}:
        raise ValueError("Invalid job state")
    if type(value["next"]) is not int or not isinstance(value["jobs"], list):
        raise ValueError("Invalid job state")
    for row in value["jobs"]:
        _validate_job(row)
    _validate_sequences(value)


def _validate_sequences(value: dict[str, Any]) -> None:
    identifiers = [row["run_id"] for row in value["jobs"]]
    sequences = [row["sequence"] for row in value["jobs"]]
    if len(set(identifiers)) != len(identifiers) or len(set(sequences)) != len(sequences):
        raise ValueError("Duplicate retained jobs")
    if value["next"] <= max(sequences, default=0):
        raise ValueError("Invalid next job sequence")
    if len(value["jobs"]) > MAX_RETENTION + MAX_ADMITTED:
        raise ValueError("Too many retained jobs")


def _validate_job(row: Any) -> None:
    if not isinstance(row, dict) or set(row) != FIELDS:
        raise ValueError("Invalid job record")
    _identifier(row["run_id"])
    if row["profile"] not in PROFILES or row["state"] not in ACTIVE | TERMINAL:
        raise ValueError("Unsupported job record")
    _validate_identity(row)
    _validate_execution(row)


def _validate_identity(row: dict[str, Any]) -> None:
    if type(row["sequence"]) is not int or re.fullmatch(r"[a-f0-9]{40}", row["commit"]) is None:
        raise ValueError("Invalid job identity")
    if row["sequence"] < 1:
        raise ValueError("Invalid job sequence")


def _validate_execution(row: dict[str, Any]) -> None:
    _timestamp(row["submitted_at"])
    for name in ("started_at", "finished_at"):
        if row[name] is not None:
            _timestamp(row[name])
    _validate_outcome(row)


def _timestamp(value: Any) -> None:
    if not isinstance(value, str) or len(value) > MAX_TIMESTAMP_LENGTH:
        raise ValueError("Invalid job timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != UTC.utcoffset(None):
        raise ValueError("Job timestamps must name UTC")


def _validate_outcome(row: dict[str, Any]) -> None:
    code = row["exit_code"]
    if code is not None and (type(code) is not int or not 0 <= code <= MAX_EXIT_CODE):
        raise ValueError("Invalid job exit code")
    if row["reason"] not in REASONS:
        raise ValueError("Invalid job stop reason")


class JobController:
    def __init__(
        self,
        root: Path,
        *,
        execute: Executor,
        commit: Callable[[], str],
        remote: bool = False,
        retention: int = 100,
    ) -> None:
        if type(retention) is not int or not 1 <= retention <= MAX_RETENTION:
            raise ValueError("Job retention must be between 1 and 1000")
        if root.is_symlink():
            raise ValueError("Job workspace cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        self.root, self.execute, self.commit, self.retention = root, execute, commit, retention
        self.profiles = PROFILES if remote else PROFILES[:2]
        self.condition = threading.Condition()
        self.cancelled = threading.Event()
        self.stopping = False
        self.failed = False
        with ExitStack() as startup:
            self.lease = (root / ".owner").open("a+")
            startup.callback(self.lease.close)
            self._claim()
            self.state = self._load()
            self._recover()
            self.thread = threading.Thread(target=self._work, name="local-validation", daemon=True)
            self.thread.start()
            startup.pop_all()

    def _claim(self) -> None:
        try:
            fcntl.flock(self.lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Local validation workspace is already owned") from None

    def _load(self) -> dict[str, Any]:
        path = self.root / "jobs.json"
        if not path.exists():
            return {"next": 1, "jobs": []}
        try:
            raw = read_bytes(path, MAX_STATE_BYTES)
            value = json.loads(raw)
            _validate_state(value)
        except ValueError, TypeError:
            raise OSError("Local job state is unavailable; preserve it for inspection") from None
        return value

    def _save(self) -> None:
        write_json(self.root / "jobs.json", self.state)

    def _recover(self) -> None:
        for row in self.state["jobs"]:
            if row["state"] in ACTIVE:
                row.update(state="interrupted", reason="owner_restart", finished_at=_now())
        self._save()

    def history(self) -> list[dict[str, Any]]:
        with self.condition:
            self._available()
            return deepcopy(
                sorted(self.state["jobs"], key=lambda row: row["sequence"], reverse=True)
            )

    def _get(self, run_id: str) -> dict[str, Any]:
        _identifier(run_id)
        for row in self.state["jobs"]:
            if row["run_id"] == run_id:
                return row
        raise KeyError("No such local job")

    def get(self, run_id: str) -> dict[str, Any]:
        with self.condition:
            self._available()
            return deepcopy(self._get(run_id))

    def submit(self, run_id: str, profile: str) -> dict[str, Any]:
        _identifier(run_id)
        if profile not in self.profiles:
            raise ValueError("Choose an enabled reviewed profile")
        with self.condition:
            self._available()
            if self.stopping:
                raise OSError("Local validation controller is stopping")
            existing = next((row for row in self.state["jobs"] if row["run_id"] == run_id), None)
            if existing is not None:
                return self._repeated(existing, profile)
            return self._admit(run_id, profile)

    def _repeated(self, row: dict[str, Any], profile: str) -> dict[str, Any]:
        if row["profile"] != profile:
            raise JobConflictError("Submission ID already names another profile")
        return deepcopy(row)

    def _admit(self, run_id: str, profile: str) -> dict[str, Any]:
        if sum(row["state"] in ACTIVE for row in self.state["jobs"]) >= MAX_ADMITTED:
            raise QueueFullError("One worker and two queued jobs are already admitted")
        row = {
            "run_id": run_id,
            "profile": profile,
            "state": "pending",
            "commit": self.commit(),
            "sequence": self.state["next"],
            "submitted_at": _now(),
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "reason": None,
        }
        _validate_job(row)
        self.state["next"] += 1
        self.state["jobs"].append(row)
        try:
            self._save()
        except OSError:
            self.state["jobs"].remove(row)
            self.state["next"] -= 1
            raise
        self.condition.notify_all()
        return deepcopy(row)

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self.condition:
            self._available()
            row = self._get(run_id)
            if row["state"] == "pending":
                row.update(state="cancelled", reason="cancelled", finished_at=_now())
                self._prune()
                self._save()
            elif row["state"] == "running":
                self.cancelled.set()
            return deepcopy(row)

    def _next(self) -> dict[str, Any] | None:
        return next((row for row in self.state["jobs"] if row["state"] == "pending"), None)

    def _work(self) -> None:
        try:
            self._dispatch()
        except Exception as error:  # noqa: BLE001 - fail the background controller closed
            with self.condition:
                self.failed = self.stopping = True
                self.cancelled.set()
            emit(
                logging.getLogger(__name__),
                logging.ERROR,
                "validation_controller_failed",
                errorType=type(error).__name__,
            )

    def _available(self) -> None:
        if self.failed:
            raise OSError("Local validation controller is unavailable; inspect its storage")

    def _dispatch(self) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopping or self._next() is not None)
                if self.stopping:
                    return
                row = cast("dict[str, Any]", self._next())
                self.cancelled.clear()
                row.update(state="running", started_at=_now())
                self._save()
            self._execute(row)

    def _execute(self, row: dict[str, Any]) -> None:
        try:
            result = self.execute(deepcopy(row), self.root / row["run_id"], self.cancelled)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            emit(
                logging.getLogger(__name__),
                logging.ERROR,
                "validation_worker_failed",
                errorType=type(error).__name__,
            )
            result = {"state": "unavailable", "exit_code": None, "reason": "worker_error"}
        with self.condition:
            row.update(result, finished_at=_now())
            if self.cancelled.is_set():
                row.update(state="cancelled", reason="cancelled")
            self._prune()
            self._save()

    def _prune(self) -> None:
        terminal = [row for row in self.state["jobs"] if row["state"] in TERMINAL]
        for row in terminal[: -self.retention]:
            path = self.root / row["run_id"]
            if path.exists() and not path.is_symlink():
                shutil.rmtree(path)
            self.state["jobs"].remove(row)

    def close(self) -> None:
        if self.lease.closed:
            return
        with self.condition:
            self.stopping = True
            self.cancelled.set()
            for row in self.state["jobs"]:
                if row["state"] == "pending":
                    row.update(state="cancelled", reason="owner_shutdown", finished_at=_now())
            self.condition.notify_all()
        try:
            with self.condition:
                self._save()
        finally:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise RuntimeError("Owned validation worker has not stopped")
            self.lease.close()
