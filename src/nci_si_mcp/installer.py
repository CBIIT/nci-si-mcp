"""Plan installation targets and optional components."""

from __future__ import annotations

import argparse
import errno
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import urlopen

from . import __version__
from .config import UpstreamConfigurationError, upstream_base_urls
from .installer_state import (
    InstallerStateError,
    current_status,
    read_state,
    reconcile,
    reverify,
    state_lock,
    validate_outcomes,
    write_state,
)
from .validation import PROFILES

COMPONENTS = ("server", "index", "documentation", "companion")
INSTALL_STAGES = ("check_prerequisites", "install", "configure", "verify")
NODE_VERSION = re.compile(r"v([0-9]+)(?:\.[0-9]+){2}\Z")
MIN_NODE_MAJOR = 24
HTTP_OK = 200
RELEASE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
OWNED_CHILDREN = ("venv", "server-data", "runtime")


@dataclass(frozen=True)
class InstallationPlan:
    target: str
    components: tuple[str, ...]
    stages: tuple[str, ...] = INSTALL_STAGES


@dataclass(frozen=True)
class Prerequisite:
    name: str
    fix: str
    observe: Callable[[], bool]


@dataclass(frozen=True)
class CheckResult:
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
        "Guide: https://github.com/CBIIT/nci-si-mcp/blob/main/docs/installer-targets.md\n"
    ),
    "install": (
        "Step: install\n"
        "Purpose: Create the owned virtual environment and install this exact release.\n"
        "Why: A local installation must not drift to another tag or source.\n"
        "Action: Run with a released X.Y.Z installer and working Git access.\n"
        "Verification: Installed metadata names X.Y.Z and requested revision vX.Y.Z.\n"
        "Common failures: development installer version, Git access, or package installation.\n"
        "Guide: https://github.com/CBIIT/nci-si-mcp/blob/main/docs/installer-targets.md\n"
    ),
    "configure": (
        "Step: configure\nPurpose: Write the owned local server configuration.\n"
        "Why: The transport, profile and data path must remain bound to this installation.\n"
        "Action: Select stdio or loopback HTTP and an EVS/caDSR profile.\n"
        "Verification: The recorded configuration exactly matches the selected values.\n"
        "Common failures: inaccessible installation paths.\n"
        "Guide: https://github.com/CBIIT/nci-si-mcp/blob/main/docs/installer-targets.md\n"
    ),
    "verify": (
        "Step: verify\nPurpose: Verify the installed local server boundary.\n"
        "Why: Installed files alone do not prove that the server can start.\n"
        "Action: Free loopback port 8000 when HTTP is selected.\n"
        "Verification: stdio metadata/configuration or HTTP /ready succeeds.\n"
        "Common failures: an unowned port listener or an invalid installed release.\n"
        "Guide: https://github.com/CBIIT/nci-si-mcp/blob/main/docs/installer-targets.md\n"
    ),
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
    _local_arguments(run)
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
    _local_arguments(status)
    status.add_argument("--data-dir", type=Path, required=True, help="installation state directory")
    for name, help_text in (
        ("start", "Start the installed local HTTP server"),
        ("stop", "Stop the installed local HTTP server"),
        ("uninstall", "Remove the recorded local installation"),
    ):
        command = commands.add_parser(name, help=help_text, color=False)
        _selection_arguments(command)
        _local_arguments(command)
        command.add_argument(
            "--data-dir", type=Path, required=True, help="installation state directory"
        )
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


def _local_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="local server transport (default: stdio)",
    )
    command.add_argument(
        "--profile",
        choices=tuple(sorted(PROFILES)),
        default="unified",
        help="EVS/caDSR tool profile (default: unified)",
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
                cwd=Path.cwd(),
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


class LocalActions:
    """Reconcile one owned local server installation."""

    step_names = ("check_prerequisites", "install", "configure", "verify")

    def __init__(
        self,
        data_dir: Path,
        components: tuple[str, ...],
        *,
        transport: str,
        profile: str,
    ) -> None:
        self.data_dir = data_dir
        self.components = components
        self.transport = transport
        self.profile = profile

    @property
    def venv(self) -> Path:
        return self.data_dir / "venv"

    @property
    def python(self) -> Path:
        return self.venv / "bin" / "python"

    @property
    def runtime(self) -> Path:
        return self.data_dir / "runtime"

    def _checks(self) -> tuple[CheckResult, ...]:
        return run_prerequisites(local_prerequisites(self.components))

    def missing_inputs(self, step: str) -> tuple[str, ...]:
        handlers = {
            "check_prerequisites": self._missing_prerequisites,
            "install": self._missing_release,
            "verify": self._missing_http_port,
        }
        return handlers.get(step, self._no_missing)()

    def _missing_prerequisites(self) -> tuple[str, ...]:
        return tuple(result.fix for result in self._checks() if not result.passed)

    @staticmethod
    def _missing_release() -> tuple[str, ...]:
        if not RELEASE_VERSION.fullmatch(__version__):
            return ("Run a released installer whose version is exactly X.Y.Z.",)
        return ()

    def _missing_http_port(self) -> tuple[str, ...]:
        if self._unowned_http_port():
            return ("Free loopback port 8000; an unowned process holds it.",)
        return ()

    @staticmethod
    def _no_missing() -> tuple[str, ...]:
        return ()

    def _unowned_http_port(self) -> bool:
        if self.transport != "http" or _port_available(8000):
            return False
        return not _owned_process(self.runtime)

    def apply(self, step: str) -> None:
        if step == "check_prerequisites":
            self._checks()
        elif step == "install":
            self._install()
        elif step == "configure":
            self._configure()

    def verify(self, step: str) -> bool:
        if step == "check_prerequisites":
            return all(result.passed for result in self._checks())
        if step == "install":
            return _installed_release(self.venv) == (__version__, f"v{__version__}")
        if step == "configure":
            return _configuration(self.data_dir) == self.environment()
        if step == "verify":
            return self._verify_server()
        return False

    def _install(self) -> None:
        if not self.python.is_file():
            subprocess.run(  # noqa: S603 - fixed running interpreter and owned destination
                [sys.executable, "-m", "venv", str(self.venv)], check=True
            )
        reference = f"nci-si-mcp[server] @ git+https://github.com/CBIIT/nci-si-mcp@v{__version__}"
        subprocess.run(  # noqa: S603 - executable is the owned venv Python
            [str(self.python), "-m", "pip", "install", reference], check=True
        )

    def environment(self) -> dict[str, str]:
        environment = {
            "NCI_SI_DATA_DIR": str(self.data_dir / "server-data"),
            "NCI_SI_PROFILE": self.profile,
            "NCI_SI_TRANSPORT": "stdio" if self.transport == "stdio" else "streamable-http",
        }
        if self.transport == "http":
            environment |= {
                "NCI_SI_HTTP_HOST": "127.0.0.1",
                "NCI_SI_HTTP_AUTH_MODE": "trusted-local",
                "NCI_SI_HTTP_REQUIRE_INDEX": "0",
            }
        return environment

    def _configure(self) -> None:
        (self.data_dir / "server-data").mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)
        (self.runtime / "server.json").write_text(
            json.dumps(self.environment(), sort_keys=True) + "\n", encoding="utf-8"
        )

    def _verify_server(self) -> bool:
        if self.transport == "stdio":
            return (
                self.verify("install") and self.verify("configure") and _verify_stdio(self.python)
            )
        if not self.missing_inputs("verify"):
            return _verify_http(self.python, self.runtime, self.environment())
        return False


def _configuration(data_dir: Path) -> dict[str, str] | None:
    try:
        value = json.loads((data_dir / "runtime" / "server.json").read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    if isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        return cast("dict[str, str]", value)
    return None


def _installed_release(venv: Path) -> tuple[str, str] | None:
    records = tuple(venv.glob("lib/python*/site-packages/nci_si_mcp-*.dist-info"))
    if len(records) != 1:
        return None
    try:
        metadata = Parser().parsestr((records[0] / "METADATA").read_text(encoding="utf-8"))
        direct = json.loads((records[0] / "direct_url.json").read_text(encoding="utf-8"))
        revision = direct["vcs_info"]["requested_revision"]
    except OSError, json.JSONDecodeError, KeyError, TypeError:
        return None
    version = metadata.get("Version")
    return (version, revision) if isinstance(version, str) and isinstance(revision, str) else None


def _process_path(runtime: Path) -> Path:
    return runtime / "server-process.json"


def _owned_process(runtime: Path) -> int | None:
    try:
        record = json.loads(_process_path(runtime).read_text(encoding="utf-8"))
        pid, token = record["pid"], record["token"]
        executable = shutil.which("ps")
        if executable is None:
            return None
        observed = subprocess.run(  # noqa: S603 - resolved system process inspector
            [executable, "eww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, TypeError:
        return None
    marker = f"INSTALLER_PROCESS_TOKEN={token}"
    return (
        pid
        if isinstance(pid, int) and observed.returncode == 0 and marker in observed.stdout
        else None
    )


def _verify_http(python: Path, runtime: Path, environment: dict[str, str]) -> bool:
    if _owned_process(runtime):
        return _ready()
    token = uuid.uuid4().hex
    process_environment = os.environ | environment | {"INSTALLER_PROCESS_TOKEN": token}
    process = subprocess.Popen(  # noqa: S603 - executable is inside the owned venv
        [str(python.with_name("nci-si-mcp")), "serve", "--transport", "streamable-http"],
        env=process_environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _process_path(runtime).write_text(
        json.dumps({"pid": process.pid, "token": token}) + "\n", encoding="utf-8"
    )
    try:
        return _poll_ready(process)
    finally:
        process.terminate()
        process.wait(timeout=30)
        _process_path(runtime).unlink(missing_ok=True)


def _verify_stdio(python: Path) -> bool:
    program = (
        "import asyncio\n"
        "import sys\n"
        "from mcp.client import Client\n"
        "async def check():\n"
        "    async with Client([sys.argv[1], 'serve']) as client:\n"
        "        await client.list_tools()\n"
        "asyncio.run(check())"
    )
    result = subprocess.run(  # noqa: S603 - executable is the owned venv Python
        [str(python), "-c", program, str(python.with_name("nci-si-mcp"))],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    return result.returncode == 0


def _ready() -> bool:
    try:
        with urlopen("http://127.0.0.1:8000/ready", timeout=5) as response:
            return response.status == HTTP_OK
    except OSError:
        return False


def _poll_ready(process: subprocess.Popen[bytes]) -> bool:
    for _ in range(30):
        if process.poll() is not None:
            return False
        if _ready():
            return True
        time.sleep(1)
    return False


def _step_help(step: str) -> str | None:
    return STEP_HELP.get(step)


def _state_command(
    args: argparse.Namespace, plan: InstallationPlan
) -> tuple[int, dict[str, object]]:
    actions = LocalActions(
        args.data_dir,
        plan.components,
        transport=args.transport,
        profile=args.profile,
    )
    if args.command == "run":
        state = reconcile(
            args.data_dir,
            plan.target,
            plan.components,
            actions,
            restart=args.restart,
            transport=args.transport,
            profile=args.profile,
        )
    else:
        read_state(
            args.data_dir,
            plan.target,
            plan.components,
            transport=args.transport,
            profile=args.profile,
        )
        with state_lock(args.data_dir):
            state = read_state(
                args.data_dir,
                plan.target,
                plan.components,
                transport=args.transport,
                profile=args.profile,
            )
            validate_outcomes(state, actions.step_names)
            current = reverify(state, actions)
            if current != state:
                write_state(args.data_dir, current)
            state = current
    record = current_status(state, actions)
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
    except (InstallerStateError, UpstreamConfigurationError) as error:
        sys.stderr.write(str(error) + "\n")
        return 2
    sys.stdout.write(json.dumps(record, indent=2) + "\n")
    return code


def _uninstall(args: argparse.Namespace, plan: InstallationPlan) -> int:
    try:
        with state_lock(args.data_dir):
            read_state(
                args.data_dir,
                plan.target,
                plan.components,
                transport=args.transport,
                profile=args.profile,
            )
            if _owned_process(args.data_dir / "runtime"):
                sys.stderr.write("BLOCKED: run `nci-si-install stop` before uninstall.\n")
                return 1
            for child in OWNED_CHILDREN:
                path = args.data_dir / child
                if path.is_symlink():
                    raise InstallerStateError(f"Refusing unowned path {path}.")
                shutil.rmtree(path, ignore_errors=False) if path.exists() else None
            (args.data_dir / "installer-state.json").unlink()
    except (InstallerStateError, OSError) as error:
        sys.stderr.write(str(error) + "\n")
        return 2
    sys.stdout.write('{"status": "uninstalled"}\n')
    return 0


def _lifecycle(args: argparse.Namespace, plan: InstallationPlan) -> int:
    try:
        state = read_state(
            args.data_dir,
            plan.target,
            plan.components,
            transport=args.transport,
            profile=args.profile,
        )
        actions = LocalActions(
            args.data_dir,
            plan.components,
            transport=state.transport,
            profile=state.profile,
        )
        if args.command == "start":
            if state.transport == "stdio":
                record = {"command": str(actions.python.with_name("nci-si-mcp")), "args": ["serve"]}
            else:
                record = _start_http(actions)
        else:
            record = _stop_http(actions)
    except (InstallerStateError, OSError) as error:
        sys.stderr.write(str(error) + "\n")
        return 2
    sys.stdout.write(json.dumps(record) + "\n")
    return 0


def _start_http(actions: LocalActions) -> dict[str, object]:
    if _owned_process(actions.runtime):
        return {"status": "running"}
    if not _port_available(8000):
        raise InstallerStateError("Loopback port 8000 is held by an unowned process.")
    token = uuid.uuid4().hex
    process = subprocess.Popen(  # noqa: S603 - executable is inside the owned venv
        [str(actions.python.with_name("nci-si-mcp")), "serve", "--transport", "streamable-http"],
        env=os.environ | actions.environment() | {"INSTALLER_PROCESS_TOKEN": token},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _process_path(actions.runtime).write_text(
        json.dumps({"pid": process.pid, "token": token}) + "\n", encoding="utf-8"
    )
    return {"status": "started", "pid": process.pid}


def _stop_http(actions: LocalActions) -> dict[str, object]:
    pid = _owned_process(actions.runtime)
    if pid is None:
        return {"status": "stopped"}
    os.kill(pid, 15)
    _process_path(actions.runtime).unlink(missing_ok=True)
    return {"status": "stopped"}


def _check_main(plan: InstallationPlan) -> int:
    try:
        code, record = _check_record(plan)
    except UpstreamConfigurationError as error:
        sys.stderr.write(str(error) + "\n")
        return 2
    sys.stdout.write(json.dumps(record, indent=2) + "\n")
    return code


def _dispatch(args: argparse.Namespace, plan: InstallationPlan) -> int:
    if args.command == "check":
        return _check_main(plan)
    if args.command == "help":
        return _help_command(args.step)
    if args.command == "uninstall":
        return _uninstall(args, plan)
    if args.command in ("start", "stop"):
        return _lifecycle(args, plan)
    if args.command in ("run", "status"):
        return _state_main(args, plan)
    record = {
        "target": plan.target,
        "components": plan.components,
        "stages": plan.stages,
    }
    sys.stdout.write(json.dumps(record, indent=2) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    blocked = _blocked(args)
    if blocked:
        sys.stderr.write(blocked + "\n")
        return 2
    return _dispatch(args, InstallationPlan(args.target, _components(args)))
