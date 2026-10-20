"""Install and operate the local NCI SI MCP server."""

from __future__ import annotations

import argparse
import errno
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
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
    InstallationState,
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

COMPONENTS = ("server",)
INSTALL_STAGES = ("check_prerequisites", "install", "configure", "verify")
HTTP_OK = 200
HTTP_PORT = 8000
RELEASE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
OWNED_CHILDREN = ("venv", "server-data", "runtime")
OWNED_MARKER = ".installer-owned"


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
        f"Action: Free loopback port {HTTP_PORT} when HTTP is selected.\n"
        "Verification: installed stdio initializes and lists tools, or HTTP /ready succeeds.\n"
        "Common failures: an unowned port listener or an invalid installed release.\n"
        "Guide: https://github.com/CBIIT/nci-si-mcp/blob/main/docs/installer-targets.md\n"
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nci-si-install",
        description=("Install and operate the local NCI Semantic Infrastructure MCP server."),
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
        ("start", "Start HTTP or print the installed stdio client configuration"),
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
    command.add_argument("--target", choices=("local",), required=True, help="installation target")


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


def run_prerequisites(checks: Sequence[Prerequisite]) -> tuple[CheckResult, ...]:
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


def _port_available(port: int) -> bool:
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            return False
        raise
    return True


def local_prerequisites() -> tuple[Prerequisite, ...]:
    checks = [
        Prerequisite("supported_os", "Run the local installer on macOS or Linux.", _supported_os),
        executable_prerequisite("git", "Install Git so pip can install the released source tag."),
        Prerequisite(
            "python",
            "Run the installer with Python 3.14 or newer.",
            lambda: sys.version_info >= (3, 14),
        ),
    ]
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
    results = run_prerequisites(local_prerequisites())
    passed = all(result.passed for result in results)
    return (0 if passed else 1), {
        "target": plan.target,
        "components": plan.components,
        "status": "passed" if passed else "failed",
        "results": [result_record(result) for result in results],
    }


class LocalActions:
    """Reconcile one owned local server installation."""

    step_names = ("check_prerequisites", "install", "configure", "verify")

    def __init__(
        self,
        data_dir: Path,
        *,
        transport: str,
        profile: str,
    ) -> None:
        self.data_dir = data_dir
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
        return run_prerequisites(local_prerequisites())

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
            return (f"Free loopback port {HTTP_PORT}; an unowned process holds it.",)
        return ()

    @staticmethod
    def _no_missing() -> tuple[str, ...]:
        return ()

    def _unowned_http_port(self) -> bool:
        if self.transport != "http" or _port_available(HTTP_PORT):
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
        _claim_child(self.venv)
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
                "NCI_SI_HTTP_PORT": str(HTTP_PORT),
                "NCI_SI_HTTP_AUTH_MODE": "trusted-local",
                "NCI_SI_HTTP_REQUIRE_INDEX": "0",
            }
        return environment

    def _configure(self) -> None:
        _claim_child(self.data_dir / "server-data")
        _claim_child(self.runtime)
        (self.runtime / "server.json").write_text(
            json.dumps(self.environment(), sort_keys=True) + "\n", encoding="utf-8"
        )

    def _verify_server(self) -> bool:
        if self.transport == "stdio":
            if not self.verify("install") or not self.verify("configure"):
                return False
            _verify_stdio(self.python, self.runtime, self.environment())
            return True
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


def _claim_child(path: Path) -> None:
    if os.path.lexists(path) and (path.is_symlink() or not (path / OWNED_MARKER).is_file()):
        raise InstallerStateError(f"Refusing unowned path {path}.")
    path.mkdir(parents=True, exist_ok=True)
    (path / OWNED_MARKER).touch(exist_ok=True)


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
    path = _process_path(runtime)
    if not os.path.lexists(path):
        return None
    pid, identity = _process_record(path)
    observed = _inspect_process(pid)
    if observed.returncode != 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            path.unlink(missing_ok=True)
            return None
        except PermissionError:
            # Existence without inspection is not absence; preserve the record and fail closed.
            pass
        raise InstallerStateError(
            "Cannot verify server process ownership; inspect the runtime record."
        )
    if identity != _normalized_identity(observed.stdout):
        raise InstallerStateError(
            "Cannot verify server process ownership; inspect the runtime record."
        )
    return pid


def _process_record(path: Path) -> tuple[int, str]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if set(record) != {"pid", "identity"}:
            raise ValueError
        pid, identity = record["pid"], record["identity"]
        if type(pid) is not int or pid < 1 or not isinstance(identity, str) or not identity:
            raise ValueError
    except OSError, json.JSONDecodeError, KeyError, TypeError, ValueError:
        raise InstallerStateError(
            "Cannot verify server process ownership; inspect the runtime record."
        ) from None
    return pid, identity


def _inspect_process(pid: int) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("ps")
    if executable is None:
        raise InstallerStateError(
            "Cannot verify server process ownership; inspect the runtime record."
        )
    try:
        return subprocess.run(  # noqa: S603 - resolved system process inspector
            [executable, "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        raise InstallerStateError(
            "Cannot verify server process ownership; inspect the runtime record."
        ) from None


def _verify_http(python: Path, runtime: Path, environment: dict[str, str]) -> bool:
    if _owned_process(runtime):
        return _ready()
    command = str(python.with_name("nci-si-mcp"))
    with _server_log(runtime).open("ab") as log:
        process = subprocess.Popen(  # noqa: S603 - executable is inside the owned venv
            [command, "serve", "--transport", "streamable-http"],
            env=os.environ | environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
    try:
        return _poll_ready(process)
    finally:
        _terminate(process)


def _terminate(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)


def _write_process_record(runtime: Path, pid: int, command: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".server-process.", dir=runtime)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": pid, "identity": command}, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, _process_path(runtime))
        directory = os.open(runtime, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)


def _verify_stdio(python: Path, runtime: Path, environment: dict[str, str]) -> None:
    program = (
        "import asyncio\n"
        "import os\n"
        "import sys\n"
        "from mcp.client import Client\n"
        "from mcp.client.stdio import StdioServerParameters, stdio_client\n"
        "async def check():\n"
        "    env = {k: v for k, v in os.environ.items() if k.startswith('NCI_SI_')}\n"
        "    server = StdioServerParameters(command=sys.argv[1], args=['serve'], env=env)\n"
        "    transport = stdio_client(server)\n"
        "    async with Client(transport) as client:\n"
        "        await client.list_tools()\n"
        "asyncio.run(check())"
    )
    log_path = _server_log(runtime)
    try:
        log = log_path.open("ab")
    except OSError as error:
        raise InstallerStateError(
            f"Cannot open stdio verification log {log_path}: {error}."
        ) from error
    try:
        with log:
            try:
                result = subprocess.run(  # noqa: S603 - executable is the owned venv Python
                    [str(python), "-c", program, str(python.with_name("nci-si-mcp"))],
                    env=os.environ | environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                    timeout=30,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise InstallerStateError(
                    f"Installed stdio server verification timed out; inspect {log_path}."
                ) from None
            except OSError as error:
                raise InstallerStateError(
                    f"Installed stdio server verification could not launch: {error}."
                ) from error
    except InstallerStateError:
        raise
    except OSError as error:
        raise InstallerStateError(
            f"Cannot write stdio verification log {log_path}: {error}."
        ) from error
    if result.returncode:
        raise InstallerStateError(
            f"Installed stdio server verification failed; inspect {log_path}."
        )


def _server_log(runtime: Path) -> Path:
    return runtime / "server.log"


def _process_identity(pid: int) -> str:
    for _ in range(30):
        observed = _inspect_process(pid)
        identity = _normalized_identity(observed.stdout)
        if not observed.returncode and identity:
            return identity
        time.sleep(0.1)
    raise InstallerStateError("Cannot record server process ownership; retry `start`.")


def _normalized_identity(value: str) -> str:
    return " ".join(value.split())


def _ready() -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{HTTP_PORT}/ready", timeout=5) as response:
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
        sys.stderr.write(f"Step {step!r} is not executable.\n")
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
            state = read_state(
                args.data_dir,
                plan.target,
                plan.components,
                transport=args.transport,
                profile=args.profile,
            )
            validate_outcomes(state, LocalActions.step_names)
            paths = _owned_paths(args.data_dir)
            if _owned_process(args.data_dir / "runtime"):
                sys.stderr.write("BLOCKED: run `nci-si-install stop` before uninstall.\n")
                return 1
            _remove_owned_paths(paths)
            (args.data_dir / "installer-state.json").unlink()
    except (InstallerStateError, OSError) as error:
        sys.stderr.write(str(error) + "\n")
        return 2
    sys.stdout.write('{"status": "uninstalled"}\n')
    return 0


def _owned_paths(data_dir: Path) -> tuple[Path, ...]:
    paths = tuple(data_dir / child for child in OWNED_CHILDREN)
    for path in paths:
        if path.is_symlink() or (path.exists() and not (path / OWNED_MARKER).is_file()):
            raise InstallerStateError(f"Refusing unowned path {path}.")
    return paths


def _remove_owned_paths(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if path.exists():
            shutil.rmtree(path, ignore_errors=False)


def _lifecycle(args: argparse.Namespace, plan: InstallationPlan) -> int:
    try:
        with state_lock(args.data_dir):
            state = read_state(
                args.data_dir,
                plan.target,
                plan.components,
                transport=args.transport,
                profile=args.profile,
            )
            actions = LocalActions(args.data_dir, transport=state.transport, profile=state.profile)
            validate_outcomes(state, actions.step_names)
            record = _lifecycle_record(args.command, state, actions)
    except (InstallerStateError, OSError) as error:
        sys.stderr.write(str(error) + "\n")
        return 2
    sys.stdout.write(json.dumps(record) + "\n")
    return 0


def _lifecycle_record(
    command: str, state: InstallationState, actions: LocalActions
) -> dict[str, object]:
    if command == "stop":
        if state.transport != "http":
            raise InstallerStateError("The stdio transport has no process to stop.")
        return _stop_http(actions)
    return _start_record(state, actions)


def _start_record(state: InstallationState, actions: LocalActions) -> dict[str, object]:
    _require_startable(state, actions)
    if state.transport == "stdio":
        return {
            "command": str(actions.python.with_name("nci-si-mcp")),
            "args": ["serve"],
            "env": actions.environment(),
        }
    return _start_http(actions)


def _require_startable(state: InstallationState, actions: LocalActions) -> None:
    if not _completed_installation(state, actions):
        raise InstallerStateError("Installation is incomplete; complete `run` before `start`.")
    if not _current_installation(state, actions):
        raise InstallerStateError("Installation is stale; complete `run` before `start`.")


def _completed_installation(state: InstallationState, actions: LocalActions) -> bool:
    completed = tuple(outcome.step for outcome in state.outcomes if outcome.status == "completed")
    return completed == actions.step_names


def _current_installation(state: InstallationState, actions: LocalActions) -> bool:
    common = actions.verify("install") and actions.verify("configure")
    return common and (state.transport != "stdio" or actions.verify("verify"))


def _start_http(actions: LocalActions) -> dict[str, object]:
    if _owned_process(actions.runtime):
        if not _ready():
            raise InstallerStateError("The owned HTTP server is not ready; run `stop` and retry.")
        return {"status": "running"}
    if not _port_available(HTTP_PORT):
        raise InstallerStateError(f"Loopback port {HTTP_PORT} is held by an unowned process.")
    command = str(actions.python.with_name("nci-si-mcp"))
    log_path = _server_log(actions.runtime)
    with log_path.open("ab") as log:
        process = subprocess.Popen(  # noqa: S603 - executable is inside the owned venv
            [
                command,
                "serve",
                "--transport",
                "streamable-http",
            ],
            env=os.environ | actions.environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            start_new_session=True,
        )
    try:
        _write_process_record(actions.runtime, process.pid, _process_identity(process.pid))
    except OSError, InstallerStateError:
        _terminate(process)
        raise
    if not _poll_ready(process):
        _terminate(process)
        _process_path(actions.runtime).unlink(missing_ok=True)
        raise InstallerStateError(f"The HTTP server did not become ready; inspect {log_path}.")
    return {"status": "started", "pid": process.pid}


def _stop_http(actions: LocalActions) -> dict[str, object]:
    pid = _owned_process(actions.runtime)
    if pid is None:
        return {"status": "stopped"}
    os.kill(pid, signal.SIGTERM)
    _wait_for_pid(pid)
    _process_path(actions.runtime).unlink(missing_ok=True)
    return {"status": "stopped"}


def _wait_for_pid(pid: int) -> None:
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(1)
    os.kill(pid, signal.SIGKILL)
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(1)
    raise InstallerStateError("Cannot stop the owned HTTP server; inspect the recorded process.")


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
    return _dispatch(args, InstallationPlan(args.target, COMPONENTS))
