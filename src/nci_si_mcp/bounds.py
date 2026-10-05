"""Traversal limits and the request allowance shared by one call (A5)."""

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_NODES = 200
DEFAULT_MAX_EDGES = 1000
HARD_MAX_DEPTH = 4
HARD_MAX_NODES = 1000
HARD_MAX_EDGES = 5000
MAX_TRAVERSAL_REQUESTS = 200
HARD_MAX_PER_KIND = 1000


def clamp_limits(max_depth: int, max_nodes: int) -> tuple[int, int]:
    return min(max(max_depth, 0), HARD_MAX_DEPTH), min(max(max_nodes, 1), HARD_MAX_NODES)


def clamp_edge_limit(max_edges: int) -> int:
    return min(max(max_edges, 1), HARD_MAX_EDGES)


class RequestBudgetError(RuntimeError):
    """Another request would exceed the call's allowance; that request was not sent."""

    def __init__(self, limit: int, reached: int) -> None:
        super().__init__("The traversal exhausted its outbound request allowance")
        self.details = {"bound": "requests", "limit": limit, "reached": reached}


@dataclass
class Budget:
    depth: int = DEFAULT_MAX_DEPTH
    requests: int = MAX_TRAVERSAL_REQUESTS
    nodes: int = DEFAULT_MAX_NODES
    edges: int = DEFAULT_MAX_EDGES
    per_kind: int | None = None
    attempts: int = field(default=0, init=False)
    added_by_kind: Counter[str] = field(default_factory=Counter, init=False)
    exhausted: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.depth, self.nodes = clamp_limits(self.depth, self.nodes)
        self.edges = clamp_edge_limit(self.edges)
        if self.per_kind is not None:
            self.per_kind = min(self.per_kind, HARD_MAX_PER_KIND)

    def request(self) -> None:
        if self.attempts >= self.requests:
            self.exhausted = True
            raise RequestBudgetError(self.requests, self.attempts)
        self.attempts += 1

    def node_bound(self, total: int, kind: str) -> str | None:
        if total >= self.nodes:
            return "nodes"
        if self.per_kind is not None and self.added_by_kind[kind] >= self.per_kind:
            return "kind_budget"
        return None


_budget: ContextVar[Budget | None] = ContextVar("traversal_budget", default=None)


def current_budget() -> Budget | None:
    return _budget.get()


@contextmanager
def budgeted(budget: Budget) -> Iterator[None]:
    token = _budget.set(budget)
    try:
        yield
    finally:
        _budget.reset(token)
