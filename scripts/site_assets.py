"""Reviewed local web assets shared by the documentation site and the portal."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, NamedTuple

# Resolved from this file, so the portal finds the assets from any working directory.
ASSETS_DIR = Path(__file__).resolve().parents[1] / "docs/site-assets"
FOOTER_SOURCE = ASSETS_DIR / "zensical.toml"
STYLESHEETS = ("ncids.css", "portal.css")
FONT_FILES = (
    "poppins-regular.ttf",
    "poppins-semibold.ttf",
    "open-sans.ttf",
    "roboto-mono.ttf",
    "poppins-OFL.txt",
    "open-sans-OFL.txt",
    "roboto-mono-OFL.txt",
    "sources.json",
)
# What the Admin image carries beside the fonts: the portal's stylesheets and its footer data.
PORTAL_FILES = (*STYLESHEETS, FOOTER_SOURCE.name)


class FooterLink(NamedTuple):
    label: str
    url: str


class Footer(NamedTuple):
    contact_url: str
    agencies: tuple[FooterLink, ...]
    policies: tuple[FooterLink, ...]


def _url(path: Path, value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise ValueError(f"{path}: footer URLs must be https:// strings")
    return value


def _links(path: Path, table: dict[str, Any], key: str) -> tuple[FooterLink, ...]:
    entries = table.get(key)
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: [project.extra.footer] needs a non-empty list {key}")
    links = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("label"), str):
            raise ValueError(f"{path}: every footer link in {key} needs a label")
        links.append(FooterLink(entry["label"], _url(path, entry.get("url"))))
    return tuple(links)


def load_footer() -> Footer:
    """The footer links of the documentation configuration, validated; a missing or malformed
    table is an error naming the file, never an empty footer."""
    try:
        config = tomllib.loads(FOOTER_SOURCE.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{FOOTER_SOURCE}: not valid TOML ({error})") from error
    table = config.get("project", {}).get("extra", {}).get("footer")
    if not isinstance(table, dict):
        raise ValueError(f"{FOOTER_SOURCE}: the [project.extra.footer] table is missing")
    return Footer(
        _url(FOOTER_SOURCE, table.get("contact_url")),
        _links(FOOTER_SOURCE, table, "agencies"),
        _links(FOOTER_SOURCE, table, "policies"),
    )
