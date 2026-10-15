"""Plan installation targets and optional components."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .config import BASE_URL_VARIABLES, PRODUCTION_BASE_URLS

COMPONENTS = ("server", "index", "documentation", "companion")
INSTALL_STAGES = ("check_prerequisites", "install", "configure", "verify")
MIN_COMPONENT_DISK_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class InstallationPlan:
    """The target, components and ordered stages an installation will run."""

    target: str
    components: tuple[str, ...]
    stages: tuple[str, ...] = INSTALL_STAGES


@dataclass(frozen=True)
class Prerequisite:
    """One read-only observation and the static action that repairs its failure."""

    name: str
    fix: str
    observe: Callable[[], bool]


@dataclass(frozen=True)
class CheckResult:
    """The safe result of one prerequisite observation."""

    name: str
    passed: bool
    fix: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nci-si-install",
        description=(
            "Plan installation of the NCI Semantic Infrastructure MCP server and optional "
            "components."
        ),
        color=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Print the ordered installation plan", color=False)
    _selection_arguments(plan)
    check = commands.add_parser(
        "check", help="Check every prerequisite before installation", color=False
    )
    _selection_arguments(check)
    return parser


def _selection_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--target", choices=("local", "aws", "cloud-one"), required=True, help="installation target"
    )
    command.add_argument(
        "--with-index",
        action="store_true",
        help="include an existing NCIt index and its embedding runtime",
    )
    command.add_argument(
        "--with-documentation", action="store_true", help="include the static documentation site"
    )
    command.add_argument(
        "--with-companion", action="store_true", help="include the validation companion"
    )


def _components(args: argparse.Namespace) -> tuple[str, ...]:
    selected = [COMPONENTS[0]]
    for enabled, component in (
        (args.with_index, COMPONENTS[1]),
        (args.with_documentation, COMPONENTS[2]),
        (args.with_companion, COMPONENTS[3]),
    ):
        if enabled:
            selected.append(component)
    return tuple(selected)


def _blocked(args: argparse.Namespace) -> str | None:
    if args.target == "cloud-one":
        return "BLOCKED: the Cloud One target awaits the consultation decisions in #292."
    if args.target == "aws" and args.with_companion:
        return "BLOCKED: AWS validation administration awaits approved platform protection in #197."
    if args.command == "check" and args.target == "aws":
        return "BLOCKED: standard-AWS prerequisite checks arrive with #324."
    return None


def run_prerequisites(checks: Sequence[Prerequisite]) -> tuple[CheckResult, ...]:
    """Run every check in order; observers own classification of expected failures."""

    return tuple(CheckResult(check.name, check.observe(), check.fix) for check in checks)


def result_record(result: CheckResult) -> dict[str, str]:
    record = {"name": result.name, "status": "passed" if result.passed else "failed"}
    if not result.passed:
        record["fix"] = result.fix
    return record


def executable_prerequisite(name: str, fix: str) -> Prerequisite:
    return Prerequisite(name, fix, lambda: shutil.which(name) is not None)


def reachability_prerequisite(name: str, url: str, fix: str) -> Prerequisite:
    def reachable() -> bool:
        try:
            with urlopen(url, timeout=5):  # noqa: S310 - operator-configured HTTP(S) endpoint
                return True
        except HTTPError:
            return True
        except OSError, URLError:
            return False

    return Prerequisite(name, fix, reachable)


def _supported_os() -> bool:
    return platform.system() in ("Darwin", "Linux")


def _container_runtime() -> str | None:
    return next((name for name in ("docker", "podman") if shutil.which(name)), None)


def _compose_available() -> bool:
    runtime = _container_runtime()
    if runtime is None:
        return False
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable selected from PATH
            [runtime, "compose", "version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except FileNotFoundError, OSError:
        return False
    return result.returncode == 0


def _disk_available() -> bool:
    try:
        return shutil.disk_usage(Path.cwd()).free >= MIN_COMPONENT_DISK_BYTES
    except OSError:
        return False


def _port_available(port: int) -> bool:
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
    except OSError:
        return False
    return True


def _base_url(field: str) -> str:
    return os.getenv(BASE_URL_VARIABLES[field], PRODUCTION_BASE_URLS[field])


def local_prerequisites(components: Sequence[str]) -> tuple[Prerequisite, ...]:
    checks = [
        Prerequisite("supported_os", "Run the local installer on macOS or Linux.", _supported_os),
        executable_prerequisite("pdm", "Install PDM as QUICKSTART.md documents."),
    ]
    companions = "documentation" in components or "companion" in components
    if companions:
        checks.extend(
            (
                Prerequisite(
                    "container_runtime",
                    "Install Docker or Podman as QUICKSTART.md documents.",
                    lambda: _container_runtime() is not None,
                ),
                Prerequisite(
                    "compose",
                    "Install Compose for the selected container runtime.",
                    _compose_available,
                ),
                Prerequisite(
                    "disk_space",
                    "Free at least 2 GiB in the installation working directory.",
                    _disk_available,
                ),
            )
        )
        if "documentation" in components:
            checks.append(executable_prerequisite("node", "Install Node.js 24 or newer."))
            checks.append(
                Prerequisite(
                    "port_8080",
                    "Free loopback port 8080 for documentation.",
                    lambda: _port_available(8080),
                )
            )
        if "companion" in components:
            checks.append(
                Prerequisite(
                    "port_8081",
                    "Free loopback port 8081 for the companion.",
                    lambda: _port_available(8081),
                )
            )
    checks.extend(
        (
            reachability_prerequisite(
                "evs_reachable",
                _base_url("evs_base_url"),
                "Restore network access to NCI_SI_EVS_BASE_URL.",
            ),
            reachability_prerequisite(
                "cadsr_reachable",
                _base_url("cadsr_base_url"),
                "Restore network access to NCI_SI_CADSR_BASE_URL.",
            ),
        )
    )
    return tuple(checks)


def _check_record(plan: InstallationPlan) -> tuple[int, dict[str, object]]:
    results = run_prerequisites(local_prerequisites(plan.components))
    passed = all(result.passed for result in results)
    return (0 if passed else 1), {
        "target": plan.target,
        "components": plan.components,
        "status": "passed" if passed else "failed",
        "results": [result_record(result) for result in results],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    blocked = _blocked(args)
    if blocked:
        sys.stderr.write(blocked + "\n")
        return 2
    plan = InstallationPlan(args.target, _components(args))
    if args.command == "check":
        code, record = _check_record(plan)
        sys.stdout.write(json.dumps(record, indent=2) + "\n")
        return code
    record = {
        "target": plan.target,
        "components": plan.components,
        "stages": plan.stages,
    }
    sys.stdout.write(json.dumps(record, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
