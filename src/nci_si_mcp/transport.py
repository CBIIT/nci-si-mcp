"""HTTP serving over the same MCP adapter, with process-local session ownership."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .audit import emit
from .config import Settings
from .context import Context
from .errors import PlatformError
from .index import IndexCompatibilityError, IndexStorageError, NoActiveIndexError
from .permissions import AuthorityResolver
from .server import create_mcp

if TYPE_CHECKING:
    from mcp.server.auth.provider import TokenVerifier
    from mcp.server.auth.settings import AuthSettings
    from starlette.applications import Starlette
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class HTTPBoundary:
    """Protect all HTTP routes and record only the status of authentication refusals."""

    def __init__(self, app: ASGIApp, security: Any, protected: bool = False) -> None:
        from mcp.server.transport_security import TransportSecurityMiddleware

        self.app = app
        self.protected = protected
        self.security = TransportSecurityMiddleware(security)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        from starlette.requests import Request

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def report(message: Message) -> None:
            if message["type"] == "http.response.start" and self.protected:
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"
                ]
                message = {**message, "headers": [*headers, (b"cache-control", b"no-store")]}
            if message["type"] == "http.response.start" and _auth_refusal(message):
                emit(logger, logging.WARNING, "http_auth_rejected", status=message["status"])
            await send(message)

        if refused := await self.security.validate_request(Request(scope)):
            await refused(scope, receive, report)
            return
        await self.app(scope, receive, report)


def _auth_refusal(message: Message) -> bool:
    return message["status"] in (401, 403) and any(
        key.lower() == b"www-authenticate" for key, _ in message.get("headers", [])
    )


def _readiness_error(context: Context, require_index: bool) -> str | None:
    try:
        if not require_index and context.index.get_active_manifest() is None:
            return None
        context.index.verify_active(context.embedding_provider)
    except (IndexStorageError, IndexCompatibilityError, NoActiveIndexError, PlatformError) as exc:
        return type(exc).__name__
    return None


def create_http_app(
    settings: Settings,
    *,
    context: Context | None = None,
    auth: AuthSettings | None = None,
    token_verifier: TokenVerifier | None = None,
    authority_resolver: AuthorityResolver | None = None,
) -> Starlette:
    """Inject an approved auth provider here; the default accepts unauthenticated clients."""
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    context = context or Context(settings)
    mcp = create_mcp(
        settings,
        context=context,
        auth=auth,
        token_verifier=token_verifier,
        authority_resolver=authority_resolver,
    )
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.http_allowed_hosts),
        allowed_origins=list(settings.http_allowed_origins),
    )
    app = mcp.streamable_http_app(
        stateless_http=settings.http_sessions == "stateless",
        json_response=True,
        max_request_body_size=settings.http_max_request_bytes,
        transport_security=security,
        session_idle_timeout=1800,
        max_sessions=10_000,
    )

    def health(request: Any) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    was_ready = True

    async def ready(request: Any) -> JSONResponse:
        nonlocal was_ready
        # SQLite can wait on a writer; health and MCP requests must remain responsive.
        error = await run_in_threadpool(
            _readiness_error, context, bool(settings.http_require_index)
        )
        # Keep the transition atomic on the event loop after the storage check.
        available = error is None
        if was_ready and not available:
            emit(logger, logging.WARNING, "http_not_ready", errorType=error)
        was_ready = available
        return JSONResponse(
            {"status": "ready" if available else "not_ready"},
            status_code=200 if available else 503,
        )

    app.routes.extend([Route("/health", health), Route("/ready", ready)])
    app.add_middleware(
        HTTPBoundary,
        security=security,
        protected=auth is not None or authority_resolver is not None,
    )
    return app


def run_http(settings: Settings, context: Context) -> None:
    """One process owns its sessions; a supervisor can start independent replicas."""
    import uvicorn

    uvicorn.run(
        create_http_app(settings, context=context),
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
    )
