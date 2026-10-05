"""Thin MCP adapter over the shared tool registry."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from functools import update_wrapper
from inspect import Parameter, Signature
from typing import Annotated, Any

from . import __version__
from .caching import LONG_TTL_MS, cache_call, cache_hint
from .config import Settings, configure_logging
from .context import Context
from .errors import InputValidationError, is_error_record
from .invocation import call
from .registry import SPECS, ToolSpec, invoke

INSTRUCTIONS = (
    "NCI Thesaurus (NCIt) lookup and relationship traversal against live NCI EVS, "
    "plus text search over a small locally indexed sample of concepts. Every item a tool "
    "returns carries a provenance record that names the NCIt monthly release it came from, "
    "the surface that supplied it and the call's correlationId. A failed tool "
    "call is flagged as an error. Failures the server handles carry the error record "
    "{error: {code, message, details?, correlationId}}: code is one of invalid_request, "
    "not_found, release_not_available, release_mismatch, upstream_unavailable, timeout, "
    "bound_exceeded, capability_unavailable, cursor_expired or internal_error, and message "
    "names the next step; correlationId echoes the _meta.correlationId of the call, or is "
    "generated. Invalid tool arguments use the same error record."
)


def create_mcp(settings: Settings | None = None, *, context: Context | None = None):
    # Optional dependencies are imported only when building the MCP adapter.
    try:
        from mcp.server.caching import CacheHint
        from mcp.server.mcpserver import Context as MCPContext
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ResourceError
        from mcp.types import CallToolResult, TextContent, ToolAnnotations
        from pydantic import RootModel
    except ImportError as exc:
        raise RuntimeError(
            "The MCP server needs the 'server' extra, which installs mcp>=2,<3 "
            f"(pdm install). Import failed: {exc}"
        ) from exc

    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings.log_level)
    context = context or Context(resolved_settings)
    mcp = MCPServer(
        "nci-si-mcp",
        instructions=INSTRUCTIONS,
        version=__version__,
        cache_hints=dict.fromkeys(
            (
                "tools/list",
                "prompts/list",
                "resources/list",
                "resources/templates/list",
                "server/discover",
            ),
            CacheHint(ttl_ms=LONG_TTL_MS, scope="public"),
        ),
        middleware=[_cache_results, _validate_inputs(resolved_settings.profile)],
    )

    def tool_call(spec: ToolSpec, arguments: dict[str, Any]) -> Any:
        ctx = arguments.pop("ctx")
        meta = ctx.request_context.meta or {}
        result = invoke(
            context, spec.operation, _correlation_id=meta.get("correlationId"), **arguments
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, indent=2))],
            structured_content=result,
            is_error=is_error_record(result),
        )

    def resource_call(spec: ToolSpec, arguments: dict[str, Any]) -> Any:
        result = invoke(context, spec.operation, **arguments)
        if is_error_record(result):
            raise ResourceError(json.dumps(result))
        return result

    def register(spec: ToolSpec) -> None:
        if spec.name and spec.visible_in(resolved_settings.profile):
            # The SDK wraps a bare union in a synthetic result field. RootModel keeps
            # the existing top-level object and all optional wire fields unchanged.
            output = Annotated[CallToolResult, RootModel[spec.output]]
            fn = _callback(spec, tool_call, context_type=MCPContext, output_type=output)
            mcp.add_tool(
                fn,
                name=spec.name,
                annotations=ToolAnnotations.model_validate(spec.annotations),
                meta={"group": spec.group},
                structured_output=True,
            )
        if spec.uri:
            fn = _callback(spec, resource_call)
            mcp.resource(spec.uri, mime_type="application/json")(fn)

    for spec in SPECS:
        register(spec)
    return mcp


def _callback(
    spec: ToolSpec,
    call: Callable[..., Any],
    *,
    context_type: Any = None,
    output_type: Any = dict[str, Any],
) -> Callable[..., Any]:
    def callback(**arguments: Any) -> Any:
        return call(spec, arguments)

    parameters = [p for p in spec.parameters if p.name not in spec.cli_only]
    if context_type is not None:
        parameters.append(Parameter("ctx", Parameter.KEYWORD_ONLY, annotation=context_type))
    update_wrapper(callback, spec.handler)
    callback.__name__ = spec.name or spec.operation
    callback.__dict__["__signature__"] = Signature(parameters, return_annotation=output_type)
    callback.__annotations__ = {p.name: p.annotation for p in parameters} | {"return": output_type}
    return callback


def _validate_inputs(profile: str) -> Callable[..., Any]:
    # The SDK renders argument-validation failures as plain text. Validate the same
    # registry fields first so invalid requests use the platform's one error path.
    from mcp.types import CallToolResult, TextContent

    models = {
        spec.name: _input_model(spec) for spec in SPECS if spec.name and spec.visible_in(profile)
    }

    async def validate(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        params = ctx.params or {}
        model = models.get(params.get("name", "")) if ctx.method == "tools/call" else None
        if model is not None:
            result = call(
                params["name"],
                lambda: _check_arguments(model, params.get("arguments", {})),
                correlation_id=(ctx.meta or {}).get("correlationId"),
            )
            if is_error_record(result):
                return CallToolResult(
                    is_error=True,
                    structured_content=result,
                    content=[TextContent(type="text", text=json.dumps(result))],
                ).model_dump(by_alias=True, exclude_none=True)
        return await call_next(ctx)

    return validate


def _input_model(spec: ToolSpec) -> Any:
    from pydantic import ConfigDict, create_model

    fields: dict[str, Any] = {
        p.name: (p.annotation, ... if p.default is Parameter.empty else p.default)
        for p in spec.parameters
        if p.name not in spec.cli_only
    }
    return create_model(
        spec.operation + "Arguments", __config__=ConfigDict(strict=True, extra="forbid"), **fields
    )


def _check_arguments(model: Any, arguments: Any) -> dict[str, Any]:
    from pydantic import ValidationError

    try:
        model.model_validate(arguments)
    except ValidationError as exc:
        error = exc.errors(include_input=False)[0]
        parameter = str(error["loc"][0]) if error["loc"] else "arguments"
        raise InputValidationError(error["msg"], parameter) from None
    return {}


async def _cache_results(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
    """Keep tool hints in protocol metadata and resource hints on the protocol result."""

    if ctx.method not in {"tools/call", "resources/read"}:
        return await call_next(ctx)
    with cache_call() as decision:
        result = await call_next(ctx)
        hint = cache_hint(error=True) if result.get("isError", False) else decision
        if not hint:
            raise RuntimeError("A registered tool or resource must declare its cache policy.")
        if ctx.method == "tools/call":
            return {**result, "_meta": {**result.get("_meta", {}), **hint}}
        return {**result, **hint}
