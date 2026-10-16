"""Plan installation targets and optional components."""

from __future__ import annotations

import argparse
import errno
import json
import platform
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import urlopen

from .config import upstream_base_urls
from .installer_state import (
    InstallerStateError,
    current_status,
    project_status,
    read_state,
    reconcile,
    reverify,
    state_lock,
    validate_outcomes,
    write_state,
)

COMPONENTS = ("server", "index", "documentation", "companion")
INSTALL_STAGES = ("check_prerequisites", "install", "configure", "verify")
NODE_VERSION = re.compile(r"v([0-9]+)(?:\.[0-9]+){2}\Z")
MIN_NODE_MAJOR = 24


@dataclass(frozen=True)
class InstallationPlan:
    """The target, components and eventual installation-stage order."""

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


STEP_HELP = {
    "check_prerequisites": (
        "Step: check_prerequisites\n"
        "Purpose: Check every selected requirement before installation changes anything.\n"
        "Why: A partial installation must not be created when required inputs are missing.\n"
        "Action: Apply every fix reported by `check` or a BLOCKED `run`.\n"
        "Verification: Rerun every selected prerequisite; confirmation alone is never accepted.\n"
        "Common failures: missing tools, occupied ports, or unreachable EVS and caDSR endpoints.\n"
        "Guide: docs/installer-targets.md\n"
    )
}


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
    run.add_argument(
        "--non-interactive",
        action="store_true",
        help="report unresolved actions without prompting",
    )
    status = commands.add_parser(
        "status", help="Reverify and show installation status", color=False
    )
    _selection_arguments(status)
    status.add_argument("--data-dir", type=Path, required=True, help="installation state directory")
    step_help = commands.add_parser(
        "help", help="Explain an executable installation step", color=False
    )
    step_help.add_argument("step", help="executable step name")
    _selection_arguments(step_help)
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


def git_prerequisite() -> Prerequisite:
    def tagged_checkout() -> bool:
        try:
            result = subprocess.run(
                ["git", "describe", "--tags", "--match", "v*", "--abbrev=0"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError, subprocess.TimeoutExpired:
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    return Prerequisite(
        "git", "Install git and fetch release tags as QUICKSTART.md documents.", tagged_checkout
    )


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


def _container_runtimes() -> tuple[str, ...]:
    return tuple(name for name in ("docker", "podman") if shutil.which(name))


def _compose_available() -> bool:
    for runtime in _container_runtimes():
        try:
            result = subprocess.run(  # noqa: S603 - fixed executable selected from PATH
                [runtime, "compose", "version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except FileNotFoundError, subprocess.TimeoutExpired:
            continue
        if result.returncode == 0:
            return True
    return False


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
    except FileNotFoundError, subprocess.TimeoutExpired:
        return False
    matched = NODE_VERSION.fullmatch(result.stdout.strip())
    return (
        result.returncode == 0 and matched is not None and int(matched.group(1)) >= MIN_NODE_MAJOR
    )


def _port_available(port: int) -> bool:
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            return False
        raise
    return True


def local_prerequisites(components: Sequence[str]) -> tuple[Prerequisite, ...]:
    checks = [
        Prerequisite("supported_os", "Run the local installer on macOS or Linux.", _supported_os),
        git_prerequisite(),
        executable_prerequisite("pdm", "Install PDM as QUICKSTART.md documents."),
    ]
    companions = "documentation" in components or "companion" in components
    if companions:
        checks.extend(
            (
                Prerequisite(
                    "container_runtime",
                    "Install Docker or Podman as QUICKSTART.md documents.",
                    lambda: bool(_container_runtimes()),
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
    evs_url, cadsr_url = upstream_base_urls("evs_base_url", "cadsr_base_url")
    checks.extend(
        (
            reachability_prerequisite(
                "evs_reachable",
                evs_url,
                "Restore network access to NCI_SI_EVS_BASE_URL.",
            ),
            reachability_prerequisite(
                "cadsr_reachable",
                cadsr_url,
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

    def _check(self) -> tuple[CheckResult, ...]:
        return run_prerequisites(local_prerequisites(self.components))

    def missing_inputs(self, step: str) -> tuple[str, ...]:
        return tuple(result.fix for result in self._check() if not result.passed)

    def apply(self, step: str) -> None:
        self._check()

    def verify(self, step: str) -> bool:
        return all(result.passed for result in self._check())


def _step_help(step: str) -> str | None:
    return STEP_HELP.get(step)


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
            validate_outcomes(state, actions.step_names)
            current = reverify(state, actions)
            if current != state:
                write_state(args.data_dir, current)
            state = current
    record = (
        current_status(state, actions) if args.command == "run" else project_status(state, actions)
    )
    unresolved = record["blocked"] or record["failed"] or record["pending"]
    return (1 if unresolved else 0), record


def _guided_run(args: argparse.Namespace, plan: InstallationPlan) -> tuple[int, dict[str, object]]:
    code, record = _state_command(args, plan)
    interactive = not args.non_interactive and sys.stdin.isatty()
    while code == 1 and record["blocked"] and interactive:
        blocked = cast("list[dict[str, Any]]", record["blocked"])[0]
        missing = blocked["missing"]
        sys.stderr.write("Resolve these requirements, then press Enter to recheck:\n")
        for fix in missing:
            sys.stderr.write(f"- {fix}\n")
        sys.stderr.write("> ")
        if sys.stdin.readline() == "":
            break
        code, record = _state_command(args, plan)
    return code, record


def _help_command(step: str) -> int:
    text = _step_help(step)
    if text is None:
        sys.stderr.write(f"Step {step!r} is not executable yet; #320 brings later steps.\n")
        return 2
    sys.stdout.write(text)
    return 0


def _state_main(args: argparse.Namespace, plan: InstallationPlan) -> int:
    try:
        code, record = (
            _guided_run(args, plan) if args.command == "run" else _state_command(args, plan)
        )
    except InstallerStateError as error:
        sys.stderr.write(str(error) + "\n")
        return 2
    sys.stdout.write(json.dumps(record, indent=2) + "\n")
    return code


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
    if args.command == "help":
        return _help_command(args.step)
    if args.command in ("run", "status"):
        return _state_main(args, plan)
    record = {
        "target": plan.target,
        "components": plan.components,
        "stages": plan.stages,
    }
    sys.stdout.write(json.dumps(record, indent=2) + "\n")
    return 0
