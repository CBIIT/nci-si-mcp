"""The readable specification, rendered from `spec/`, its source of record.

    pdm run spec-render

writes `docs/specification.md`: an introduction, the conventions, the required tools, the
requirements with the suite tests that cite each, and the acceptance rules. The two prose
parts are `spec/introduction.md` and `spec/acceptance.md`; everything else comes from the
data. A self-test fails when the file is not current. A Word copy is built from it with
pandoc where one is wanted; neither is edited by hand.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pytest

from nci_si_acceptance.requirements import citations, load_requirements
from nci_si_acceptance.spec import CONVENTIONS, RECORDS, SPEC, TOOLS

DOCUMENT = SPEC.parent / "docs" / "specification.md"
SUITE = Path(__file__).parents[2] / "tests"
GROUPS = {"evs": "EVS", "cadsr": "caDSR", "cross-domain": "Cross-domain", "workflow": "Workflow"}
# The requirements, in the order the suite runs them: the gates, the tests every
# content-returning tool takes, and each tool's own, by group.
SECTIONS = {
    "P": "Protocol gates: once per server, before any tool test",
    "X": "Cross-cutting: against every content-returning tool",
    **{group: f"{title} tools" for group, title in GROUPS.items()},
}


def _text(name: str) -> str:
    """A hand-written part of the document: the introduction, and the acceptance rules."""

    return (SPEC / name).read_text(encoding="utf-8")


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _conventions() -> list[str]:
    lines = ["## 1. Conventions", "", "Binding on every tool.", ""]
    for key, section in CONVENTIONS.items():
        rows = [f"| {rule} | {_cell(text)} |" for rule, text in section["rules"].items()]
        lines += [f"### {key} · {section['title']}", "", "| Id | Convention |", "|---|---|"]
        lines += [*rows, ""]
        lines += [f"*Why {rule}.* {_cell(text)}\n" for rule, text in section.get("why", {}).items()]
    return lines + [line for record in RECORDS.values() for line in _record(record)]


def _values(field: dict[str, Any]) -> str:
    values = field.get("values")
    listed = f": one of {', '.join(f'`{value}`' for value in values)}" if values else ""
    sets = field.get("exclusions", {})
    excluded = "".join(
        f"; exclusion set of {key}: {', '.join(codes)}" for key, codes in sets.items()
    )
    forms = "".join(
        f"; the {name} form `{text.partition(':')[0]}`: {_cell(text.partition(':')[2])}"
        for name, text in field.get("forms", {}).items()
    )
    return listed + excluded + forms + (" (optional)" if field.get("optional") else "")


def _record(record: dict[str, Any]) -> list[str]:
    """A record of the specification as a table of its fields."""

    rows = [
        f"| `{name}` | {_cell(field['content'])}{_values(field)} | {field['of']} |"
        for name, field in record["fields"].items()
    ]
    header = [f"### {record['title']}", "", _cell(record["about"]), ""]
    return [*header, "| Field | Content | Rule |", "|---|---|---|", *rows, ""]


def _bound(bound: dict[str, Any]) -> str:
    default = f"default {bound['default']}, " if "default" in bound else ""
    return f"{default}at most {bound['maximum']}"


def _limits(tool: dict[str, Any]) -> str:
    """What bounds a tool's call: its bounded arguments, stated defaults, request bound and
    what it does not offer."""

    limits = "".join(
        f" `{argument}`: {_bound(bound)}." for argument, bound in tool.get("bounds", {}).items()
    )
    limits += "".join(
        f" `{argument}`: default {value}." for argument, value in tool.get("defaults", {}).items()
    )
    limits += f" At most {tool['requests']} upstream requests a call." if "requests" in tool else ""
    limits += (
        " Computed from caller-supplied values: ttlMs 0, private." if tool.get("computed") else ""
    )
    limits += "".join(
        f" `{argument}`: at most {count} a call."
        for argument, count in tool.get("lists", {}).items()
    )
    limits += "".join(
        f" Not offered, `{key}`: {', '.join(values)}."
        for key, values in tool.get("not_offered", {}).items()
    )
    return limits + _forms(tool)


def _forms(tool: dict[str, Any]) -> str:
    """The forms a tool's identifier arguments take, and its free-text arguments (A7.6, A7.7)."""

    forms = "".join(
        f" `{argument}` form{_by_terminology(form)}."
        for argument, form in tool.get("patterns", {}).items()
    )
    texts = tool.get("free_text", [])
    return forms + (f" Free text: {', '.join(f'`{path}`' for path in texts)}." if texts else "")


def _by_terminology(form: str | dict[str, str]) -> str:
    if isinstance(form, str):
        return f" `{form}`"
    return "".join(f" for {terminology} `{pattern}`" for terminology, pattern in form.items())


def _tool_row(name: str, tool: dict[str, Any]) -> str:
    values = "".join(
        f" `{argument}`: {', '.join(choices)}."
        for argument, choices in tool.get("values", {}).items()
    )
    signature = _cell(f"{tool['inputs']} → {tool['returns']}")
    items = (
        f" Items: {', '.join(f'`{path}`' for path in tool['items'])}." if "items" in tool else ""
    )
    return f"| `{name}` | `{signature}` | {_cell(tool['summary'])}{values}{_limits(tool)}{items} |"


def _tools() -> list[str]:
    lines = ["## 2. Tools", ""]
    for group, title in GROUPS.items():
        rows = [_tool_row(name, tool) for name, tool in TOOLS.items() if tool["group"] == group]
        lines += [f"### {title} tools", "", "| Tool | Inputs → result | What it does |"]
        lines += ["|---|---|---|", *rows, ""]
    return lines


def _status(key: str, entry: dict[str, Any], tests: dict[str, list[str]]) -> str:
    cited = ", ".join(f"`{test}`" for test in tests.get(key, []))
    planned = f"planned {entry['planned']}" if "planned" in entry else ""
    return " | ".join([cited or "—", planned or "tested"])


def _section(key: str) -> str:
    """The section a requirement belongs to: P, X, or its tool's group."""

    prefix = key.rpartition("-")[0]
    return prefix if prefix in ("P", "X") else TOOLS[prefix]["group"]


def _requirements(cited: dict[str, tuple[str, ...]]) -> list[str]:
    tests: dict[str, list[str]] = {}
    for test, keys in cited.items():
        for key in keys:
            tests.setdefault(key, []).append(test)
    requirements = load_requirements()
    lines = ["## 3. Requirements", ""]
    for section, title in SECTIONS.items():
        rows = [
            f"| {key} | {_cell(entry['statement'])} | {', '.join(entry['basis'])} "
            f"| {_status(key, entry, tests)} |"
            for key, entry in requirements.items()
            if _section(key) == section
        ]
        lines += [f"### {title}", "", "| Id | Requirement | Basis | Tests | Status |"]
        lines += ["|---|---|---|---|---|", *rows, ""]
    return lines


def render(cited: dict[str, tuple[str, ...]]) -> str:
    """The specification, given the requirements each suite test cites."""

    lines = [_text("introduction.md"), *_conventions(), *_tools(), *_requirements(cited)]
    lines.append(_text("acceptance.md"))
    return "\n".join(lines).rstrip() + "\n"


class _Collected:
    """A pytest plugin that keeps the collected items."""

    items: list[pytest.Item]

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.items = session.items


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the specification from spec/.")
    parser.add_argument("--output", type=Path, default=DOCUMENT, help="where to write it")
    options = parser.parse_args(arguments)
    collected = _Collected()
    code = pytest.main([str(SUITE), "--collect-only", "-q", "-p", "no:cacheprovider"], [collected])
    if code != pytest.ExitCode.OK:
        return int(code)
    options.output.write_text(render(citations(collected.items)), encoding="utf-8")
    sys.stdout.write(f"Wrote {options.output}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
