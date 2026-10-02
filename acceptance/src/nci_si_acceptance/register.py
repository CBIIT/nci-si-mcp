"""The register of upstream request forms, generated from the manifest.

    pdm run acceptance-register [--fixtures DIR]

The government furnishes an initial form for every platform operation the tools rest
on: from the published API documentation and what was verified live where the
operation exists, and a draft naming its requirement where it does not. The manifest
holds each form with its operation and rationale; this writes the readable register
from it, one view for the EVS team, one for the caDSR team, and one for both
(`acceptance/request-forms/`). A self-test fails when the files are not current.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from nci_si_acceptance.fixture_server import MANIFEST
from nci_si_acceptance.record import FIXTURES

REGISTER = FIXTURES.parent / "request-forms"
VIEWS = {
    "evs": ("the EVS team", {"evs", "evs-fhir"}),
    "cadsr": ("the caDSR team", {"cadsr", "cadsr-ftp"}),
    "all": ("both teams", None),
}
FALLBACK_HEADER = (
    "| Operation | Prescribed form (crafted) | Served form (recorded) | Release reported |"
)
# Payload fields that name the release or version of the content they carry.
RELEASE_FIELDS = ("version", "sourceTerminologyVersion")
INTRODUCTION = """\
The upstream requests the acceptance suite's fixtures answer, each with the platform operation it
serves (*MCP API Specification* §10, or the requirement where the operation is missing) and why
it has this form. They are initial forms furnished by the government: from the published API
documentation and live checks where the operation exists, a draft where it does not. They are
open to refinement by the service teams and to proposals from the contractors.

Two kinds of request are answered whatever their form: EVS concept requests, by rules over one
recording per concept (below), and requests whose parameters the service is shown to ignore.
"""
RULES = """\
## Concept requests answered by rule

One recording per concept answers each of these in every projection (`include`), batch and
relation list the recordings cover (`acceptance/src/nci_si_acceptance/concepts.py`).

| Operations | Form |
|---|---|
| OP-E05 | `GET /api/v1/concept/{terminology}_{release}/{code}?include=…` |
| OP-E07 | `GET /api/v1/concept/{terminology}_{release}?list=…&include=…`, ≤ 1,000 codes |
| OP-E10, E11, E14 to E17, E20 | `GET /api/v1/concept/{terminology}_{release}/{code}/{relation}` |
"""
FALLBACK = """\
## Operations without a pinned form upstream

These are served unpinned today. The verified fallback is approved for the prototype: the tool
calls the unpinned form, compares the release the payload reports with the one requested, and
fails closed on a difference. NCI's approval under EVS SOW v2.1 item 5 is taken from this list. A
mapset that reports a version of its own, not an NCIt release, cannot be verified that way: it is a
content state of its own, named in provenance and not presented as release-verified.
"""


def _split_form(entry: dict[str, Any]) -> str:
    """A request as the manifest writes it, safe in a table cell."""

    return _cell(f"`GET {entry['surface']} {entry['path']}`")


def _expected(entry: dict[str, Any]) -> str:
    ignored = sorted(entry.get("ignored", {}))
    if ignored == ["*"]:
        return f"{entry.get('status', 200)}; every parameter ignored"
    return f"{entry.get('status', 200)}{'; ignores ' + ', '.join(ignored) if ignored else ''}"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _shown(entry: dict[str, Any], surfaces: set[str] | None) -> bool:
    return surfaces is None or entry["surface"] in surfaces


def _rows(manifest: dict[str, Any], surfaces: set[str] | None) -> list[str]:
    return [
        f"| {entry['operation']} | {_split_form(entry)} | {_expected(entry)} "
        f"| {_cell(entry['rationale'])} | `{entry['fixture']}` |"
        for entry in manifest["record"].get("requests", [])
        if _shown(entry, surfaces)
    ]


def _fallback_rows(manifest: dict[str, Any], root: Path, surfaces: set[str] | None) -> list[str]:
    """The operations served unpinned: each with the pinned form a requirement prescribes,
    where one is crafted, and the release the recorded payload reports."""

    requests = {entry["fixture"]: entry for entry in manifest["record"].get("requests", [])}
    prescribed = {entry["from"]: entry for entry in manifest["record"].get("derived", [])}
    release = manifest["evs"]["release"]
    return [
        _fallback_row(entry, prescribed.get(entry["fixture"]), root)
        for entry in requests.values()
        if _unpinned(entry, release) and _shown(entry, surfaces)
    ]


def _fallback_row(entry: dict[str, Any], pinned: dict[str, Any] | None, root: Path) -> str:
    pinned_form = _split_form(pinned | {"surface": entry["surface"]}) if pinned else "—"
    document = json.loads((root / entry["fixture"]).read_text(encoding="utf-8"))
    releases = ", ".join(_releases(document["response"]["body"])) or "none"
    return f"| {entry['operation']} | {pinned_form} | {_split_form(entry)} | {releases} |"


def _unpinned(entry: dict[str, Any], release: str) -> bool:
    """A recorded request naming no release that is not release discovery."""

    recorded = entry["fixture"].startswith("recorded/")
    discovery = entry["operation"] in ("OP-E01", "baseline")
    return recorded and not discovery and release not in entry["path"]


def _releases(payload: Any) -> list[str]:
    """The versions a payload reports for its content, at its top and one level down."""

    objects = _objects(payload)
    objects += [item for value in _lists(objects) for item in _objects(value)]
    return sorted({str(item[key]) for item in objects for key in RELEASE_FIELDS if key in item})


def _objects(payload: Any) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else [payload]
    return [item for item in items if isinstance(item, dict)]


def _lists(objects: list[dict[str, Any]]) -> list[list[Any]]:
    return [value for item in objects for value in item.values() if isinstance(value, list)]


def _table(header: str, rows: list[str]) -> list[str]:
    if not rows:
        return ["None yet.", ""]
    columns = header.count("|") - 1
    return [header, "|" + "---|" * columns, *rows, ""]


def render(root: Path = FIXTURES) -> dict[str, str]:
    """Each view of the register, by file name."""

    manifest = yaml.safe_load((root / MANIFEST).read_text(encoding="utf-8"))
    views = {}
    for name, (audience, surfaces) in VIEWS.items():
        lines = [
            f"# Upstream request forms, for {audience}",
            "",
            f"Generated from `acceptance/fixtures/{MANIFEST}` by `pdm run acceptance-register`;"
            " do not edit by hand.",
            "",
            INTRODUCTION,
            "## Requests",
            "",
            *_table(
                "| Operation | Request | Expected | Rationale | Fixture |",
                _rows(manifest, surfaces),
            ),
        ]
        if surfaces is None or "evs" in surfaces:
            lines.append(RULES)
        lines += [
            FALLBACK,
            *_table(FALLBACK_HEADER, _fallback_rows(manifest, root, surfaces)),
        ]
        views[f"{name}.md"] = "\n".join(lines).rstrip() + "\n"
    return views


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the register of upstream request forms.")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES, help="the fixture directory")
    options = parser.parse_args(arguments)
    target = options.fixtures.parent / "request-forms"
    target.mkdir(exist_ok=True)
    for name, text in render(options.fixtures).items():
        (target / name).write_text(text, encoding="utf-8")
    sys.stdout.write(f"Wrote the register to {target}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
