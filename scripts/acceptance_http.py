"""Run the furnished server through the remote fixture suite, with owned process cleanup.

The harness invokes this file's `state` command as its normal operator state hook. A private
Unix socket hands the fixture settings to the parent, which owns and reaps the server process.
No PID files, detached children or production services are involved.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import socketserver
import subprocess
import sys
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from typing import Any

import yaml

from nci_si_acceptance.client import server_environment, without_nci_si_settings
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures
from nci_si_acceptance.report import load_report
from nci_si_acceptance.suite import index_set, unmatched_requests

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / "acceptance"
FIXTURES = ACCEPTANCE / "fixtures"
REPORT = ACCEPTANCE / "http-fixture.json"
# The remote contract deliberately cannot remove the operator's prepared index.
UNPREPARED = {
    f"tests/test_evs.py::{function}[{argument}]"
    for function, arguments in (
        ("test_an_index_mode_without_an_index_is_unavailable", ("hybrid", "semantic")),
        (
            "test_an_index_mode_for_a_terminology_without_an_index_is_invalid",
            ("none-hybrid", "none-semantic"),
        ),
    )
    for argument in arguments
}


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


class ServerProcess:
    def __init__(
        self, directory: Path, port: int, *, environment: dict[str, str] | None = None
    ) -> None:
        self.directory, self.port = directory, port
        self.environment = dict(os.environ if environment is None else environment)
        self.process: subprocess.Popen[bytes] | None = None
        self.log = (directory / "server.log").open("ab")

    def stop(self) -> None:
        if self.process is None:
            return
        stop(self.process)
        self.process = None

    def close(self) -> None:
        self.stop()
        self.log.close()

    def restart(self, settings: dict[str, str]) -> None:
        self.stop()
        environment = (
            without_nci_si_settings(self.environment)
            | settings
            | {
                "NCI_SI_DATA_DIR": str(self.directory / "index"),
                "NCI_SI_TRANSPORT": "streamable-http",
                "NCI_SI_HTTP_PORT": str(self.port),
                "NCI_SI_HTTP_SESSIONS": "stateful",
                "NCI_SI_HTTP_REQUIRE_INDEX": "1",
            }
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "nci_si_mcp.cli", "serve"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=self.log,
        )


def hook_handler(process: ServerProcess) -> type[socketserver.StreamRequestHandler]:
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            settings = json.loads(self.rfile.readline())
            process.restart(settings)
            self.wfile.write(b"ok\n")

    return Handler


def state(socket_path: str) -> None:
    settings = {
        name: value
        for name, value in os.environ.items()
        if name.startswith("NCI_SI_") and not name.startswith("NCI_SI_ACCEPTANCE_")
    }
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(30)
        path = Path(socket_path).resolve()
        # This hook is a dedicated child; short addresses avoid AF_UNIX's path limit.
        with chdir(path.parent):
            connection.connect(path.name)
        connection.sendall(json.dumps(settings).encode() + b"\n")
        if connection.recv(16) != b"ok\n":
            raise RuntimeError("The fixture server restart failed")


def prepare(directory: Path, *, environment: dict[str, str] | None = None) -> None:
    manifest = yaml.safe_load((FIXTURES / "manifest.yaml").read_text())
    with FixtureServer(load_fixtures(FIXTURES)) as upstream:
        command = [sys.executable, "-m", "nci_si_mcp.cli", "index-sample", *index_set(manifest)]
        settings = server_environment("fixture", directory / "index", upstream.url)
        if environment is not None:
            settings = environment | {
                key: value for key, value in settings.items() if key.startswith("NCI_SI_")
            }
        subprocess.run(  # noqa: S603 - fixture preparation with the project interpreter
            command,
            env=settings,
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=120,
        )
        if unmatched_requests(upstream.log()):
            raise RuntimeError("Index preparation requested an unrecorded upstream response")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def report_skips(tests: dict[str, Any]) -> None:
    skipped = {name for name, row in tests.items() if row["outcome"] == "skipped"}
    print(f"Remote fixture run: {len(tests) - len(skipped)} executed, {len(skipped)} skipped.")
    for name in sorted(skipped):
        print(f"Skipped (remote unprepared contract): {name}")


def verdict(report: dict[str, Any], expected: set[str]) -> bool:
    tests = report["tests"]
    report_skips(tests)
    observed = {name: row["outcome"] for name, row in tests.items()}
    wanted = {name: "skipped" if name in UNPREPARED else "passed" for name in expected}
    return (
        report["transport"] == "streamable-http"
        and observed == wanted
        and not report["failed_gates"]
    )


def run_suite(
    port: int,
    socket_path: Path,
    *,
    report: Path | None = None,
    environment: dict[str, str] | None = None,
) -> int:
    output = REPORT if report is None else report
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), "state", str(socket_path)])
    environment = without_nci_si_settings(os.environ if environment is None else environment) | {
        "NCI_SI_ACCEPTANCE_URL": f"http://127.0.0.1:{port}/mcp",
        "NCI_SI_ACCEPTANCE_MODE": "fixture",
        "NCI_SI_ACCEPTANCE_PREPARED": "1",
        "NCI_SI_ACCEPTANCE_SECURITY_SERVER": shlex.join(
            [sys.executable, str(ROOT / "scripts/permissions_fixture.py")]
        ),
        "NCI_SI_ACCEPTANCE_STATE_HOOK": command,
    }
    output.unlink(missing_ok=True)
    suite = subprocess.Popen(  # noqa: S603 - the existing suite in this checkout
        [sys.executable, "-m", "pytest", "tests", "-q", "-rs", "--report=" + str(output)],
        env=environment,
        cwd=ACCEPTANCE,
    )
    try:
        status = suite.wait()
    finally:
        stop(suite)
    if status or not output.exists():
        return 1
    try:
        observed = load_report(output, "fixture")
    except SystemExit as error:
        print(error, file=sys.stderr)
        return 1
    expected = set(json.loads((ACCEPTANCE / "expected/fixture.json").read_text()))
    return 0 if verdict(observed, expected) else 1


def run(*, report: Path | None = None, environment: dict[str, str] | None = None) -> int:
    (ROOT / "tmp").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="http-", dir=ROOT / "tmp") as temporary:
        directory = Path(temporary)
        prepare(directory, environment=environment)
        port = free_port()
        process = ServerProcess(directory, port, environment=environment)
        socket_path = directory / "control"
        try:
            # The runner owns its process. Restore cwd before starting the control thread.
            with chdir(directory):
                control = socketserver.UnixStreamServer("control", hook_handler(process))
            with control:
                thread = Thread(target=control.serve_forever, daemon=True)
                thread.start()
                try:
                    return run_suite(port, socket_path, report=report, environment=environment)
                finally:
                    control.shutdown()
                    thread.join()
        finally:
            process.close()


def interrupted(_signum: int, _frame: Any) -> None:
    # Unwind both owned processes and the temporary directory on CI cancellation too.
    raise KeyboardInterrupt


if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[1] == "state":
        state(sys.argv[2])
    else:
        signal.signal(signal.SIGTERM, interrupted)
        raise SystemExit(run())
