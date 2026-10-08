"""Bounded client-observed MCP HTTP measurements; remote probes are explicitly opt-in."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from scripts.benchmark import CASES
from scripts.benchmark_limits import Budget, Limits, RunStoppedError, approved_target
from scripts.benchmark_transport import http_client
from scripts.http_measurement import MeasurementError, failure_sample, measure_reply, summarize_http


def _save(output: Path, report: dict[str, Any]) -> None:
    if output.is_symlink():
        raise ValueError("Benchmark output cannot be a symlink")
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(report, indent=2) + "\n")
            stream.close()
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)


def _validate_cases(cases: list[dict[str, Any]]) -> None:
    for case in cases:
        if set(case) != {"tool", "arguments", "scenario"} or not isinstance(
            case["arguments"], dict
        ):
            raise ValueError("Invalid benchmark case")
        if case["scenario"] is not None and not isinstance(case["scenario"], str):
            raise ValueError("Invalid benchmark scenario")
    _case_names([case["tool"] for case in cases])


def _case_names(names: list[str]) -> None:
    if not names or len(set(names)) != len(names) or not set(names) <= set(CASES):
        raise ValueError("Choose distinct representative benchmark tools")


def _reason(error: BaseException) -> str | None:
    if isinstance(error, BaseExceptionGroup):
        return _group_reason(error)
    if isinstance(error, RunStoppedError):
        return str(error)
    if isinstance(error, (TimeoutError, httpx2.TimeoutException)):
        return "timeout"
    if isinstance(error, MeasurementError):
        return "invalid_response"
    if isinstance(error, httpx2.HTTPError):
        return "transport_error"
    return None


def _group_reason(error: BaseExceptionGroup) -> str | None:
    reasons = []
    for item in error.exceptions:
        if isinstance(item, asyncio.CancelledError):
            continue
        reason = _reason(item)
        if reason is None:
            return None
        reasons.append(reason)
    return reasons[0] if reasons else None


class _Campaign:
    def __init__(
        self,
        target: str,
        cases: list[dict[str, Any]],
        budget: Budget,
        output: Path,
        fixture: bool,
        authorization: str | None,
    ) -> None:
        self.target, self.budget, self.output, self.authorization = (
            target,
            budget,
            output,
            authorization,
        )
        self.report: dict[str, Any] = {
            "schema": 1,
            "transport": "streamable-http",
            "mode": "fixture" if fixture else "live",
            "createdAt": datetime.now(UTC).isoformat(),
            "state": "running",
            "complete": False,
            "stopReason": None,
            "httpRequests": 0,
            "limits": asdict(budget.limits),
            "targetSha256": hashlib.sha256(target.encode()).hexdigest(),
            "expectedCases": [case["tool"] for case in cases],
            "conditions": {
                "cold": "first measured call in a new client session; not a cold server",
                "warm": "same client session after the recorded warm-up calls",
                "timing": "tool-call duration; setup excluded from latency, included in budget",
                "concurrency": 1,
                "remoteTermination": "not established by client cancellation",
                "serverTelemetry": "unknown; no audit-fetch endpoint or trusted telemetry source",
            },
            "cases": [
                case
                | {
                    "cold": {"samples": [], "summary": summarize_http([])},
                    "warm": {"samples": [], "summary": summarize_http([])},
                    "warmups": {"samples": [], "summary": summarize_http([])},
                }
                for case in cases
            ],
        }

    def save(self) -> None:
        self.report["httpRequests"] = self.budget.requests
        _save(self.output, self.report)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Client]:
        self.budget.remaining()
        async with (
            http_client(self.target, self.budget, authorization=self.authorization) as http,
            Client(
                streamable_http_client(self.target, http_client=http),
                cache=None,
                read_timeout_seconds=self.budget.limits.timeout,
            ) as client,
        ):
            yield client

    async def measure(
        self, client: Client, case: dict[str, Any]
    ) -> tuple[dict[str, Any], Exception | None]:
        correlation = uuid.uuid4().hex
        started, requests = time.perf_counter(), self.budget.requests
        seconds = min(self.budget.remaining(), self.budget.limits.timeout)
        try:
            async with asyncio.timeout(seconds):
                reply = await client.call_tool(
                    case["tool"], case["arguments"], meta={"correlationId": correlation}
                )
            return measure_reply(
                reply,
                (time.perf_counter() - started) * 1000,
                correlation,
                http_requests=self.budget.requests - requests,
            ), None
        except Exception as error:
            reason = _reason(error)
            if reason is None:
                raise
            sample = failure_sample(
                reason,
                (time.perf_counter() - started) * 1000,
                correlation,
                http_requests=self.budget.requests - requests,
            )
            return sample, error

    async def phase(self, client: Client, case: dict[str, Any], phase: str) -> None:
        sample, error = await self.measure(client, case)
        case[phase]["samples"].append(sample)
        case[phase]["summary"] = summarize_http(case[phase]["samples"])
        self.save()
        if error is not None:
            raise error

    async def execute(self) -> None:
        for case in self.report["cases"]:
            for _ in range(self.budget.limits.repetitions):
                async with self.session() as client:
                    await self.phase(client, case, "cold")
            async with self.session() as client:
                for _ in range(self.budget.limits.warmups):
                    await self.phase(client, case, "warmups")
                for _ in range(self.budget.limits.repetitions):
                    await self.phase(client, case, "warm")


async def _watch(task: asyncio.Task[None], budget: Budget, stopped: list[str]) -> None:
    while not task.done():
        try:
            budget.remaining()
        except RunStoppedError as error:
            stopped.append(str(error))
            task.cancel()
            return
        await asyncio.sleep(0.05)


async def _run(campaign: _Campaign) -> dict[str, Any]:
    campaign.save()
    stopped: list[str] = []
    task = asyncio.create_task(campaign.execute())
    watcher = asyncio.create_task(_watch(task, campaign.budget, stopped))
    try:
        await task
        campaign.budget.remaining()
        campaign.report.update(state="completed", complete=True)
    except asyncio.CancelledError:
        reason = stopped[0] if stopped else "cancelled"
        _stop_report(campaign, reason)
        if not stopped:
            raise
    except Exception as error:  # noqa: BLE001 - preserve failed evidence; unknown bugs propagate
        _failure(campaign, error)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        campaign.save()
    return campaign.report


def _stop_report(campaign: _Campaign, reason: str) -> None:
    campaign.report.update(
        state="cancelled" if reason == "cancelled" else "failed", stopReason=reason
    )


def _failure(campaign: _Campaign, error: Exception) -> None:
    reason = _reason(error)
    _stop_report(campaign, reason or "runner_error")
    if reason is None:
        raise error


async def run_http(
    target: str,
    allowed: Sequence[str],
    cases: list[dict[str, Any]],
    limits: Limits,
    output: Path,
    *,
    fixture: bool = False,
    allow_remote: bool = False,
    authorization: str | None = None,
    cancelled: threading.Event | None = None,
) -> dict[str, Any]:
    if not fixture and not allow_remote:
        raise ValueError("Remote read-only probes require explicit operator opt-in")
    if authorization is not None and not (authorization.isascii() and authorization.isprintable()):
        raise ValueError("Authorization must be a printable ASCII header value")
    target = approved_target(target, allowed, fixture=fixture)
    _validate_cases(cases)
    campaign = _Campaign(
        target, cases, Budget(limits, cancelled=cancelled), output, fixture, authorization
    )
    return await _run(campaign)
