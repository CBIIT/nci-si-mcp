"""Atomic installer state and ordered step reconciliation."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

STATE_VERSION = 1
STATE_NAME = "installer-state.json"
LOCK_NAME = "installer-state.lock"
STATUSES = frozenset({"completed", "blocked", "failed"})
TARGETS = frozenset({"local", "aws"})
COMPONENTS = ("server", "index", "documentation", "companion")


class InstallerStateError(Exception):
    """An operator-correctable refusal to read or lock installer state."""


class StepActions(Protocol):
    """The four operations reconciliation needs from an installation target."""

    @property
    def step_names(self) -> tuple[str, ...]: ...

    def missing_inputs(self, step: str) -> tuple[str, ...]: ...

    def apply(self, step: str) -> None: ...

    def verify(self, step: str) -> bool: ...


@dataclass(frozen=True)
class StepOutcome:
    step: str
    status: str
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstallationState:
    version: int
    target: str
    components: tuple[str, ...]
    outcomes: tuple[StepOutcome, ...]


def _state_path(data_dir: Path) -> Path:
    return data_dir / STATE_NAME


def _malformed_state() -> InstallerStateError:
    return InstallerStateError("Installer state is malformed; inspect or replace the state file.")


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _malformed_state()
    return tuple(value)


def _parse_outcome(value: object) -> StepOutcome:
    if not isinstance(value, dict) or set(value) - {"step", "status", "missing"}:
        raise _malformed_state()
    step, status = value.get("step"), value.get("status")
    if not isinstance(step, str) or status not in STATUSES:
        raise _malformed_state()
    return StepOutcome(step, status, _string_list(value.get("missing", [])))


def _valid_identity(state: InstallationState) -> bool:
    canonical = tuple(item for item in COMPONENTS if item in state.components)
    return (
        type(state.version) is int
        and state.target in TARGETS
        and state.components == canonical
        and bool(state.components)
        and state.components[0] == "server"
    )


def _decode_state(content: str) -> InstallationState:
    value = json.loads(content)
    if not isinstance(value, dict) or set(value) != {
        "version",
        "target",
        "components",
        "outcomes",
    }:
        raise _malformed_state()
    outcomes = value["outcomes"]
    if not isinstance(outcomes, list):
        raise _malformed_state()
    state = InstallationState(
        version=value["version"],
        target=value["target"],
        components=_string_list(value["components"]),
        outcomes=tuple(_parse_outcome(item) for item in outcomes),
    )
    if not _valid_identity(state):
        raise _malformed_state()
    return state


def _selection_matches(state: InstallationState, target: str, components: tuple[str, ...]) -> bool:
    return (
        state.version == STATE_VERSION and state.target == target and state.components == components
    )


def read_state(data_dir: Path, target: str, components: tuple[str, ...]) -> InstallationState:
    path = _state_path(data_dir)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise InstallerStateError(
            f"There is no installation recorded in {data_dir}; run `run` first."
        ) from None
    except OSError:
        raise InstallerStateError(f"Cannot read {path}; Fix access and retry.") from None
    try:
        state = _decode_state(content)
    except json.JSONDecodeError, KeyError, TypeError, ValueError, InstallerStateError:
        raise _malformed_state() from None
    if state.version != STATE_VERSION:
        raise InstallerStateError(
            f"Installer state has unsupported version {state.version}; use a compatible installer."
        )
    if not _selection_matches(state, target, components):
        raise InstallerStateError(
            "Installer state belongs to another selection; use a new --data-dir."
        )
    return state


def _state_record(state: InstallationState) -> dict[str, object]:
    outcomes = []
    for outcome in state.outcomes:
        record: dict[str, object] = {"step": outcome.step, "status": outcome.status}
        if outcome.missing:
            record["missing"] = list(outcome.missing)
        outcomes.append(record)
    return {
        "version": state.version,
        "target": state.target,
        "components": list(state.components),
        "outcomes": outcomes,
    }


def write_state(data_dir: Path, state: InstallationState) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".installer-state.", suffix=".tmp", dir=data_dir
        )
    except OSError:
        raise InstallerStateError(
            f"Cannot write state in {data_dir}; fix access and retry."
        ) from None
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(_state_record(state), handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, _state_path(data_dir))
        directory = os.open(data_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)


@contextmanager
def state_lock(data_dir: Path) -> Iterator[None]:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        handle = (data_dir / LOCK_NAME).open("a+b")
    except OSError:
        raise InstallerStateError(
            f"Cannot lock state in {data_dir}; fix access and retry."
        ) from None
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallerStateError(
                "Another installer is already running for this data directory."
            ) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _initial_state(target: str, components: tuple[str, ...]) -> InstallationState:
    return InstallationState(STATE_VERSION, target, components, ())


def _load_or_create(data_dir: Path, target: str, components: tuple[str, ...]) -> InstallationState:
    if _state_path(data_dir).exists():
        return read_state(data_dir, target, components)
    state = _initial_state(target, components)
    write_state(data_dir, state)
    return state


def _verified_prefix(state: InstallationState, actions: StepActions) -> tuple[StepOutcome, ...]:
    verified = []
    for outcome in state.outcomes:
        if outcome.status != "completed" or not actions.verify(outcome.step):
            break
        verified.append(outcome)
    return tuple(verified)


def _valid_outcome(outcome: StepOutcome, index: int, last: int) -> bool:
    terminal = outcome.status in ("blocked", "failed")
    missing_matches_status = (outcome.status == "blocked") == bool(outcome.missing)
    return missing_matches_status and (not terminal or index == last)


def validate_outcomes(state: InstallationState, step_names: tuple[str, ...]) -> None:
    names = tuple(outcome.step for outcome in state.outcomes)
    if len(set(step_names)) != len(step_names) or names != step_names[: len(names)]:
        raise _malformed_state()
    last = len(state.outcomes) - 1
    for index, outcome in enumerate(state.outcomes):
        if not _valid_outcome(outcome, index, last):
            raise _malformed_state()


def reconcile(
    data_dir: Path,
    target: str,
    components: tuple[str, ...],
    actions: StepActions,
    *,
    restart: bool = False,
) -> InstallationState:
    with state_lock(data_dir):
        state = _load_or_create(data_dir, target, components)
        validate_outcomes(state, actions.step_names)
        outcomes = () if restart else _verified_prefix(state, actions)
        state = replace(state, outcomes=outcomes)
        write_state(data_dir, state)
        for step in actions.step_names[len(outcomes) :]:
            missing = actions.missing_inputs(step)
            if missing:
                state = replace(
                    state, outcomes=(*state.outcomes, StepOutcome(step, "blocked", missing))
                )
                write_state(data_dir, state)
                return state
            actions.apply(step)
            if not actions.verify(step):
                state = replace(state, outcomes=(*state.outcomes, StepOutcome(step, "failed")))
                write_state(data_dir, state)
                return state
            state = replace(state, outcomes=(*state.outcomes, StepOutcome(step, "completed")))
            write_state(data_dir, state)
        return state


def reverify(state: InstallationState, actions: StepActions) -> InstallationState:
    outcomes = _verified_prefix(state, actions)
    if len(outcomes) < len(state.outcomes):
        stale = state.outcomes[len(outcomes)]
        missing = actions.missing_inputs(stale.step)
        if missing:
            outcomes = (*outcomes, StepOutcome(stale.step, "blocked", missing))
        elif stale.status == "failed":
            outcomes = (*outcomes, stale)
    return replace(state, outcomes=outcomes)


def _next_action(
    blocked: list[dict[str, object]], failed: list[str], pending: list[str]
) -> str | None:
    if failed:
        return f"Retry failed step {failed[0]}."
    if blocked:
        missing = blocked[0]["missing"]
        if isinstance(missing, list):
            return "Resolve the failed prerequisites: " + "; ".join(missing)
    if pending:
        return f"Run {pending[0]}."
    return None


def _steps_with(state: InstallationState, status: str) -> list[str]:
    return [outcome.step for outcome in state.outcomes if outcome.status == status]


def _blocked_steps(state: InstallationState) -> list[dict[str, object]]:
    return [
        {"step": outcome.step, "missing": list(outcome.missing)}
        for outcome in state.outcomes
        if outcome.status == "blocked"
    ]


def current_status(state: InstallationState, actions: StepActions) -> dict[str, object]:
    completed = _steps_with(state, "completed")
    failed = _steps_with(state, "failed")
    blocked = _blocked_steps(state)
    recorded = {outcome.step for outcome in state.outcomes}
    pending = [step for step in actions.step_names if step not in recorded]
    return {
        "components": list(state.components),
        "completed": completed,
        "failed": failed,
        "blocked": blocked,
        "pending": pending,
        "nextAction": _next_action(blocked, failed, pending),
    }


def project_status(state: InstallationState, actions: StepActions) -> dict[str, object]:
    validate_outcomes(state, actions.step_names)
    return current_status(state, actions)
