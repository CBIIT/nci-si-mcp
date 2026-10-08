"""Closed operator limits and exact targets for single-concurrency HTTP measurements."""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class RunStoppedError(Exception):
    """A bounded campaign stopped; the reason contains no endpoint or credential."""


def _url_syntax(url: str) -> None:
    if any(character in url for character in ("?", "#", "\\", "%")):
        raise ValueError("Target must have an unambiguous path and no query or fragment")
    if any(character.isspace() or not character.isprintable() for character in url):
        raise ValueError("Target contains unsupported characters")


def _path(path: str) -> str:
    if not path or path == "/":
        return "/"
    if any(segment in {".", "..", ""} for segment in path[1:].split("/")):
        raise ValueError("Target path must be canonical")
    return path


def _normalized(url: str, fixture: bool) -> str:
    _url_syntax(url)
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        raise ValueError("Target must not contain credentials")
    host, port = parts.hostname, parts.port
    _authority(parts.scheme, host, fixture)
    authority = f"[{host}]" if host == "::1" else str(host)
    default_port = 443 if parts.scheme == "https" else 80
    if port is not None and port != default_port:
        authority += f":{port}"
    return urlunsplit((parts.scheme, authority, _path(parts.path), "", ""))


def _authority(scheme: str, host: str | None, fixture: bool) -> None:
    if not host or not re.fullmatch(r"[a-zA-Z0-9.-]+|::1", host):
        raise ValueError("Target needs an explicit supported host")
    if scheme == "https":
        return
    if scheme != "http" or not fixture or host not in {"127.0.0.1", "::1"}:
        raise ValueError("Remote targets require HTTPS; HTTP is only for owned loopback fixtures")


def approved_target(url: str, allowed: Sequence[str], *, fixture: bool = False) -> str:
    target = _normalized(url, fixture)
    if target not in {_normalized(item, fixture) for item in allowed}:
        raise ValueError("Target is not in the exact operator allowlist")
    return target


@dataclass(frozen=True)
class Limits:
    max_requests: int = 200
    seconds: float = 120
    timeout: float = 15
    repetitions: int = 5
    warmups: int = 1

    def __post_init__(self) -> None:
        for value, low, high in (
            (self.max_requests, 1, 1000),
            (self.repetitions, 1, 20),
            (self.warmups, 0, 3),
        ):
            _integer_limit(value, low, high)
        for value, high in ((self.seconds, 1200), (self.timeout, 120)):
            _time_limit(value, high)


def _integer_limit(value: int, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Integer campaign limit is outside the supported range")


def _time_limit(value: float, high: int) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= high:
        raise ValueError("Time limit must be finite, positive and bounded")


class Budget:
    def __init__(
        self,
        limits: Limits,
        *,
        cancelled: threading.Event | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits, self.clock = limits, clock
        self.cancelled = cancelled if cancelled is not None else threading.Event()
        self.deadline = clock() + limits.seconds
        self.requests = 0

    def remaining(self) -> float:
        if self.cancelled.is_set():
            raise RunStoppedError("cancelled")
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise RunStoppedError("deadline")
        return remaining

    def admit(self) -> None:
        self.remaining()
        if self.requests >= self.limits.max_requests:
            raise RunStoppedError("request_budget")
        self.requests += 1
