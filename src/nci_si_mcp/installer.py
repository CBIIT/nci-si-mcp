"""Plan installation targets and optional components."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from .config import BASE_URL_VARIABLES, PRODUCTION_BASE_URLS
from .installer_state import (
    InstallerStateError,
    project_status,
    read_state,
    reconcile,
    reverify,
    state_lock,
    write_state,
)

COMPONENTS = ("server", "index", "documentation", "companion")
INSTALL_STAGES = ("check_prerequisites", "install", "configure", "verify")
NODE_VERSION = re.compile(r"v([0-9]+)(?:\.[0-9]+){2}\Z")
MIN_NODE_MAJOR = 24


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
    run = commands.add_parser("run", help="Run and record verified installation steps", color=False)
    _selection_arguments(run)
    run.add_argument("--data-dir", type=Path, required=True, help="installation state directory")
    run.add_argument("--restart", action="store_true", help="reset progress for the same selection")
    status = commands.add_parser(
        "status", help="Reverify and show installation status", color=False
    )
    _selection_arguments(status)
    status.add_argument("--data-dir", type=Path, required=True, help="installation state directory")
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
    if args.command in ("check", "run", "status") and args.target == "aws":
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
        except OSError:
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
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _node_available() -> bool:
    try:
        result = subprocess.run(
            ["node", "--version"],  # noqa: S607
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    matched = NODE_VERSION.fullmatch(result.stdout.strip())
    return (
        result.returncode == 0 and matched is not None and int(matched.group(1)) >= MIN_NODE_MAJOR
    )


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
        executable_prerequisite("git", "Install git with release tags as QUICKSTART.md documents."),
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
            )
        )
        if "documentation" in components:
            checks.append(Prerequisite("node", "Install Node.js 24 or newer.", _node_available))
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


class PrerequisiteActions:
    """The prerequisite stage as the first production reconciliation action."""

    step_names: tuple[str, ...] = ("check_prerequisites",)

    def __init__(self, components: tuple[str, ...]) -> None:
        self.components = components
        self.results: tuple[CheckResult, ...] = ()

    def _check(self) -> tuple[CheckResult, ...]:
        self.results = run_prerequisites(local_prerequisites(self.components))
        return self.results

    def missing_inputs(self, step: str) -> tuple[str, ...]:
        return tuple(result.fix for result in self._check() if not result.passed)

    def apply(self, step: str) -> None:
        self._check()

    def verify(self, step: str) -> bool:
        return all(result.passed for result in self._check())


def _state_command(
    args: argparse.Namespace, plan: InstallationPlan
) -> tuple[int, dict[str, object]]:
    actions = PrerequisiteActions(plan.components)
    if args.command == "run":
        state = reconcile(
            args.data_dir,
            plan.target,
            plan.components,
            actions,
            restart=args.restart,
        )
    else:
        read_state(args.data_dir, plan.target, plan.components)
        with state_lock(args.data_dir):
            state = read_state(args.data_dir, plan.target, plan.components)
            current = reverify(state, actions)
            if current != state:
                write_state(args.data_dir, current)
            state = current
    record = project_status(state, actions)
    return (0 if not record["blocked"] and not record["pending"] else 1), record


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
    if args.command in ("run", "status"):
        try:
            code, record = _state_command(args, plan)
        except InstallerStateError as error:
            sys.stderr.write(str(error) + "\n")
            return 2
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
