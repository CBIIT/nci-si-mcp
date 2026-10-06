"""One tool declaration and invocation path for MCP, CLI and resources."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, make_dataclass
from inspect import Parameter, Signature, getdoc, signature
from typing import Any, Literal, get_args, get_origin, get_type_hints, is_typeddict

from . import cadsr_content, cadsr_matching, content, handlers
from .audit import AuditClass, audited, secrets
from .caching import invocation_policy
from .context import Context
from .invocation import call
from .release_selection import selection_scope
from .results import (
    ClassificationSchemesResult,
    CodeMapResource,
    CodeMapsResult,
    Concept,
    ConceptBatch,
    ConceptResult,
    ConceptSearch,
    ContextsResult,
    DataElement,
    DataElementMatches,
    DataElementSearch,
    ErrorResult,
    Form,
    Hierarchy,
    IndexManifestResult,
    MappingsResult,
    Neighborhood,
    PermissibleValue,
    RegistryReleaseResult,
    RelationshipsResult,
    ReleaseResult,
    ResolvedReleaseResult,
    RetiredCode,
    SearchResult,
    SubsetsResult,
    TerminologiesResult,
    TraversalResult,
    ValueMeaningMatches,
    ValueSetExpansion,
)


@dataclass(frozen=True)
class ToolSpec:
    """One declaration shared by both adapters; input fields come from the handler."""

    handler: Callable[..., dict[str, Any]]
    group: str
    output: Any
    resolution: bool
    name: str | None = None
    command: str | None = None
    uri: str | None = None
    audit: dict[str, AuditClass] = field(default_factory=dict)
    input_model: type = field(init=False)
    parameters: tuple[Parameter, ...] = field(init=False)

    def __post_init__(self) -> None:
        hints = get_type_hints(self.handler)
        parameters = tuple(
            p.replace(annotation=hints[p.name])
            for p in list(signature(self.handler).parameters.values())[1:]
        )
        model = make_dataclass(
            self.handler.__name__ + "Input",
            [_input_field(p) for p in parameters],
        )
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "input_model", model)

    @property
    def operation(self) -> str:
        return self.handler.__name__

    @property
    def description(self) -> str:
        return getdoc(self.handler) or ""

    @property
    def annotations(self) -> dict[str, bool]:
        return {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }

    def visible_in(self, profile: str) -> bool:
        return profile in ("unified", self.group)

    def arguments(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        bound = Signature(self.parameters).bind(*args, **kwargs)
        return asdict(self.input_model(**bound.arguments))


def _input_field(parameter: Parameter) -> tuple:
    declaration = (parameter.name, parameter.annotation)
    if parameter.default is Parameter.empty:
        return declaration
    return (*declaration, field(default=parameter.default))


SPECS = (
    ToolSpec(
        cadsr_content.get_form,
        "cadsr",
        Form | ErrorResult,
        False,
        name="get_form",
        command="get-form",
        audit={
            "publicId": "plain",
            "keyword": "hash",
            "version": "plain",
            "includeModules": "plain",
            "registryRelease": "plain",
        },
    ),
    ToolSpec(
        cadsr_content.get_permissible_value,
        "cadsr",
        PermissibleValue | ErrorResult,
        False,
        name="get_permissible_value",
        command="get-permissible-value",
        audit={"permissibleValueId": "plain", "registryRelease": "plain"},
    ),
    ToolSpec(
        cadsr_content.get_code_map,
        "cadsr",
        CodeMapsResult | ErrorResult,
        False,
        name="get_code_map",
        command="get-code-map",
        audit={
            "sourceSystem": "plain",
            "targetContext": "hash",
            "dataElementId": "plain",
            "limit": "plain",
            "cursor": "hash",
            "registryRelease": "plain",
        },
    ),
    ToolSpec(
        cadsr_content.crosswalk_resource,
        "cadsr",
        CodeMapResource | ErrorResult,
        False,
        uri="cadsr://crosswalk/crdc",
    ),
    ToolSpec(
        cadsr_matching.match_data_elements,
        "cadsr",
        DataElementMatches | ErrorResult,
        False,
        name="match_data_elements",
        command="match-data-elements",
        audit={
            "entities": "hash",
            "matchLimit": "plain",
            "modelVariant": "hash",
            "similarityThreshold": "plain",
            "filters": "hash",
            "registryRelease": "plain",
        },
    ),
    ToolSpec(
        cadsr_matching.match_value_meanings,
        "cadsr",
        ValueMeaningMatches | ErrorResult,
        False,
        name="match_value_meanings",
        command="match-value-meanings",
        audit={
            "values": "hash",
            "strictness": "plain",
            "terminologyScope": "plain",
            "registryRelease": "plain",
        },
    ),
    ToolSpec(
        cadsr_content.get_data_element,
        "cadsr",
        DataElement | ErrorResult,
        False,
        name="get_data_element",
        command="get-data-element",
        audit={
            "publicId": "plain",
            "longName": "hash",
            "questionText": "hash",
            "version": "plain",
            "include": "plain",
            "registryRelease": "plain",
        },
    ),
    ToolSpec(
        cadsr_content.search_data_elements,
        "cadsr",
        DataElementSearch | ErrorResult,
        False,
        name="search_data_elements",
        command="search-data-elements",
        audit={
            "query": "hash",
            "mode": "plain",
            "filters": "hash",
            "cursor": "hash",
            "limit": "plain",
            "registryRelease": "plain",
        },
    ),
    ToolSpec(
        cadsr_content.list_contexts,
        "cadsr",
        ContextsResult | ErrorResult,
        False,
        name="list_contexts",
        command="list-contexts",
        audit={"limit": "plain", "cursor": "hash", "registryRelease": "plain"},
    ),
    ToolSpec(
        cadsr_content.list_classification_schemes,
        "cadsr",
        ClassificationSchemesResult | ErrorResult,
        False,
        name="list_classification_schemes",
        command="list-classification-schemes",
        audit={"context": "hash", "limit": "plain", "cursor": "hash", "registryRelease": "plain"},
    ),
    ToolSpec(
        cadsr_content.resolve_registry_release,
        "cadsr",
        RegistryReleaseResult | ErrorResult,
        True,
        name="resolve_registry_release",
        command="resolve-registry-release",
    ),
    ToolSpec(
        cadsr_content.data_element_resource,
        "cadsr",
        DataElement | ErrorResult,
        False,
        uri="cadsr://data-element/{publicId}",
        audit={"publicId": "plain"},
    ),
    ToolSpec(
        cadsr_content.data_element_version_resource,
        "cadsr",
        DataElement | ErrorResult,
        False,
        uri="cadsr://data-element/{publicId}/{version}",
        audit={"publicId": "plain", "version": "plain"},
    ),
    ToolSpec(
        cadsr_content.registry_resource,
        "cadsr",
        RegistryReleaseResult | ErrorResult,
        False,
        uri="cadsr://registry/release",
    ),
    ToolSpec(
        content.expand_value_set,
        "evs",
        ValueSetExpansion | ErrorResult,
        False,
        name="expand_value_set",
        audit={
            "terminology": "plain",
            "release": "plain",
            "valueSet": "plain",
            "code": "plain",
            "count": "plain",
            "offset": "plain",
            "activeOnly": "plain",
        },
    ),
    ToolSpec(
        content.get_concept_subsets,
        "evs",
        SubsetsResult | ErrorResult,
        False,
        name="get_concept_subsets",
        audit={"terminology": "plain", "release": "plain", "code": "plain"},
    ),
    ToolSpec(
        content.get_concept_mappings,
        "evs",
        MappingsResult | ErrorResult,
        False,
        name="get_concept_mappings",
        audit={
            "terminology": "plain",
            "release": "plain",
            "code": "plain",
            "targetTerminology": "plain",
        },
    ),
    ToolSpec(
        content.resolve_retired_code,
        "evs",
        RetiredCode | ErrorResult,
        False,
        name="resolve_retired_code",
        audit={"terminology": "plain", "release": "plain", "code": "plain"},
    ),
    ToolSpec(
        content.list_relationships,
        "evs",
        RelationshipsResult | ErrorResult,
        False,
        name="list_relationships",
        audit={"terminology": "plain", "release": "plain"},
    ),
    ToolSpec(
        content.get_concepts,
        "evs",
        ConceptBatch | ErrorResult,
        False,
        name="get_concepts",
        audit={"terminology": "plain", "release": "plain", "codes": "plain", "include": "plain"},
    ),
    ToolSpec(
        content.get_concept,
        "evs",
        Concept | ErrorResult,
        False,
        name="get_concept",
        audit={"terminology": "plain", "release": "plain", "code": "plain", "include": "plain"},
    ),
    ToolSpec(
        content.search_concepts,
        "evs",
        ConceptSearch | ErrorResult,
        False,
        name="search_concepts",
        audit={
            "terminology": "plain",
            "release": "plain",
            "query": "hash",
            "mode": "plain",
            "limit": "plain",
            "cursor": "hash",
            "retired": "plain",
        },
    ),
    ToolSpec(
        content.get_concept_hierarchy,
        "evs",
        Hierarchy | ErrorResult,
        False,
        name="get_concept_hierarchy",
        audit={
            "terminology": "plain",
            "release": "plain",
            "code": "plain",
            "direction": "plain",
            "depth": "plain",
            "limit": "plain",
            "cursor": "hash",
        },
    ),
    ToolSpec(
        content.get_concept_neighborhood,
        "evs",
        Neighborhood | ErrorResult,
        False,
        name="get_concept_neighborhood",
        audit={
            "terminology": "plain",
            "release": "plain",
            "code": "plain",
            "depth": "plain",
            "kinds": "plain",
            "maxNodes": "plain",
            "maxEdges": "plain",
            "budgetPerKind": "plain",
            "includeNegative": "plain",
        },
    ),
    ToolSpec(
        handlers.resolve_release,
        "evs",
        ResolvedReleaseResult | ErrorResult,
        True,
        name="resolve_release",
        command="resolve-release",
        audit={"terminology": "plain", "channel": "plain"},
    ),
    ToolSpec(
        handlers.list_terminologies,
        "evs",
        TerminologiesResult | ErrorResult,
        True,
        name="list_terminologies",
        command="list-terminologies",
    ),
    ToolSpec(
        handlers.search,
        "evs",
        SearchResult | ErrorResult,
        False,
        command="search",
        audit={"query": "hash", "limit": "plain", "mode": "plain", "include_raw": "plain"},
    ),
    ToolSpec(
        handlers.lookup,
        "evs",
        ConceptResult | ErrorResult,
        False,
        command="lookup",
        audit={"code": "plain", "live_only": "plain", "include_raw": "plain"},
    ),
    ToolSpec(
        handlers.traverse,
        "evs",
        TraversalResult | ErrorResult,
        False,
        command="traverse",
        audit={
            "start_codes": "plain",
            "direction": "plain",
            "max_depth": "plain",
            "max_nodes": "plain",
            "max_edges": "plain",
            "include_hierarchy": "plain",
            "include_roles": "plain",
            "include_associations": "plain",
            "relationship_names": "hash",
            "edge_types": "plain",
            "budget_per_kind": "plain",
        },
    ),
    ToolSpec(
        handlers.release_info,
        "evs",
        ReleaseResult | ErrorResult,
        True,
        command="release-info",
    ),
    ToolSpec(
        handlers.index_codes,
        "evs",
        dict[str, Any],
        False,
        command="index-sample",
        audit={"codes": "plain"},
    ),
    ToolSpec(
        handlers.evaluate,
        "evs",
        dict[str, Any],
        False,
        command="evaluate",
        audit={"build_id": "plain"},
    ),
    ToolSpec(handlers.index_build, "evs", dict[str, Any], False, command="index-build"),
    ToolSpec(handlers.index_builds, "evs", dict[str, Any], False, command="index-builds"),
    ToolSpec(
        handlers.index_rebuild,
        "evs",
        dict[str, Any],
        False,
        command="index-rebuild",
        audit={"build_id": "plain"},
    ),
    ToolSpec(
        handlers.index_activate,
        "evs",
        dict[str, Any],
        False,
        command="index-activate",
        audit={"build_id": "plain"},
    ),
    ToolSpec(
        handlers.concept_resource,
        "evs",
        Concept | ErrorResult,
        False,
        uri="ncit://concept/{release}/{code}",
        audit={"release": "plain", "code": "plain"},
    ),
    ToolSpec(
        handlers.release_resource,
        "evs",
        ResolvedReleaseResult | ErrorResult,
        False,
        uri="ncit://release/{version}",
        audit={"version": "plain"},
    ),
    ToolSpec(
        handlers.index_resource,
        "evs",
        IndexManifestResult | ErrorResult,
        False,
        uri="ncit://index/manifest/{release}",
        audit={"release": "plain"},
    ),
)
OPERATIONS = {spec.operation: spec for spec in SPECS}


def invoke(
    context: Context, operation: str, /, *args: Any, _correlation_id: object = None, **kwargs: Any
) -> dict[str, Any]:
    """Invoke any producer under the same correlation, error and cache boundary."""

    spec = OPERATIONS[operation]

    def produce() -> dict[str, Any]:
        with invocation_policy(resolution=spec.resolution), selection_scope():
            return spec.handler(context, **spec.arguments(args, kwargs))

    arguments = dict(zip((p.name for p in spec.parameters), args, strict=False)) | kwargs
    hidden = secrets(context.settings.evs_license_key, context.settings.cadsr_credential)
    with audited(spec.name or operation, arguments, spec.audit, hidden, _correlation_id) as record:
        result = call(operation, produce)
        record.result = result
        return result


# CLI spellings differ from the shared handler fields only in these legacy flags.
_CLI_FLAGS = {
    "includeModules": "--no-modules",
    "include_hierarchy": "--no-hierarchy",
    "include_roles": "--no-roles",
    "include_associations": "--no-associations",
    "relationship_names": "--relationship-name",
    "edge_types": "--edge-type",
}


def _argument_type(annotation: Any) -> tuple[Any, bool, tuple[Any, ...]]:
    args = get_args(annotation)
    if type(None) in args:
        return _argument_type(next(arg for arg in args if arg is not type(None)))
    if get_origin(annotation) is list:
        scalar, _multiple, choices = _argument_type(args[0])
        return scalar, True, choices
    if get_origin(annotation) is Literal:
        return type(args[0]), False, tuple(sorted(args))
    return _scalar_type(annotation), False, ()


def _scalar_type(annotation: Any) -> Any:
    """Structured CLI arguments are JSON objects; scalar arguments use their own parser."""
    if get_origin(annotation) is dict or is_typeddict(annotation):
        return json.loads
    return annotation


def _value_options(parameter: Parameter) -> dict[str, Any]:
    scalar, multiple, choices = _argument_type(parameter.annotation)
    if scalar is bool:
        return {"action": "store_false" if parameter.default else "store_true"}
    options: dict[str, Any] = {"type": scalar}
    if choices:
        options["choices"] = choices
    if multiple:
        if parameter.default is Parameter.empty:
            options["nargs"] = "+"
        else:
            options["action"] = "append"
    return options


def _cli_argument(parameter: Parameter) -> tuple[tuple[str, ...], dict[str, Any]]:
    options = _value_options(parameter)
    flag = parameter.name
    if parameter.default is not Parameter.empty:
        spelling = re.sub(r"(?<!^)(?=[A-Z])", "-", parameter.name).lower().replace("_", "-")
        flag = _CLI_FLAGS.get(parameter.name, "--" + spelling)
        options.update(default=parameter.default, dest=parameter.name)
    return (flag,), options


def cli_arguments(spec: ToolSpec) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    return [_cli_argument(parameter) for parameter in spec.parameters]
