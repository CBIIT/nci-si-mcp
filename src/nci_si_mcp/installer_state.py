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


def _invalid_state() -> ValueError:
    return ValueError(
        "Installer state is invalid or belongs to another selection; use a new --data-dir."
    )


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _invalid_state()
    return tuple(value)


def _parse_outcome(value: object) -> StepOutcome:
    if not isinstance(value, dict) or set(value) - {"step", "status", "missing"}:
        raise _invalid_state()
    step, status = value.get("step"), value.get("status")
    if not isinstance(step, str) or status not in STATUSES:
        raise _invalid_state()
    return StepOutcome(step, status, _string_list(value.get("missing", [])))


def _decode_state(content: str) -> InstallationState:
    value = json.loads(content)
    if not isinstance(value, dict) or set(value) != {
        "version",
        "target",
        "components",
        "outcomes",
    }:
        raise _invalid_state()
    outcomes = value["outcomes"]
    if not isinstance(outcomes, list):
        raise _invalid_state()
    return InstallationState(
        version=value["version"],
        target=value["target"],
        components=_string_list(value["components"]),
        outcomes=tuple(_parse_outcome(item) for item in outcomes),
    )


def _selection_matches(state: InstallationState, target: str, components: tuple[str, ...]) -> bool:
    return (
        state.version == STATE_VERSION and state.target == target and state.components == components
    )


def read_state(data_dir: Path, target: str, components: tuple[str, ...]) -> InstallationState:
    try:
        state = _decode_state(_state_path(data_dir).read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, KeyError, TypeError, ValueError:
        raise _invalid_state() from None
    if not _selection_matches(state, target, components):
        raise _invalid_state()
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
    data_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".installer-state.", suffix=".tmp", dir=data_dir
    )
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
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / LOCK_NAME).open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
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
        if stale.status == "blocked":
            missing = actions.missing_inputs(stale.step)
            if missing:
                outcomes = (*outcomes, StepOutcome(stale.step, "blocked", missing))
    return replace(state, outcomes=outcomes)


def _next_action(blocked: list[dict[str, object]], pending: list[str]) -> str | None:
    if blocked:
        step = blocked[0]["step"]
        missing = blocked[0]["missing"]
        if isinstance(missing, list) and len(missing) == 1:
            return f"Provide {missing[0]} for {step}."
        return f"Resolve the missing prerequisites for {step}."
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


def project_status(state: InstallationState, actions: StepActions) -> dict[str, object]:
    current = reverify(state, actions)
    completed = _steps_with(current, "completed")
    blocked = _blocked_steps(current)
    recorded = {outcome.step for outcome in current.outcomes}
    pending = [step for step in actions.step_names if step not in recorded]
    return {
        "components": list(current.components),
        "completed": completed,
        "blocked": blocked,
        "pending": pending,
        "nextAction": _next_action(blocked, pending),
    }
