"""The closed value sets of validation runs and jobs, once here for every raiser and validator."""

from __future__ import annotations

import re
from typing import Literal, NewType, TypedDict, get_args

# Why a measurement run stopped. An HTTP error status stop is an `HttpStop`, `http_4NN` or
# `http_5NN`, built by `benchmark_limits.http_stop`.
RunStop = Literal[
    "cancelled",
    "deadline",
    "request_budget",
    "response_limit",
    "target",
    "redirect",
    "content_encoding",
    "timeout",
    "invalid_response",
    "transport_error",
    "runner_error",
]
RUN_STOPS = frozenset(get_args(RunStop))
HttpStop = NewType("HttpStop", str)
_HTTP_STOP = re.compile(r"http_[45][0-9]{2}")

RunState = Literal["running", "completed", "failed", "cancelled"]
RUN_STATES = frozenset(get_args(RunState))

# Why a job ended other than in success, recorded by the worker controller.
JobReason = Literal[
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
]
JOB_REASONS = frozenset(get_args(JobReason))

ActiveJobState = Literal["pending", "running"]
TerminalJobState = Literal["completed", "failed", "cancelled", "interrupted", "unavailable"]
ACTIVE = frozenset(get_args(ActiveJobState))
TERMINAL = frozenset(get_args(TerminalJobState))


class JobOutcome(TypedDict):
    """How a job ended: the fields the worker controller records on its row."""

    state: TerminalJobState
    exit_code: int | None
    reason: JobReason | None


def is_run_stop(value: object) -> bool:
    return isinstance(value, str) and (
        value in RUN_STOPS or _HTTP_STOP.fullmatch(value) is not None
    )


def run_stop(reason: RunStop | HttpStop) -> RunStop | HttpStop:
    """The reason itself; a reason outside the vocabulary is a bug of its raiser."""
    if not is_run_stop(reason):
        raise ValueError("Unknown run stop reason")
    return reason
