"""Benchmark records shared by measurement writers and evidence readers."""

# Wire names are dataclass fields so producers and readers cannot declare different names.
# ruff: noqa: N815

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal, get_args

from scripts.evidence_envelope import EvidenceError
from scripts.vocabulary import HttpStop, RunStop, is_run_stop

from nci_si_mcp.errors import ErrorCode

Transport = Literal["stdio", "streamable-http"]
MAX_MEASUREMENT = 2**63 - 1


def require(condition: bool, path: str) -> None:
    if not condition:
        raise EvidenceError(f"Invalid benchmark field: {path}")


def wire_fields(record: type, transport: Transport) -> set[str]:
    """Field declarations own transport applicability as well as JSON names."""
    return {
        item.name
        for item in fields(record)
        if item.metadata.get("transport", transport) == transport
    }


def shape(value: Any, expected: set[str], path: str) -> None:
    require(isinstance(value, dict), path)
    for name in sorted(expected):
        require(name in value, f"{path}.{name}")
    # An unknown key can itself contain private text; report its owning record instead.
    require(set(value) == expected, path)


def number(value: Any, path: str, *, integer: bool = False, maximum: int = MAX_MEASUREMENT) -> None:
    kinds = (int,) if integer else (int, float)
    require(type(value) in kinds and 0 <= value <= maximum, path)


@dataclass(frozen=True)
class Sample:
    elapsedMs: float
    resultBytes: int | None
    responseCode: Literal["ok"] | ErrorCode | RunStop | HttpStop
    error: bool
    outboundRequests: int | None = None
    release: dict[str, Any] | None = field(default=None, metadata={"transport": "stdio"})
    httpRequests: int | None = field(default=None, metadata={"transport": "streamable-http"})
    correlationId: str | None = field(default=None, metadata={"transport": "streamable-http"})
    cacheState: None = field(default=None, metadata={"transport": "streamable-http"})
    serverCommit: None = field(default=None, metadata={"transport": "streamable-http"})
    replica: None = field(default=None, metadata={"transport": "streamable-http"})

    def to_dict(self, transport: Transport) -> dict[str, Any]:
        names = wire_fields(type(self), transport)
        return {key: value for key, value in asdict(self).items() if key in names}

    @classmethod
    def from_dict(
        cls, value: Any, transport: Transport, path: str, *, max_requests: int = MAX_MEASUREMENT
    ) -> Sample:
        shape(value, wire_fields(cls, transport), path)
        number(value["elapsedMs"], f"{path}.elapsedMs")
        code = value["responseCode"]
        allowed = code in ("ok", *get_args(ErrorCode))
        require(
            allowed or (transport == "streamable-http" and is_run_stop(code)),
            f"{path}.responseCode",
        )
        require(type(value["error"]) is bool and value["error"] == (code != "ok"), f"{path}.error")
        _result_size(value, transport, path)
        if transport == "stdio":
            number(value["outboundRequests"], f"{path}.outboundRequests", integer=True)
            require(isinstance(value["release"], dict), f"{path}.release")
        else:
            _http_sample(value, path, max_requests)
        return cls(**value)


def _result_size(value: dict[str, Any], transport: Transport, path: str) -> None:
    if value["resultBytes"] is None:
        require(transport == "streamable-http" and value["error"], f"{path}.resultBytes")
    else:
        number(value["resultBytes"], f"{path}.resultBytes", integer=True)


def _http_sample(value: dict[str, Any], path: str, max_requests: int) -> None:
    number(value["httpRequests"], f"{path}.httpRequests", integer=True, maximum=max_requests)
    correlation = value["correlationId"]
    require(
        isinstance(correlation, str) and re.fullmatch(r"[a-f0-9]{32}", correlation) is not None,
        f"{path}.correlationId",
    )
    for name in ("outboundRequests", "cacheState", "serverCommit", "replica"):
        require(value[name] is None, f"{path}.{name}")
