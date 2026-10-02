"""The project's specification, as data: the conventions, the records and the required tools
(`spec/`).

`requirements.py` reads the requirements beside them, and `document.py` renders all three
as the readable specification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SPEC = Path(__file__).parents[3] / "spec"


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((SPEC / name).read_text(encoding="utf-8"))


CONVENTIONS: dict[str, dict[str, Any]] = _load("conventions.yaml")
TOOLS: dict[str, dict[str, Any]] = _load("tools.yaml")
RECORDS: dict[str, dict[str, Any]] = _load("records.yaml")
# The group of each required tool: A terminology, B metadata, C cross-domain, W workflow.
REQUIRED_TOOLS = {name: tool["group"] for name, tool in TOOLS.items()}


def is_basis(name: str) -> bool:
    """Whether `name` is a convention (a section such as A6, or a rule such as A3.6.1, or a
    subsection such as A3.6 above rules), a record (provenance) or a required tool."""

    return name in CONVENTIONS or name in RECORDS or name in TOOLS or _is_rule(name)


def _is_rule(name: str) -> bool:
    rules = {rule for section in CONVENTIONS.values() for rule in section["rules"]}
    return any(rule == name or rule.startswith(f"{name}.") for rule in rules)
