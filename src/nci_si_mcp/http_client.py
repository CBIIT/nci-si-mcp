"""The one HTTP client for the upstream platforms (docs/implementation-plan.md section 3.4).

JSON API requests carry `Accept: application/json`; explicit export text requests ask for HTML.
Both carry the correlation identifier of the call in progress (M7.1). A 5xx, a 429 and a
connection failure are retried with jittered backoff, a 429
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
import re
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

from .audit import emit, hashed, requested
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
    """The platform refused the request with a status that a retry cannot change.

    empty_body is true only when the bounded error-body read succeeded with no bytes.
    """

    empty_body = False


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

    def http_details(self, redact: Callable[[str], str]) -> dict[str, Any]:
        if self.status is None:
            return {}
        details: dict[str, Any] = {"status": self.status}
        if self.retry_after:
            # Keep the raw header for retry timing, but never echo a credential to callers.
            details["retryAfter"] = redact(self.retry_after)
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
        # urllib can turn a redirected POST into a GET and discard the match body.
        if req.get_method() == "POST":
            raise HTTPError(req.full_url, code, "POST redirect refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(_SameOriginRedirects)


def _open(request: Request, timeout: float) -> Any:
    # The request is built by `HttpClient`, from an http or https base URL.
    return _OPENER.open(request, timeout=timeout)


def _error_detail(exc: HTTPError) -> tuple[str, bool]:
    """The reason the platform gives in the body of an error response, if it gives one."""

    try:
        payload = exc.read(4096)
    except OSError, HTTPException:
        return "", False
    try:
        body = json.loads(payload.decode("utf-8"))
    except ValueError:
        return "", not payload
    return (str(body.get("message") or "") if isinstance(body, dict) else ""), False


def _retry_after_seconds(value: str | None) -> float | None:
    """The wait a Retry-After header names, in its seconds or HTTP-date form, or None."""

    value = (value or "").strip()
    if value.isdecimal():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if when.tzinfo is None:
        # HTTP's obsolete asctime form implies UTC (RFC 9110 section 5.6.7).
        # Other zone-less email dates are not HTTP dates.
        asctime = r"[A-Z][a-z]{2} [A-Z][a-z]{2} [ 0-9][0-9] [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}"
        if not re.fullmatch(asctime, value):
            return None
        when = when.replace(tzinfo=UTC)
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
    """Read JSON, post JSON/forms, or read bounded export text under one base URL.

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
        redact_values: tuple[str, ...] = (),
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.label = label
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.max_response_bytes = max_response_bytes
        self.size_bound = size_bound
        self.credentials = dict(credentials or {})
        self.redact_values = redact_values
        self.on_request: Callable[[RequestRecord], None] | None = None
        self.sleep: Callable[[float], None] = time.sleep
        self.jitter: Callable[[float, float], float] = random.uniform

    @property
    def surface(self) -> str:
        return self.label.lower()

    def _redact(self, text: str) -> str:
        """`text` without any credential, which a platform can echo in an error message."""

        for secret in (*self.credentials.values(), *self.redact_values):
            text = text.replace(secret, "[redacted]")
        return text

    def url(self, path: str, params: dict[str, Any] | None = None) -> str:
        """Build the exact encoded URL used by a request, without sending it."""

        query = ""
        if params:
            filtered = {key: value for key, value in params.items() if value is not None}
            query = "?" + urlencode(filtered, doseq=True) if filtered else ""
        return f"{self.base_url}{path}{query}"

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None,
        *,
        headers: Mapping[str, str] | None = None,
        data: bytes | None = None,
    ) -> Request:
        headers = {"Accept": "application/json", **(headers or {}), **self.credentials}
        if correlation_id := current_correlation_id():
            headers[CORRELATION_HEADER] = correlation_id
        # The base URL comes from Settings, which accepts only http and https.
        return Request(self.url(path, params), headers=headers, data=data)  # noqa: S310

    def _record(self, request: Request, attempt: _Attempt, elapsed: float) -> None:
        requested()
        record = RequestRecord(
            surface=self.surface,
            method=request.get_method(),
            url=self._redact(request.full_url.partition("?")[0]),
            status=attempt.status,
            failure=attempt.failure,
            attempt=attempt.number,
            elapsed_seconds=elapsed,
            correlation_id=current_correlation_id(),
        )
        emit(
            logger,
            logging.DEBUG,
            "upstream_request",
            surface=record.surface,
            method=record.method,
            urlHash=hashed(record.url)["sha256"],
            status=record.status,
            failure=record.failure,
            attempt=record.attempt,
            elapsedMs=record.elapsed_seconds * 1000,
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
            emit(
                logger,
                logging.WARNING,
                "upstream_request_hook_failed",
                errorType=type(exc).__name__,
            )

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
        return payload

    def _parse(
        self,
        payload: bytes,
        path: str,
        attempt: _Attempt,
        json_response: bool,
        interpret: Callable[[bytes, int | None], None] | None,
    ) -> Any:
        try:
            if interpret:
                interpret(payload, attempt.status)
            if json_response:
                return parse_upstream_json(payload, f"{self.label} {path}")
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            attempt.failure = "unusable_response"
            raise UpstreamUnavailableError(
                "The upstream text response is not UTF-8. Check the export URL.",
                surface=self.surface,
                attempts=attempt.number,
            ) from None
        except PlatformError as exc:
            attempt.failure = "unusable_response"
            details = dict(exc.details)
            if exc.code == "upstream_unavailable":
                details["attempts"] = attempt.number
            redacted = type(exc)(exc.code, self._redact(exc.message), **details)
        # Raised outside the handler, so that no chain holds the message as the platform
        # worded it: a platform can echo a header in its message.
        raise redacted

    def _http_failure(self, exc: HTTPError, path: str, attempt: _Attempt) -> Exception:
        """The failure an HTTP error status stands for: to retry, or to report as it is."""

        detail, empty_body = _error_detail(exc)
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
        failure = UpstreamRejectedError(
            message, surface=self.surface, status=exc.code, attempts=attempt.number
        )
        failure.empty_body = empty_body
        return failure

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

    def _attempt(
        self,
        request: Request,
        path: str,
        number: int,
        reject: Callable[[Any], str | None] | None,
        json_response: bool,
        interpret: Callable[[bytes, int | None], None] | None,
    ) -> Any:
        if budget := current_budget():
            budget.request()
        attempt = _Attempt(number)
        started = time.monotonic()
        try:
            payload = self._parse(
                self._exchange(request, path, attempt), path, attempt, json_response, interpret
            )
            reason = reject(payload) if reject else None
            if reason is not None:
                attempt.failure = "unusable_response"
                raise UpstreamUnavailableError(
                    self._redact(reason),
                    surface=self.surface,
                    status=attempt.status,
                    attempts=number,
                )
            return payload
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
        emit(
            logger,
            logging.WARNING,
            "upstream_request_retry",
            surface=self.surface,
            pathHash=hashed(path)["sha256"],
            attempt=attempt,
            maxAttempts=self.max_attempts,
            delayMs=delay * 1000,
            reasonHash=hashed(reason)["sha256"],
        )
        if delay:
            self.sleep(delay)

    def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        reject: Callable[[Any], str | None] | None = None,
        interpret: Callable[[bytes, int | None], None] | None = None,
    ) -> Any:
        """GET a JSON document, retrying transport failures, HTTP 429 and HTTP 5xx.

        Every attempt is counted: the error reports the number of requests made. Whatever
        the platform answered is classified before it is returned.
        A request-local reject callback may name unusable domain content. That terminal
        failure carries this response's status and actual attempt count.
        An explicit operation interpretation may raise a domain error before the shared
        parser; it cannot replace the content returned or disable common classification.
        """

        return self._run(self._request(path, params), path, reject=reject, interpret=interpret)

    def post_json(
        self,
        path: str,
        body: Any,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """POST JSON; every transport retry sends the identical body and headers."""
        request = self._request(
            path,
            None,
            data=json.dumps(body, allow_nan=False).encode("utf-8"),
            headers={**(headers or {}), "Content-Type": "application/json"},
        )
        return self._run(request, path)

    def get_text(self, path: str) -> str:
        """Read an explicitly textual export listing with the same bounds and audit."""
        request = self._request(path, None, headers={"Accept": "text/html"})
        return self._run(request, path, json_response=False)

    def post_form(self, path: str, fields: Mapping[str, str], *, accept: str) -> Any:
        """POST an encoded form for a JSON response; retries preserve its exact bytes."""
        request = self._request(
            path,
            None,
            data=urlencode(fields).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": accept},
        )
        return self._run(request, path)

    def _run(
        self,
        request: Request,
        path: str,
        *,
        reject: Callable[[Any], str | None] | None = None,
        json_response: bool = True,
        interpret: Callable[[bytes, int | None], None] | None = None,
    ) -> Any:
        attempts = timeouts = 0
        last_http: dict[str, Any] = {}
        while True:
            attempts += 1
            try:
                return self._attempt(request, path, attempts, reject, json_response, interpret)
            except _Transient as failure:
                timeouts += failure.timed_out
                last_http = failure.http_details(self._redact) or last_http
                delay = self._delay(attempts, failure)
                if attempts >= self.max_attempts or delay is None:
                    raise self._unavailable(failure, attempts, timeouts, last_http) from failure
                self._wait(path, attempts, delay, failure.message)
