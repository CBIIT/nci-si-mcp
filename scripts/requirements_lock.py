"""What the two requirement generators share: PDM versions, name form and the lock line."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pdm_versions(groups: set[str]) -> dict[str, str]:
    """Pinned versions in pdm.lock of the packages that belong to any of `groups`."""
    packages = tomllib.loads((ROOT / "pdm.lock").read_text())["package"]
    return {
        package["name"]: package["version"]
        for package in packages
        if set(package.get("groups", [])) & groups
    }


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_line(requirement: str, digests: Iterable[str]) -> str:
    """One requirement with each `sha256:` digest on its own continuation line."""
    return " \\\n    ".join([requirement, *(f"--hash={digest}" for digest in digests)])
