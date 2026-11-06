"""Reviewed live destinations; fixture history never grants executable permission."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, get_args
from urllib.parse import urlsplit

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode

if TYPE_CHECKING:
    from collections.abc import Mapping

type Surface = Literal[
    "evs", "evs-fhir", "cadsr", "cadsr-ftp", "cadsr-contracts", "ssis", "ssis-sparql"
]
SURFACES = get_args(Surface.__value__)
VARIABLE = "NCI_SI_ACCEPTANCE_ENVIRONMENT"
POLICY = Path(__file__).parents[2] / "fixtures/environments.yaml"
LAUNCHER = [sys.executable, "-I", "-m", "nci_si_acceptance.live_launcher"]


@dataclass(frozen=True, slots=True)
class Environment:
    """One immutable selection shared by all managed callers of this run."""

    name: str
    origins: Mapping[str, str | None]


def _mapping(node: Node | None) -> dict[str, Node]:
    if not isinstance(node, MappingNode):
        raise ValueError("invalid reviewed environment policy mapping")
    result: dict[str, Node] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str":
            raise ValueError("invalid reviewed environment policy key")
        if key.value in result:
            raise ValueError("duplicate reviewed environment policy key")
        result[key.value] = value
    return result


def _url(node: Node) -> str | None:
    if not isinstance(node, ScalarNode):
        raise ValueError("invalid reviewed environment origin")
    if node.tag == "tag:yaml.org,2002:null":
        return None
    try:
        parsed = urlsplit(node.value)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid reviewed environment origin") from None
    if any(
        (
            node.tag != "tag:yaml.org,2002:str",
            parsed.scheme not in ("http", "https"),
            not parsed.hostname,
            parsed.username is not None,
            parsed.password is not None,
            bool(parsed.query),
            bool(parsed.fragment),
            port == 0,
        )
    ):
        raise ValueError("invalid reviewed environment origin")
    return node.value.rstrip("/")


def _origins(node: Node) -> dict[str, str | None]:
    values = _mapping(node)
    if set(values) != set(SURFACES):
        raise ValueError("reviewed environment policy needs every known surface exactly once")
    return {key: _url(value) for key, value in values.items()}


def selection(mode: str) -> Environment | None:
    name = os.environ.get(VARIABLE)
    if mode == "fixture":
        if name is not None:
            raise ValueError(f"{VARIABLE} applies only to local live mode")
        return None
    if not name:
        raise ValueError(f"{VARIABLE} is required for live mode")
    return _selected(name)


def _selected(name: str) -> Environment:
    allowed, maps = _policy()
    if name not in maps:
        raise ValueError(f"{VARIABLE} does not name a reviewed environment")
    origins = _origins(maps[name])
    if any(value is not None and value != allowed[key] for key, value in origins.items()):
        raise ValueError("selected environment exceeds reviewed surface permission")
    return Environment(name, MappingProxyType(origins))


def _policy() -> tuple[dict[str, str | None], dict[str, Node]]:
    try:
        raw = POLICY.read_bytes()
        document = _mapping(yaml.compose(raw))
    except OSError, yaml.YAMLError:
        raise ValueError("cannot read reviewed environment policy") from None
    if set(document) != {"allowed", "environments"}:
        raise ValueError("invalid reviewed environment policy fields")
    allowed = _origins(document["allowed"])
    maps = _mapping(document["environments"])
    return allowed, maps


def validate_live_inputs(environment: Environment) -> None:
    exact = {
        "NCI_SI_ACCEPTANCE_SERVER": "nci-si-mcp serve",
        "NCI_SI_ACCEPTANCE_PREPARE": "nci-si-mcp index-sample",
        "NCI_SI_ACCEPTANCE_PROFILE": "unified",
    }
    for key, value in os.environ.items():
        if key in exact:
            if value != exact[key]:
                raise ValueError(f"{key} requires the documented exact live selector")
        elif _incompatible(key, environment):
            raise ValueError(f"{key} is incompatible with the selected live environment")


def _incompatible(key: str, environment: Environment) -> bool:
    if key.lower().endswith("_proxy") or key.upper().startswith("PYTHON"):
        return True
    if key.startswith("NCI_SI_") and key.endswith(("_BASE_URL", "_FTP_URL", "_SPARQL_URL")):
        return True
    forbidden = {
        "NCI_SI_ACCEPTANCE_URL",
        "NCI_SI_ACCEPTANCE_SECURITY_SERVER",
        "NCI_SI_ACCEPTANCE_FIXTURE_BIND",
        "NCI_SI_ACCEPTANCE_FIXTURE_URL",
    }
    if key in forbidden:
        return True
    return key == "NCI_SI_CADSR_CREDENTIAL" and environment.origins["cadsr"] is None
