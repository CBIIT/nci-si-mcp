"""Thin MCP adapter over the shared tool registry."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from functools import update_wrapper
from importlib.resources import files
from inspect import Parameter, Signature
from typing import TYPE_CHECKING, Annotated, Any, get_args, get_origin

from . import __version__
from .audit import audited, compact, hashed, secrets
from .caching import LONG_TTL_MS, cache_call, cache_hint
from .config import Settings, configure_logging
from .context import Context
from .errors import InputValidationError, is_error_record
from .invocation import call
from .parameters import Described
from .permissions import AuthorityResolver
from .registry import SPECS, ToolSpec, invoke
from .release_selection import SessionRelease, session_scope
from .results import Untruncated

if TYPE_CHECKING:
    from mcp.server.auth.provider import TokenVerifier
    from mcp.server.auth.settings import AuthSettings

INSTRUCTIONS = (
    "NCI Thesaurus (NCIt) lookup and relationship traversal against live NCI EVS, "
    "plus local indexed search and caDSR data-element lookup, matching and registry discovery. "
    "caDSR keyword search is a requested upstream capability not served today. Every item a tool "
    "returns carries a provenance record that names its terminology release or registry state, "
    "the surface that supplied it and the call's correlationId. A failed tool "
    "call is flagged as an error. Failures the server handles carry the error record "
    "{error: {code, message, details?, correlationId}}: code is one of invalid_request, "
    "not_found, release_not_available, release_mismatch, upstream_unavailable, timeout, "
    "bound_exceeded, capability_unavailable, cursor_expired, permission_denied or internal_error, "
    "and message "
    "names the next step; correlationId echoes the _meta.correlationId of the call, or is "
    "generated. Invalid tool arguments use the same error record."
)


def create_mcp(
    settings: Settings | None = None,
    *,
    context: Context | None = None,
    auth: AuthSettings | None = None,
    token_verifier: TokenVerifier | None = None,
    authority_resolver: AuthorityResolver | None = None,
):
    # Optional dependencies are imported only when building the MCP adapter.
    try:
        from mcp.server.caching import CacheHint
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ResourceError
        from mcp.types import CallToolResult, TextContent, ToolAnnotations
        from pydantic import Field, RootModel, with_config
    except ImportError as exc:
        raise RuntimeError(
            "The MCP server needs the 'server' extra, which installs mcp>=2,<3 "
            f"(pdm install). Import failed: {exc}"
        ) from exc

    # Only the complete truncation record is closed; upstream dictionaries stay extensible.
    with_config(extra="forbid")(Untruncated)
    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings.log_level)
    context = context or Context(resolved_settings)
    from .server_permissions import authorization

    protected = authority_resolver is not None or auth is not None
    mcp = MCPServer(
        "nci-si-mcp",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth=auth,
        token_verifier=token_verifier,
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
        middleware=[
            _audit_tools(context, resolved_settings.profile, protected=protected),
            *(
                [authorization(resolved_settings.profile, authority_resolver, _session_state)]
                if protected
                else []
            ),
            _cache_results,
            _validate_inputs(resolved_settings.profile),
            _release_session,
        ],
    )

    def tool_call(spec: ToolSpec, arguments: dict[str, Any]) -> Any:
        result = invoke(context, spec.operation, **arguments)
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
            # Handshake-era MCP requires outputSchema.type=object even for a union.
            # Every registry arm is an object; retain the union's detailed validation.
            record = Annotated[spec.output, Field(json_schema_extra={"type": "object"})]
            output = Annotated[CallToolResult, RootModel[record]]
            fn = _callback(spec, tool_call, output_type=output)
            mcp.add_tool(
                fn,
                name=spec.name,
                annotations=ToolAnnotations.model_validate(spec.annotations),
                meta={"group": spec.group},
                structured_output=True,
            )
            # The SDK derives the schema with pydantic's titles and the RootModel's
            # type expression; publish the same schema without them.
            tool: Any = mcp._tool_manager.get_tool(spec.name)
            tool.parameters = _served_input_schema(tool.parameters)
            tool.fn_metadata.output_schema = _output_schema(record, f"{spec.name} result")
        if spec.uri and spec.visible_in(resolved_settings.profile):
            fn = _callback(spec, resource_call)
            mcp.resource(spec.uri, mime_type="application/json")(fn)

    for spec in SPECS:
        register(spec)
    _register_prompts(mcp, resolved_settings.profile)
    return mcp


def _output_schema(record: Any, title: str) -> dict[str, Any]:
    """The schema of `record` as the SDK would publish it, without generated titles."""

    from mcp.server.mcpserver.utilities.func_metadata import StrictJsonSchema, _inline_root_ref
    from pydantic import RootModel, TypeAdapter

    class WithoutTitles(StrictJsonSchema):
        def generate(self, schema: Any, mode: Any = "validation") -> dict[str, Any]:
            return _drop_titles(super().generate(schema, mode))

    adapter = TypeAdapter(RootModel[record])
    schema = _inline_root_ref(adapter.json_schema(schema_generator=WithoutTitles))
    return {"title": title, **schema}


def _served_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The input schema with the nested records' field descriptions, closed to other arguments.

    The server refuses any argument the tool does not declare, so the schema says so."""

    from .parameters import FIELD_DESCRIPTIONS

    definitions = {
        name: _described_record(record, FIELD_DESCRIPTIONS.get(name, {}))
        for name, record in schema.get("$defs", {}).items()
    }
    return {
        **schema,
        **({"$defs": definitions} if definitions else {}),
        "additionalProperties": False,
    }


def _described_record(record: dict[str, Any], descriptions: dict[str, str]) -> dict[str, Any]:
    properties = {
        name: {**spec, "description": descriptions[name]} if name in descriptions else spec
        for name, spec in record.get("properties", {}).items()
    }
    return {**record, "properties": properties} if properties else record


def _field_annotation(annotation: Any) -> Any:
    """`annotation` with its `Described` metadata as the pydantic `Field` that states it."""

    from pydantic import Field

    if get_origin(annotation) is not Annotated:
        return annotation
    base, *metadata = get_args(annotation)
    fields = [Field(**m.field_arguments()) if isinstance(m, Described) else m for m in metadata]
    return Annotated[(base, *fields)]


def _drop_titles(node: Any) -> Any:
    """`node` without its `title` keywords; the names under `properties` are kept."""

    if isinstance(node, list):
        return [_drop_titles(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: _named(value) if key == "properties" else _drop_titles(value)
        for key, value in node.items()
        if key != "title"
    }


def _named(properties: Any) -> Any:
    return {name: _drop_titles(schema) for name, schema in properties.items()}


def _register_prompts(mcp: Any, profile: str) -> None:
    from mcp.server.mcpserver.prompts import Prompt
    from mcp.server.mcpserver.prompts.base import PromptArgument

    # Packaged without a YAML dependency; test_prompts verifies equality with spec/.
    templates = json.loads(files("nci_si_mcp").joinpath("data/prompts.json").read_text())
    tools = {spec.name for spec in SPECS if spec.name and spec.visible_in(profile)}
    for name, template in templates.items():
        if set(template["tools"]) <= tools:
            mcp.add_prompt(
                Prompt(
                    name=name,
                    title=template["title"],
                    description=template["adds"],
                    arguments=[PromptArgument(**argument) for argument in template["arguments"]],
                    fn=_prompt_callback(template),
                    context_kwarg=None,
                )
            )


def _prompt_callback(template: dict[str, Any]) -> Callable[..., str]:
    def render(**arguments: str) -> str:
        values = {a["name"]: "" for a in template["arguments"] if not a["required"]}
        return template["template"].format(**(values | arguments))

    return render


def _callback(
    spec: ToolSpec,
    call: Callable[..., Any],
    *,
    output_type: Any = dict[str, Any],
) -> Callable[..., Any]:
    def callback(**arguments: Any) -> Any:
        return call(spec, arguments)

    parameters = [p.replace(annotation=_field_annotation(p.annotation)) for p in spec.parameters]
    update_wrapper(callback, spec.handler)
    callback.__name__ = spec.name or spec.operation
    callback.__dict__["__signature__"] = Signature(parameters, return_annotation=output_type)
    callback.__annotations__ = {p.name: p.annotation for p in parameters} | {"return": output_type}
    return callback


def _audit_tools(context: Context, profile: str, *, protected: bool = False) -> Callable[..., Any]:
    specs = {spec.name: spec for spec in SPECS if spec.name and spec.visible_in(profile)}
    fields = {name: {} if protected else spec.audit for name, spec in specs.items()}
    hidden = secrets(context.settings.evs_license_key, context.settings.cadsr_credential)

    async def record_call(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params or {}
        name = params.get("name", "")
        spec = specs.get(name)
        arguments = params.get("arguments", {})
        with audited(
            name if spec else compact(hashed(name)),
            arguments if isinstance(arguments, dict) else {"arguments": arguments},
            fields.get(name, {}),
            hidden,
            (ctx.meta or {}).get("correlationId"),
        ) as record:
            result = await call_next(ctx)
            record.result = result.get("structuredContent")
            return result

    return record_call


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
        p.name: (
            _field_annotation(p.annotation),
            ... if p.default is Parameter.empty else p.default,
        )
        for p in spec.parameters
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
        if error["type"] == "missing" and len(error["loc"]) > 1:
            # Name the incomplete object, as callers must supply its required fields.
            parameter = ".".join(str(part) for part in error["loc"][:-1])
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


async def _release_session(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
    state = _session_state(ctx)
    pin = None
    if state is not None:
        pin = state.setdefault("nci_si_implicit_release", SessionRelease())
    with session_scope(pin):
        return await call_next(ctx)


def _session_state(ctx: Any) -> dict[str, Any] | None:
    # MCP 2 creates ServerSession per request. Its connection owns the validated
    # HTTP session, not the proxy or an untrusted Mcp-Session-Id header.
    connection = ctx.session._connection
    if ctx.request is None:
        # A stdio lifespan is one session, including the SDK's envelope protocol
        # which creates a fresh Connection for each request on that same stream.
        return ctx.lifespan_context
    if connection.session_id is not None:
        return connection.state
    return None
