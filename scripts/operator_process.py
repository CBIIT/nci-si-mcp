"""Bounded owned process groups with a parent-liveness pipe; no detached validation work."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from scripts.evidence_envelope import MAX_REPORT_BYTES

LIFELINE = "NCI_SI_OPERATOR_LIFELINE"


def _signal_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, sig)


def _stop(process: subprocess.Popen[bytes]) -> None:
    _signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGKILL)
        process.wait(timeout=10)
    # A group leader can exit while an owned descendant ignores graceful termination.
    _signal_group(process, signal.SIGKILL)


def _output_problem(directory: Path) -> str | None:
    report = directory / "bundle/report.json"
    if report.is_symlink():
        return "invalid_evidence"
    if report.exists() and report.stat().st_size > MAX_REPORT_BYTES:
        return "output_limit"
    return None


def _wait(
    process: subprocess.Popen[bytes],
    directory: Path,
    seconds: float,
    cancelled: threading.Event,
) -> str | None:
    deadline = time.monotonic() + seconds
    while process.poll() is None:
        if cancelled.is_set():
            return "cancelled"
        if time.monotonic() >= deadline:
            return "deadline"
        if problem := _output_problem(directory):
            return problem
        time.sleep(0.05)
    return _output_problem(directory)


def run_owned(
    command: list[str],
    *,
    directory: Path,
    environment: dict[str, str],
    seconds: float,
    cancelled: threading.Event,
) -> dict[str, Any]:
    if cancelled.is_set():
        return {"state": "cancelled", "exit_code": None, "reason": "cancelled"}
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb"), os.fdopen(write_fd, "wb"):
        process = subprocess.Popen(  # noqa: S603 - caller supplies only fixed reviewed worker commands
            command,
            cwd=Path(__file__).resolve().parents[1],
            env=environment | {LIFELINE: str(read_fd)},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(read_fd,),
        )
        try:
            reason = _wait(process, directory, seconds, cancelled)
        finally:
            _stop(process)
    code = process.returncode if process.returncode >= 0 else 128 - process.returncode
    if reason is not None:
        return {
            "state": "cancelled" if reason == "cancelled" else "failed",
            "exit_code": code,
            "reason": reason,
        }
    return {
        "state": "completed" if code == 0 else "failed",
        "exit_code": code,
        "reason": None if code == 0 else "worker_failed",
    }


def watch_parent() -> None:
    """The group leader kills its owned session if the controller's pipe disappears."""
    if os.getpgrp() != os.getpid():
        raise RuntimeError("Validation worker must lead its own process group")
    descriptor = int(os.environ.pop(LIFELINE))
    os.set_inheritable(descriptor, False)

    def parent_lost() -> None:
        try:
            os.read(descriptor, 1)
        finally:
            os.close(descriptor)
            # No controller remains to escalate graceful shutdown if a child ignores it.
            os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=parent_lost, name="validation-owner-liveness", daemon=True).start()
