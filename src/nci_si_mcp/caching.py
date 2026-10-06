"""Cache hints for the content and status results the server currently emits."""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

LONG_TTL_MS = 86_400_000
SHORT_TTL_MS = 3_600_000
_decision: ContextVar[dict[str, int | str]] = ContextVar("cache_decision")


def cache_hint(
    *,
    resolution: bool = False,
    error: bool = False,
    unpinned: bool = False,
    computed: bool = False,
    implicit: bool = False,
) -> dict[str, int | str]:
    """Select freshness and sharing for the producer's response class."""

    private = any((error, computed, implicit))
    return {
        "ttlMs": 0 if resolution or private else SHORT_TTL_MS if unpinned else LONG_TTL_MS,
        "cacheScope": "private" if private else "public",
    }


@contextmanager
def cache_call() -> Iterator[dict[str, int | str]]:
    # A distinct mutable carrier lets SDK worker threads return the declared policy
    # to the calling task; ContextVar assignments in a worker would not propagate.
    decision: dict[str, int | str] = {}
    token = _decision.set(decision)
    try:
        yield decision
    finally:
        _decision.reset(token)


def select_cache_hint(
    *,
    resolution: bool,
    unpinned: bool = False,
    computed: bool = False,
    implicit: bool = False,
) -> None:
    """Declare the current response's class at its producer, never from serialized content."""

    _decision.get().update(
        cache_hint(
            resolution=resolution,
            unpinned=unpinned,
            computed=computed,
            implicit=implicit,
        )
    )


@contextmanager
def invocation_policy(*, resolution: bool) -> Iterator[None]:
    """Select the registry policy, also for CLI calls with no protocol middleware."""

    with cache_call() if _decision.get(None) is None else nullcontext():
        select_cache_hint(resolution=resolution)
        yield
