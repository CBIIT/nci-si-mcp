"""Owned local validation workers use a minimal environment, never deployment settings."""

import argparse
import asyncio
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scripts import acceptance_http
from scripts.benchmark import CASES, cases
from scripts.benchmark_http import _save, run_http
from scripts.benchmark_limits import Limits

from nci_si_acceptance.client import server_environment
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures
from nci_si_acceptance.suite import unmatched_requests

FIXTURE_LIMITS = Limits(max_requests=500)


def clean_environment(directory: Path) -> dict[str, str]:
    """Keep runtime discovery and locale, with all writable user paths owned by the run."""
    return {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")
        if key in os.environ
    } | {"HOME": str(directory), "TMPDIR": str(directory)}


def _ready(process: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + 15
    while process.poll() is None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned fixture server did not become ready") from None
            time.sleep(0.05)
    raise RuntimeError("Owned fixture server exited before readiness")


@contextmanager
def _server(directory: Path, settings: dict[str, str]):
    port = acceptance_http.free_port()
    environment = (
        clean_environment(directory)
        | settings
        | {
            "NCI_SI_DATA_DIR": str(directory / "data"),
            "NCI_SI_TRANSPORT": "streamable-http",
            "NCI_SI_HTTP_PORT": str(port),
            "NCI_SI_HTTP_SESSIONS": "stateful",
            "NCI_SI_HTTP_REQUIRE_INDEX": "0",
        }
    )
    with (directory / "server.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "nci_si_mcp.cli", "serve"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        try:
            _ready(process, port)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            acceptance_http.stop(process)


def fixture_benchmark(
    output: Path,
    *,
    limits: Limits = FIXTURE_LIMITS,
    names: list[str] | None = None,
    cancelled: threading.Event | None = None,
) -> dict[str, Any]:
    """Measure only furnished cases against disposable owned HTTP and upstream processes."""
    selected = cases(list(CASES) if names is None else names)
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        TemporaryDirectory(prefix="worker-", dir=output.parent) as temporary,
        FixtureServer(load_fixtures(acceptance_http.FIXTURES)) as upstream,
    ):
        directory = Path(temporary)
        scenarios = tuple(sorted({case["scenario"] for case in selected if case["scenario"]}))
        upstream.activate(*scenarios)
        generated = server_environment("fixture", directory / "data", upstream.url)
        settings = {key: value for key, value in generated.items() if key.startswith("NCI_SI_")}
        settings |= upstream.fixtures.settings_of(scenarios)
        with _server(directory, settings) as target:
            report = asyncio.run(
                run_http(
                    target, (target,), selected, limits, output, fixture=True, cancelled=cancelled
                )
            )
        if unmatched_requests(upstream.log()):
            report.update(complete=False, state="failed", stopReason="runner_error")
            _save(output, report)
            raise RuntimeError("Fixture benchmark requested an unrecorded response")
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=("benchmark-http-fixture", "acceptance-http-fixture"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.profile == "benchmark-http-fixture":
        return 0 if fixture_benchmark(output)["complete"] else 1
    with TemporaryDirectory(prefix="worker-", dir=output.parent) as temporary:
        return acceptance_http.run(report=output, environment=clean_environment(Path(temporary)))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, acceptance_http.interrupted)
    raise SystemExit(main())
