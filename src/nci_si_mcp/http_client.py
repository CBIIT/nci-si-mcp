"""The one HTTP client for the upstream platforms (docs/implementation-plan.md section 3.4).

Every request carries `Accept: application/json` and the correlation identifier of the call in
progress (M7.1). A 5xx, a 429 and a connection failure are retried with jittered backoff, a 429
after the wait the platform names; no other 4xx is retried. Every attempt is counted, reported in
the error and handed to the request-log hook. A response is classified before it is returned: a
failure masked as a success is an `upstream_unavailable` error (`upstream.parse_upstream_json`).
A credential is sent only to the platform the client was made for, and appears in no log record,
hook record, error message or error detail (A7.5).
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from http.client import HTTPException, HTTPMessage, IncompleteRead
from typing import IO, Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .bounds import current_budget
from .errors import PlatformError, current_correlation_id
from .upstream import parse_upstream_json

logger = logging.getLogger(__name__)

# Longest wait before a retry, whatever the backoff setting and attempt number. A platform that
# asks for a longer wait is not asked again by this call.
MAX_RETRY_DELAY_SECONDS = 60.0
# The header of the correlation identifier until a platform defines its own (A6.2a).
CORRELATION_HEADER = "X-Correlation-ID"
# A backoff is waited between this share of its length and all of it.
MIN_BACKOFF_SHARE = 0.5


class UpstreamError(RuntimeError):
    """Base error of a request that failed; `details` is what the error record carries."""

    def __init__(self, message: str, /, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class UpstreamUnavailableError(UpstreamError):
    """The platform could not be reached, or kept failing after the bounded retries."""


class UpstreamTimeoutError(UpstreamUnavailableError):
    """The platform did not answer within the timeout on any attempt."""


class UpstreamRejectedError(UpstreamError):
    """The platform refused the request with a status that a retry cannot change."""


class UpstreamTooLargeError(UpstreamError):
    """The platform answered with more bytes than the configured response limit."""


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """What the request-log hook receives for each attempt (never a credential).

    `url` has no query, which could hold caller text. `status` is the HTTP status where there
    was one, and `failure` names what went wrong (`http_status`, `timeout`, `connection`,
    `incomplete_body`, `too_large`, `unusable_response`), or is None for a usable answer.
    """

    surface: str
    method: str
    url: str
    status: int | None
    failure: str | None
    attempt: int
    elapsed_seconds: float
    correlation_id: str | None


class _Transient(Exception):  # noqa: N818 - control flow inside this module, never escapes
    """A failure that a retry can change, with what the loop needs to decide on it."""

    def __init__(
        self,
        message: str,
        *,
        timed_out: bool = False,
        status: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.timed_out = timed_out
        self.status = status
        self.retry_after = retry_after

    def http_details(self) -> dict[str, Any]:
        if self.status is None:
            return {}
        details: dict[str, Any] = {"status": self.status}
        if self.retry_after:
            details["retryAfter"] = self.retry_after
        return details


@dataclass(slots=True)
class _Attempt:
    """One request in flight: its number, and what it turned out to be, for the record."""

    number: int
    status: int | None = None
    failure: str | None = None


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc.lower()


class _SameOriginRedirects(HTTPRedirectHandler):
    """Follow a redirect only within the origin that was asked.

    urllib copies a request's headers, credentials included, to the redirect target.
    """

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        if _origin(newurl) != _origin(req.full_url):
            raise HTTPError(req.full_url, code, "redirect to another origin refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(_SameOriginRedirects)


def _open(request: Request, timeout: float) -> Any:
    # The request is built by `HttpClient`, from an http or https base URL.
    return _OPENER.open(request, timeout=timeout)


def _error_detail(exc: HTTPError) -> str:
    """The reason the platform gives in the body of an error response, if it gives one."""

    try:
        body = json.loads(exc.read(4096).decode("utf-8"))
    except OSError, ValueError, HTTPException:
        return ""
    return str(body.get("message") or "") if isinstance(body, dict) else ""


def _retry_after_seconds(value: str | None) -> float | None:
    """The wait a Retry-After header names, in its seconds or HTTP-date form, or None."""

    value = (value or "").strip()
    if value.isdecimal():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _timed_out(failure: Exception) -> bool:
    return isinstance(failure, TimeoutError) or isinstance(
        getattr(failure, "reason", None), TimeoutError
    )


def _transport_failure(exc: Exception) -> str:
    if _timed_out(exc):
        return "timeout"
    return "incomplete_body" if isinstance(exc, IncompleteRead) else "connection"


def _declared_length(response: Any) -> int:
    """The Content-Length of a response, or 0 when none applies."""

    # http.client ignores Content-Length for a chunked body, and so does this.
    if response.headers.get("Transfer-Encoding", "").lower() == "chunked":
        return 0
    try:
        return int(response.headers.get("Content-Length") or 0)
    except ValueError:
        return 0


class HttpClient:
    """GET JSON documents from one platform under its base URL.

    `credentials` are headers sent on every request of this client and to no other host: a
    redirect to another origin is refused. `on_request`, `sleep` and `jitter` are public so
    that the caller can set the request-log hook and a test can replace the clock and the
    random source. A hook that raises is logged by its exception type and otherwise ignored:
    it never changes the result of a call, nor the error of a failing one.
    """

    def __init__(
        self,
        base_url: str,
        *,
        label: str,
        timeout_seconds: float,
        max_attempts: int,
        retry_backoff_seconds: float,
        max_response_bytes: int,
        size_bound: str,
        credentials: Mapping[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.label = label
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.max_response_bytes = max_response_bytes
        self.size_bound = size_bound
        self.credentials = dict(credentials or {})
        self.on_request: Callable[[RequestRecord], None] | None = None
        self.sleep: Callable[[float], None] = time.sleep
        self.jitter: Callable[[float, float], float] = random.uniform

    @property
    def surface(self) -> str:
        return self.label.lower()

    def _redact(self, text: str) -> str:
        """`text` without any credential, which a platform can echo in an error message."""

        for secret in self.credentials.values():
            text = text.replace(secret, "[redacted]")
        return text

    def _request(self, path: str, params: dict[str, Any] | None) -> Request:
        query = ""
        if params:
            filtered = {key: value for key, value in params.items() if value is not None}
            query = "?" + urlencode(filtered, doseq=True) if filtered else ""
        headers = {"Accept": "application/json", **self.credentials}
        if correlation_id := current_correlation_id():
            headers[CORRELATION_HEADER] = correlation_id
        # The base URL comes from Settings, which accepts only http and https.
        return Request(f"{self.base_url}{path}{query}", headers=headers)  # noqa: S310

    def _record(self, request: Request, attempt: _Attempt, elapsed: float) -> None:
        record = RequestRecord(
            surface=self.surface,
            method=request.get_method(),
            url=request.full_url.partition("?")[0],
            status=attempt.status,
            failure=attempt.failure,
            attempt=attempt.number,
            elapsed_seconds=elapsed,
            correlation_id=current_correlation_id(),
        )
        logger.debug(
            "upstream_request surface=%s method=%s url=%s status=%s failure=%s attempt=%s "
            "elapsed_seconds=%.3f correlation_id=%s",
            *(getattr(record, name) for name in record.__slots__),
        )
        hook = self.on_request
        if hook:
            self._report(hook, record)

    @staticmethod
    def _report(hook: Callable[[RequestRecord], None], record: RequestRecord) -> None:
        """Hand a record to the hook. The hook is observability: it never breaks a call."""

        try:
            hook(record)
        except Exception as exc:  # noqa: BLE001 - whatever a hook raises must not reach the call
            # The type only: the message of a hook's failure may hold anything.
            logger.warning("upstream_request_hook_failed error_type=%s", type(exc).__name__)

    def _read(self, response: Any, path: str, attempt: _Attempt) -> Any:
        too_large = (
            f"{self.label} response for {path} exceeded {self.max_response_bytes} bytes "
            f"({self.size_bound})"
        )
        bound = {"bound": self.size_bound, "limit": self.max_response_bytes}
        declared_length = _declared_length(response)
        if declared_length > self.max_response_bytes:
            attempt.failure = "too_large"
            raise UpstreamTooLargeError(too_large, **bound, reached=declared_length)
        payload = response.read(self.max_response_bytes + 1)
        if len(payload) > self.max_response_bytes:
            # Only one byte past the limit is read, so `reached` is a lower bound.
            attempt.failure = "too_large"
            raise UpstreamTooLargeError(too_large, **bound, reached=len(payload))
        if len(payload) < declared_length:
            # http.client returns a body cut short by a dropped connection without raising.
            raise IncompleteRead(payload, declared_length - len(payload))
        return self._parse(payload, path, attempt)

    def _parse(self, payload: bytes, path: str, attempt: _Attempt) -> Any:
        try:
            return parse_upstream_json(payload, f"{self.label} {path}")
        except PlatformError as exc:
            attempt.failure = "unusable_response"
            details = {**exc.details, "attempts": attempt.number}
            redacted = PlatformError(exc.code, self._redact(exc.message), **details)
        # Raised outside the handler, so that no chain holds the message as the platform
        # worded it: a platform can echo a header in its message.
        raise redacted

    def _http_failure(self, exc: HTTPError, path: str, attempt: _Attempt) -> Exception:
        """The failure an HTTP error status stands for: to retry, or to report as it is."""

        detail = _error_detail(exc)
        reason = " ".join(
            part
            for part in (f"HTTP {exc.code}", str(exc.reason or ""), f"({detail})" if detail else "")
            if part
        )
        # Everything here, the status line included, is what the platform said.
        message = self._redact(f"{self.label} request failed for {path}: {reason}")
        retry_after = exc.headers.get("Retry-After")
        exc.close()
        if exc.code == HTTPStatus.TOO_MANY_REQUESTS or exc.code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            return _Transient(message, status=exc.code, retry_after=retry_after)
        return UpstreamRejectedError(
            message, surface=self.surface, status=exc.code, attempts=attempt.number
        )

    def _exchange(self, request: Request, path: str, attempt: _Attempt) -> Any:
        try:
            with _open(request, self.timeout_seconds) as response:
                attempt.status = response.status
                return self._read(response, path, attempt)
        except HTTPError as exc:
            attempt.status, attempt.failure = exc.code, "http_status"
            failure = self._http_failure(exc, path, attempt)
        except (OSError, HTTPException) as exc:
            attempt.failure = _transport_failure(exc)
            reason = getattr(exc, "reason", None) or exc
            failure = _Transient(
                self._redact(f"{self.label} request failed for {path}: {reason}"),
                timed_out=_timed_out(exc),
            )
        # Raised outside the handlers, so that no chain carries the failure as it came: an
        # HTTP status line can hold anything the platform chose to echo.
        raise failure

    def _attempt(self, request: Request, path: str, number: int) -> Any:
        if budget := current_budget():
            budget.request()
        attempt = _Attempt(number)
        started = time.monotonic()
        try:
            return self._exchange(request, path, attempt)
        finally:
            self._record(request, attempt, time.monotonic() - started)

    def _delay(self, attempt: int, failure: _Transient) -> float | None:
        """The wait before the next attempt, or None when the platform asks for too long."""

        backoff = min(self.retry_backoff_seconds * 2 ** (attempt - 1), MAX_RETRY_DELAY_SECONDS)
        backoff *= self.jitter(MIN_BACKOFF_SHARE, 1.0)
        named = _retry_after_seconds(failure.retry_after)
        if named is None:
            return backoff
        return None if named > MAX_RETRY_DELAY_SECONDS else max(named, backoff)

    def _unavailable(
        self, failure: _Transient, attempts: int, timeouts: int, last_http: dict[str, Any]
    ) -> UpstreamUnavailableError:
        """The error for a request that kept failing, with what is known of the failures.

        It is a timeout only when every one of the `attempts` timed out; otherwise the details
        carry those of the last HTTP failure, if there was one.
        """

        if timeouts == attempts:
            return UpstreamTimeoutError(
                failure.message,
                surface=self.surface,
                seconds=self.timeout_seconds,
                attempts=attempts,
            )
        return UpstreamUnavailableError(
            failure.message, surface=self.surface, attempts=attempts, **last_http
        )

    def _wait(self, path: str, attempt: int, delay: float, reason: str) -> None:
        logger.warning(
            "upstream_request_retry surface=%s path=%s attempt=%s max_attempts=%s "
            "delay_seconds=%.3f reason=%s",
            self.surface,
            path,
            attempt,
            self.max_attempts,
            delay,
            reason,
        )
        if delay:
            self.sleep(delay)

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a JSON document, retrying transport failures, HTTP 429 and HTTP 5xx.

        Every attempt is counted: the error reports the number of requests made. Whatever
        the platform answered is classified before it is returned.
        """

        request = self._request(path, params)
        attempts = timeouts = 0
        last_http: dict[str, Any] = {}
        while True:
            attempts += 1
            try:
                return self._attempt(request, path, attempts)
            except _Transient as failure:
                timeouts += failure.timed_out
                last_http = failure.http_details() or last_http
                delay = self._delay(attempts, failure)
                if attempts >= self.max_attempts or delay is None:
                    raise self._unavailable(failure, attempts, timeouts, last_http) from failure
                self._wait(path, attempts, delay, failure.message)
