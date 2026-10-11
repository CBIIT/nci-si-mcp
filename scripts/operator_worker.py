"""Owned local validation workers use a minimal environment, never deployment settings."""

import argparse
import asyncio
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scripts import acceptance_http
from scripts.benchmark import CASES, cases
from scripts.benchmark_http import run_http
from scripts.benchmark_limits import Limits
from scripts.operator_files import write_json
from scripts.processes import exit_on_sigterm

from nci_si_acceptance.client import OwnedServer, server_environment
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures
from nci_si_acceptance.suite import unmatched_requests

FIXTURE_LIMITS = Limits()


def clean_environment(directory: Path, *, source: Path | None = None) -> dict[str, str]:
    """Keep runtime discovery and locale, with all writable user paths owned by the run."""
    source = acceptance_http.ROOT if source is None else source
    return {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")
        if key in os.environ
    } | {
        "HOME": str(directory),
        "TMPDIR": str(directory),
        "PYTHONDONTWRITEBYTECODE": "1",
        # Always derive these paths from this worker's source; never inherit caller PYTHONPATH.
        "PYTHONPATH": os.pathsep.join(
            str(source / name) for name in ("src", "acceptance/src", ".")
        ),
    }


@contextmanager
def _server(directory: Path, settings: dict[str, str]):
    with OwnedServer(
        clean_environment(directory),
        directory / "server.log",
        data_dir=directory / "data",
        require_index=False,
    ) as server:
        server.start(settings)
        yield server.url


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
            write_json(output, report, form="report")
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
    exit_on_sigterm()
    raise SystemExit(main())
