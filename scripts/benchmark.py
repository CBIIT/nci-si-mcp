"""Measure representative MCP calls; fixture timings are not production latency floors."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml

from nci_si_acceptance.client import Mode, Session, open_session, server_environment
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures
from nci_si_acceptance.suite import unmatched_requests
from nci_si_acceptance.suite_identity import identity
from nci_si_mcp.registry import SPECS

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "acceptance/fixtures"
# Let normal upstream retries complete before asking the audit record for their count.
CLIENT_TIMEOUT_SECONDS = 300
CASES = (
    "get_concept",
    "search_concepts",
    "get_data_element",
    "get_form",
    "match_data_elements",
    "search_data_elements",
    "find_data_elements_for_concept",
    "ground_value",
    "expand_cohort",
    "harmonize_data_dictionary",
)


def cases(names: list[str]) -> list[dict[str, Any]]:
    """Reuse the furnished request examples and release pin, without a second copy."""
    examples = yaml.safe_load((ROOT / "acceptance/tests/calls.yaml").read_text())
    manifest = yaml.safe_load((FIXTURES / "manifest.yaml").read_text())
    terminology, _, release = manifest["evs"]["release"].partition("_")
    pins = {"terminology": terminology, "release": release}
    parameters = {spec.name: {p.name for p in spec.parameters} for spec in SPECS}
    return [
        {
            "tool": name,
            "arguments": examples[name]["arguments"]
            | {key: value for key, value in pins.items() if key in parameters[name]},
            "scenario": examples[name].get("scenario"),
        }
        for name in names
    ]


def percentile(values: list[float], fraction: float) -> float:
    if not values or not 0 < fraction <= 1:
        raise ValueError("A percentile needs samples and a fraction in (0, 1]")
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("Timing samples must be finite and nonnegative")
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def completion(log: str, correlation: str, tool: str) -> dict[str, Any]:
    records = map(json.loads, log.splitlines())
    found = [
        row
        for row in records
        if row.get("event") == "call_completed" and row.get("correlationId") == correlation
    ]
    (record,) = found  # Missing or duplicate completion records cannot supply a measurement.
    if record.get("tool") != tool:
        raise ValueError("The correlated completion names another tool")
    if type(record.get("outboundRequests")) is not int or record["outboundRequests"] < 0:
        raise ValueError("The completion must report actual outbound attempts")
    return record


def measure(session: Session, log: Path, case: dict[str, Any]) -> dict[str, Any]:
    correlation = uuid.uuid4().hex
    started = time.perf_counter()
    reply = session.call_tool(
        case["tool"],
        case["arguments"],
        meta={"correlationId": correlation},
    )
    elapsed = (time.perf_counter() - started) * 1000
    record = completion(log.read_text(), correlation, case["tool"])
    content = reply.structured_content
    if content is None or reply.is_error != (record["status"] == "error"):
        raise ValueError("MCP and its completion disagree about the measured result")
    return {
        "elapsedMs": elapsed,
        "resultBytes": len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()),
        "outboundRequests": record["outboundRequests"],
        "responseCode": record["responseCode"],
        "error": reply.is_error,
        "release": record["release"],
    }


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    times = [sample["elapsedMs"] for sample in samples]
    requests = sum(sample["outboundRequests"] for sample in samples)
    return {
        "calls": len(samples),
        "p50Ms": percentile(times, 0.50),
        "p95Ms": percentile(times, 0.95),
        "errorRate": sum(sample["error"] for sample in samples) / len(samples),
        "meanResultBytes": sum(sample["resultBytes"] for sample in samples) / len(samples),
        "meanOutboundRequests": requests / len(samples),
    }


@contextmanager
def opened(environment: dict[str, str]):
    with TemporaryDirectory(prefix="benchmark-", dir=ROOT / "tmp") as temporary:
        directory = Path(temporary)
        log = directory / "audit.jsonl"
        settings = environment | {"NCI_SI_DATA_DIR": str(directory / "data")}
        command = [sys.executable, "-m", "nci_si_mcp.cli", "serve"]
        with (
            log.open("w") as stream,
            open_session(command, settings, stream, timeout=CLIENT_TIMEOUT_SECONDS) as session,
        ):
            yield session, log


def run_case(case: dict[str, Any], environment: dict[str, str], repetitions: int) -> dict[str, Any]:
    cold = []
    for _ in range(repetitions):
        with opened(environment) as (session, log):
            cold.append(measure(session, log, case))
    with opened(environment) as (session, log):
        measure(session, log, case)  # Prime once, excluded from timing and error aggregates.
        warm = [measure(session, log, case) for _ in range(repetitions)]
    return case | {
        "cold": {"summary": summarize(cold), "samples": cold},
        "warm": {"summary": summarize(warm), "samples": warm},
    }


def environment_for(mode: Mode, upstream: FixtureServer | None, case: dict[str, Any]):
    environment = server_environment(mode, ROOT / "tmp", upstream.url if upstream else None)
    if upstream:
        selected = (case["scenario"],) if case["scenario"] else ()
        upstream.activate(*selected)
        upstream.reset()
        environment |= upstream.fixtures.settings_of(selected)
    return environment


def source_digest() -> str:
    digest = hashlib.sha256()
    paths = [*sorted((ROOT / "src/nci_si_mcp").glob("*.py")), Path(__file__)]
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def save(output: Path, report: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")


def run(mode: Mode, names: list[str], repetitions: int, output: Path) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("At least one measured repetition is required")
    (ROOT / "tmp").mkdir(exist_ok=True)
    report: dict[str, Any] = {
        "mode": mode,
        "transport": "stdio",
        "createdAt": datetime.now(UTC).isoformat(),
        "serverVersion": version("nci-si-mcp"),
        "sourceSha256": source_digest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "suite": identity(),
        "repetitions": repetitions,
        "expectedCases": names,
        "complete": False,
        "conditions": {
            "clientTimeoutSeconds": CLIENT_TIMEOUT_SECONDS,
            "cold": "fresh process and data directory per call; startup excluded",
            "warm": "one process after one excluded priming call; actual attempts report caching",
            "percentiles": "nearest rank; tool errors included",
            "resultBytes": "UTF-8 compact structured content, excluding MCP envelope",
            "index": "none; lexical search; no semantic-quality or production latency claim",
        },
        "cases": [],
    }
    save(output, report)  # An interrupted first case must not leave an older successful report.
    fixture = FixtureServer(load_fixtures(FIXTURES)) if mode == "fixture" else nullcontext(None)
    with fixture as upstream:
        for case in cases(names):
            environment = environment_for(mode, upstream, case)
            report["cases"].append(run_case(case, environment, repetitions))
            if upstream and unmatched_requests(upstream.log()):
                raise ValueError("Benchmark requested an unrecorded fixture response")
            save(output, report)
            print(f"{mode}: measured {case['tool']}", flush=True)
    report["complete"] = True
    save(output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--case", choices=CASES, action="append", dest="names")
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.mode, args.names or list(CASES), args.repetitions, args.output)


if __name__ == "__main__":
    main()
