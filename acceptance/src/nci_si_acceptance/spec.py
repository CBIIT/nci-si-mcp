"""The project's specification, as data: the conventions, the records and the required tools
(`spec/`), and the profiles that serve them.

`requirements.py` reads the requirements beside them, and `document.py` renders them all
as the readable specification.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

SPEC = Path(__file__).parents[3] / "spec"


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((SPEC / name).read_text(encoding="utf-8"))


CONVENTIONS: dict[str, dict[str, Any]] = _load("conventions.yaml")
TOOLS: dict[str, dict[str, Any]] = _load("tools.yaml")
RECORDS: dict[str, dict[str, Any]] = _load("records.yaml")
# The group of each required tool: evs, cadsr, cross-domain or workflow.
REQUIRED_TOOLS = {name: tool["group"] for name, tool in TOOLS.items()}


# The profiles of M1.5: one per module, serving its own group, and unified, serving all.
PROFILES = ("evs", "cadsr", "unified")


def profile_tools(profile: str) -> set[str]:
    """The required tools a server of `profile` lists."""

    return {name for name, group in REQUIRED_TOOLS.items() if profile in (group, "unified")}


def items_of(tool: str, result: Any) -> list[Any]:
    """The items of a result of `tool`, where its `items` in tools.yaml say they are; a part
    of the path the result lacks contributes none."""

    return [item for path in TOOLS[tool].get("items", []) for item in _walk(result, path)]


def _walk(value: Any, path: str) -> list[Any]:
    found = [value]
    for step in filter(None, path.split(".")):
        found = _step(found, step)
    return [each for each in found if each is not None]


def _step(found: list[Any], step: str) -> list[Any]:
    """One step of a path: a field of each value, and with [] the elements of each list."""

    found = [_field(each, step.removesuffix("[]")) for each in found]
    if not step.endswith("[]"):
        return found
    return [item for each in found if isinstance(each, list) for item in each]


def _field(value: Any, key: str) -> Any:
    if not key:
        return value
    return value.get(key) if isinstance(value, dict) else None


def _split(text: str, separator: str) -> list[str]:
    """`text` split at each `separator` outside brackets and braces, the parts stripped."""

    parts, depth, start = [], 0, 0
    for index, character in enumerate(text):
        depth += (character in "[{") - (character in "]}")
        if character == separator and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    return [part.strip() for part in [*parts, text[start:]] if part.strip()]


def _names(alternative: str) -> list[str]:
    """The parameter names of one alternative: a name, or `{ a, b }`, a set of them."""

    if alternative.startswith("{"):
        return [name for part in _split(alternative[1:-1], ",") for name in _names(part)]
    return re.findall(r"\w+", alternative)[:1]


def parameters(tool: str) -> tuple[set[str], set[str]]:
    """The parameters `tool` takes, by `inputs` in tools.yaml, and those it requires.

    `inputs` reads `(a, b?, c | d, e[]?, f[{ g, h? }])`: a trailing ? marks an optional
    parameter, | alternatives, none of which is required, and brackets a list.
    """

    names: set[str] = set()
    required: set[str] = set()
    for part in _split(TOOLS[tool]["inputs"].strip()[1:-1], ","):
        alternatives = _split(part, "|")
        found = [name for alternative in alternatives for name in _names(alternative)]
        names.update(found)
        if len(alternatives) == 1 and not part.endswith("?"):
            required.update(found)
    return names, required


def is_basis(name: str) -> bool:
    """Whether `name` is a convention (a section such as A6, or a rule such as A3.6.1, or a
    subsection such as A3.6 above rules), a record (provenance) or a required tool."""

    return name in CONVENTIONS or name in RECORDS or name in TOOLS or _is_rule(name)


def _is_rule(name: str) -> bool:
    rules = {rule for section in CONVENTIONS.values() for rule in section["rules"]}
    return any(rule == name or rule.startswith(f"{name}.") for rule in rules)
