"""Fail when a committed registry dependency is less than five days old."""

import json
import re
import sys
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
MINIMUM_AGE = timedelta(days=5)
PYTHON_LOCKS = (
    Path("container/requirements-amd64.txt"),
    Path("container/companion-requirements.txt"),
)
NPM_LOCK = Path("docs/site-assets/package-lock.json")
NORMALISE_PYTHON_NAME = re.compile(r"[-_.]+")
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")
type Fetch = Callable[[str], dict[str, Any]]


@dataclass(frozen=True, order=True)
class Package:
    ecosystem: str
    name: str
    version: str


def _python_name(name: str) -> str:
    return NORMALISE_PYTHON_NAME.sub("-", name).lower()


def _pdm_packages(path: Path) -> set[Package]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        Package("pypi", _python_name(row["name"]), row["version"])
        for row in data["package"]
        if "name" in row and "version" in row
    }


def _requirements_packages(path: Path) -> set[Package]:
    packages = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := PIN.match(line):
            packages.add(Package("pypi", _python_name(match[1]), match[2]))
    return packages


def _npm_packages(path: Path) -> set[Package]:
    records = json.loads(path.read_text(encoding="utf-8"))["packages"]
    return {
        Package("npm", key.rsplit("node_modules/", 1)[1], row["version"])
        for key, row in records.items()
        if "node_modules/" in key and "version" in row
    }


def locked_packages(root: Path = ROOT) -> list[Package]:
    """Unique registry name/version pairs in every committed dependency lock."""

    packages = _pdm_packages(root / "pdm.lock")
    for path in PYTHON_LOCKS:
        packages.update(_requirements_packages(root / path))
    packages.update(_npm_packages(root / NPM_LOCK))
    return sorted(packages)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _checked_times(values: list[object], missing: str) -> tuple[list[datetime], str]:
    if not values:
        return [], missing
    times = [_timestamp(value) for value in values]
    if any(value is None for value in times):
        return [], "has an invalid upload time"
    return [value for value in times if value is not None], ""


def _pypi_times(package: Package, metadata: dict[str, Any]) -> tuple[list[datetime], str]:
    values = [row.get("upload_time_iso_8601") for row in metadata.get("urls", [])]
    return _checked_times(values, "has no distribution upload time")


def _npm_times(package: Package, metadata: dict[str, Any]) -> tuple[list[datetime], str]:
    value = metadata.get("time", {}).get(package.version)
    return _checked_times([] if value is None else [value], "has no release upload time")


def _metadata_url(package: Package) -> str:
    encoded = quote(package.name, safe="")
    if package.ecosystem == "pypi":
        return f"https://pypi.org/pypi/{encoded}/{quote(package.version, safe='')}/json"
    return f"https://registry.npmjs.org/{encoded}"


def age_findings(packages: list[Package], now: datetime, fetch: Fetch) -> list[str]:
    """Explain every release whose age cannot be proved to meet the minimum."""

    findings = []
    cutoff = now - MINIMUM_AGE
    for package in dict.fromkeys(packages):
        label = f"{package.ecosystem}:{package.name}=={package.version}"
        try:
            metadata = fetch(_metadata_url(package))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            findings.append(f"{label} metadata could not be verified ({type(error).__name__})")
            continue
        times, error = (
            _pypi_times(package, metadata)
            if package.ecosystem == "pypi"
            else _npm_times(package, metadata)
        )
        if error:
            findings.append(f"{label} {error}")
        elif any(uploaded > cutoff for uploaded in times):
            findings.append(f"{label} was uploaded inside the five-day window")
    return findings


def fetch_json(url: str) -> dict[str, Any]:
    """Read public package metadata over the fixed HTTPS registry URLs."""

    if not url.startswith("https://"):
        raise ValueError("Registry metadata URL must use HTTPS")
    request = urllib.request.Request(  # noqa: S310 - HTTPS is required immediately above.
        url, headers={"User-Agent": "nci-si-mcp-dependency-age"}
    )
    with urllib.request.urlopen(  # noqa: S310 - the Request contains the checked HTTPS URL.
        request, timeout=30
    ) as response:
        return json.load(response)


def main() -> int:
    findings = age_findings(locked_packages(), datetime.now(UTC), fetch_json)
    for finding in findings:
        print(finding, file=sys.stderr)
    return bool(findings)


if __name__ == "__main__":
    raise SystemExit(main())
