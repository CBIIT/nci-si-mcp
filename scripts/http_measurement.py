"""Client-observed HTTP measurements; server telemetry is never inferred from elapsed time."""

from __future__ import annotations

import json
from typing import Any, get_args

from mcp import types
from scripts.benchmark import percentile

from nci_si_mcp.errors import ErrorCode


class MeasurementError(ValueError):
    """A reply cannot support a trustworthy client-observed measurement."""


def _response_code(content: dict[str, Any], is_error: bool, correlation: str) -> str:
    if is_error != ("error" in content):
        raise MeasurementError("Protocol and structured result disagree")
    if not is_error:
        return "ok"
    error = content["error"]
    if not isinstance(error, dict) or error.get("code") not in get_args(ErrorCode):
        raise MeasurementError("Invalid structured error code")
    if error.get("correlationId") != correlation:
        raise MeasurementError("Error correlation does not match the measured call")
    return error["code"]


def measure_reply(
    reply: types.CallToolResult,
    elapsed_ms: float,
    correlation: str,
    *,
    http_requests: int,
) -> dict[str, Any]:
    content = reply.structured_content
    if not isinstance(content, dict):
        raise MeasurementError("Measured reply has no structured object")
    response_code = _response_code(content, reply.is_error, correlation)
    if "provenance" in content:
        provenance = content["provenance"]
        if not isinstance(provenance, dict) or provenance.get("correlationId") != correlation:
            raise MeasurementError("Provenance correlation does not match the measured call")
    size = len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode())
    return _sample(elapsed_ms, correlation, http_requests, size, response_code, reply.is_error)


def _sample(
    elapsed_ms: float,
    correlation: str,
    http_requests: int,
    size: int | None,
    response_code: str,
    error: bool,
) -> dict[str, Any]:
    return {
        "elapsedMs": elapsed_ms,
        "resultBytes": size,
        "httpRequests": http_requests,
        "responseCode": response_code,
        "error": error,
        "correlationId": correlation,
        "outboundRequests": None,
        "cacheState": None,
        "serverCommit": None,
        "replica": None,
    }


def failure_sample(
    reason: str, elapsed_ms: float, correlation: str, *, http_requests: int
) -> dict[str, Any]:
    return _sample(elapsed_ms, correlation, http_requests, None, reason, True)


def summarize_http(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {
            "calls": 0,
            "p50Ms": None,
            "p95Ms": None,
            "errorRate": None,
            "meanResultBytes": None,
            "meanOutboundRequests": None,
        }
    times = [row["elapsedMs"] for row in samples]
    return {
        "calls": len(samples),
        "p50Ms": percentile(times, 0.5),
        "p95Ms": percentile(times, 0.95),
        "errorRate": sum(row["error"] for row in samples) / len(samples),
        "meanResultBytes": _mean_size(samples),
        "meanOutboundRequests": None,
    }


def _mean_size(samples: list[dict[str, Any]]) -> float | None:
    sizes = [row["resultBytes"] for row in samples]
    if any(size is None for size in sizes):
        return None
    return sum(sizes) / len(sizes)
