"""HTTP transport guards shared by all requests of an operator benchmark campaign."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import httpx2
from scripts.benchmark_limits import Budget, RunStoppedError

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class _BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream, maximum: int) -> None:
        self.stream, self.maximum = stream, maximum

    async def __aiter__(self) -> AsyncIterator[bytes]:
        remaining = self.maximum
        async for chunk in self.stream:
            remaining -= len(chunk)
            if remaining < 0:
                raise RunStoppedError("response_limit")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class _GuardedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, target: str, budget: Budget, maximum: int) -> None:
        self.target, self.budget, self.maximum = target, budget, maximum
        self.inner = httpx2.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if str(request.url) != self.target:
            raise RunStoppedError("target")
        self.budget.admit()
        response = await self.inner.handle_async_request(request)
        try:
            _response_headers(response, self.maximum)
        except RunStoppedError:
            await response.aclose()
            raise
        response.stream = _BoundedStream(
            cast("httpx2.AsyncByteStream", response.stream), self.maximum
        )
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


def _response_headers(response: httpx2.Response, maximum: int) -> None:
    if response.is_redirect:
        raise RunStoppedError("redirect")
    if response.is_error:
        raise RunStoppedError(f"http_{response.status_code}")
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        # Count actual bytes without permitting a compressed response to expand without a bound.
        raise RunStoppedError("content_encoding")
    length = response.headers.get("content-length")
    if length is not None and (not length.isdecimal() or int(length) > maximum):
        raise RunStoppedError("response_limit")


def http_client(
    target: str,
    budget: Budget,
    *,
    authorization: str | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> httpx2.AsyncClient:
    """Create a client for an already approved target; no proxy, redirect or implicit retry."""
    headers = {"Accept-Encoding": "identity"}
    if authorization is not None:
        headers["Authorization"] = authorization
    return httpx2.AsyncClient(
        headers=headers,
        transport=_GuardedTransport(target, budget, max_bytes),
        timeout=budget.limits.timeout,
        follow_redirects=False,
        trust_env=False,
    )
