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
ADVISORY = re.compile(r"^(?:GHSA-[a-z0-9]{4}(?:-[a-z0-9]{4}){2}|CVE-\d{4}-\d{4,}|PYSEC-\d{4}-\d+)$")
type Fetch = Callable[[str], dict[str, Any]]


@dataclass(frozen=True, order=True)
class Package:
    ecosystem: str
    name: str
    version: str


@dataclass(frozen=True)
class SecurityException:
    version: str
    advisory: str


def _python_name(name: str) -> str:
    return NORMALISE_PYTHON_NAME.sub("-", name).lower()


def _pdm_packages(path: Path) -> set[Package]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        Package("pypi", _python_name(row["name"]), row["version"])
        for row in data["package"]
        if "name" in row and "version" in row and not {"path", "url", "git"}.intersection(row)
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

    return sorted({package for packages in inventories(root).values() for package in packages})


def inventories(root: Path) -> dict[str, list[Package]]:
    parsers = {
        Path("pdm.lock"): _pdm_packages,
        **dict.fromkeys(PYTHON_LOCKS, _requirements_packages),
        NPM_LOCK: _npm_packages,
    }
    result = {}
    for relative, parser in parsers.items():
        path = root / relative
        if not path.is_file():
            raise ValueError(f"{relative}: is missing")
        packages = sorted(parser(path))
        if not packages:
            raise ValueError(f"{relative}: contains no registry releases")
        result[str(relative)] = packages
    return result


def parse_exceptions(records: object) -> dict[str, SecurityException]:
    """Validate the temporary security-fix exceptions from project configuration."""

    if not isinstance(records, dict):
        raise ValueError("Dependency age exceptions must be a table")
    return dict(_parse_exception(name, record) for name, record in records.items())


def _parse_exception(raw_name: object, record: object) -> tuple[str, SecurityException]:
    if not isinstance(raw_name, str) or not isinstance(record, dict):
        raise ValueError("Each dependency age exception must be a package table")
    version, advisory = record.get("version"), record.get("advisory")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{raw_name} exception needs a version")
    if not isinstance(advisory, str) or ADVISORY.fullmatch(advisory) is None:
        raise ValueError(f"{raw_name} exception needs a valid advisory")
    return _python_name(raw_name), SecurityException(version, advisory)


def read_exceptions(root: Path = ROOT) -> dict[str, SecurityException]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    records = (
        project.get("tool", {}).get("nci-si", {}).get("dependency-age", {}).get("exceptions", {})
    )
    return parse_exceptions(records)


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
    rows = metadata.get("urls", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return [], "has malformed registry metadata"
    values = [row.get("upload_time_iso_8601") for row in rows]
    return _checked_times(values, "has no distribution upload time")


def _npm_times(package: Package, metadata: dict[str, Any]) -> tuple[list[datetime], str]:
    times = metadata.get("time", {})
    if not isinstance(times, dict):
        return [], "has malformed registry metadata"
    value = times.get(package.version)
    return _checked_times([] if value is None else [value], "has no release upload time")


def _metadata_url(package: Package) -> str:
    encoded = quote(package.name, safe="")
    if package.ecosystem == "pypi":
        return f"https://pypi.org/pypi/{encoded}/{quote(package.version, safe='')}/json"
    return f"https://registry.npmjs.org/{encoded}"


def age_findings(
    packages: list[Package],
    now: datetime,
    fetch: Fetch,
    exceptions: dict[str, SecurityException] | None = None,
) -> list[str]:
    """Explain every release whose age cannot be proved to meet the minimum."""

    findings = []
    cutoff = now - MINIMUM_AGE
    exceptions = exceptions or {}
    for package in dict.fromkeys(packages):
        if finding := _age_finding(package, cutoff, fetch, exceptions.get(package.name)):
            findings.append(finding)
    return findings


def _age_finding(
    package: Package,
    cutoff: datetime,
    fetch: Fetch,
    exception: SecurityException | None,
) -> str:
    label = f"{package.ecosystem}:{package.name}=={package.version}"
    times, error = _release_times(package, fetch)
    if error:
        return f"{label} {error}"
    if exception and package.ecosystem == "pypi" and package.version == exception.version:
        return _exception_finding(label, times, cutoff)
    return (
        f"{label} was uploaded inside the five-day window"
        if any(uploaded > cutoff for uploaded in times)
        else ""
    )


def _exception_finding(label: str, times: list[datetime], cutoff: datetime) -> str:
    return (
        f"{label} is five days old; remove this exception"
        if all(uploaded <= cutoff for uploaded in times)
        else ""
    )


def _release_times(package: Package, fetch: Fetch) -> tuple[list[datetime], str]:
    try:
        metadata = fetch(_metadata_url(package))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [], f"metadata could not be verified ({type(error).__name__})"
    if not isinstance(metadata, dict):
        return [], "has malformed registry metadata"
    return (
        _pypi_times(package, metadata)
        if package.ecosystem == "pypi"
        else _npm_times(package, metadata)
    )


def release_upload_time(package: Package, fetch: Fetch) -> datetime:
    """Newest verified artifact upload time for an exact registry release."""

    times, error = _release_times(package, fetch)
    if error:
        raise ValueError(f"{package.name} upload time could not be read: {error}")
    return max(times)


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


def run(
    packages: list[Package],
    exceptions: dict[str, SecurityException],
    now: datetime,
    fetch: Fetch,
) -> int:
    findings = age_findings(packages, now, fetch, exceptions)
    for finding in findings:
        print(finding, file=sys.stderr)
    return int(bool(findings))


def main() -> int:
    return check(ROOT, read_exceptions(), datetime.now(UTC), fetch_json)


def check(
    root: Path,
    exceptions: dict[str, SecurityException],
    now: datetime,
    fetch: Fetch,
) -> int:
    try:
        locks = inventories(root)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    packages = sorted({package for rows in locks.values() for package in rows})
    status = run(packages, exceptions, now, fetch)
    for name, rows in locks.items():
        print(_summary(name, rows, exceptions))
    return status


def _summary(name: str, packages: list[Package], exceptions: dict[str, SecurityException]) -> str:
    matched = [
        f"{package.name} {package.version}, {exceptions[package.name].advisory}"
        for package in packages
        if package.ecosystem == "pypi"
        and package.name in exceptions
        and package.version == exceptions[package.name].version
    ]
    suffix = f" ({'; '.join(matched)})" if matched else ""
    return f"{name}: {len(packages)} releases checked, {len(matched)} exceptions{suffix}"


if __name__ == "__main__":
    raise SystemExit(main())
