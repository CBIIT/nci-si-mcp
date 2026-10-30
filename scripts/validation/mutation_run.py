"""Owned scratch snapshots and deterministic child-pytest evidence classification."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import current_thread, main_thread
from time import monotonic

from coverage import CoverageData
from pytest import ExitCode
from scripts.processes import exit_on_sigterm, stop

DOMAINS = {
    "unit": (".", "tests"),
    "selftest": ("acceptance", "selftests"),
    "fixture": ("acceptance", "tests"),
}
FIXTURE = {
    "NCI_SI_ACCEPTANCE_MODE": "fixture",
    "NCI_SI_ACCEPTANCE_REGISTRY_RELEASE": "required",
    "NCI_SI_ACCEPTANCE_SECURITY_SERVER": "python ../scripts/permissions_fixture.py",
    "NCI_SI_ACCEPTANCE_PREPARE": 'nci-si-mcp index-sample $(cat "$NCI_SI_ACCEPTANCE_INDEX_CODES")',
}


@contextmanager
def snapshot(root: Path):
    with tempfile.TemporaryDirectory(prefix="mutation-") as temporary:
        scratch = Path(temporary).resolve() / "source"
        if (root / ".git").exists():
            subprocess.run(  # noqa: S603 - fixed git command, owned paths
                ["git", "clone", "--quiet", "--no-hardlinks", str(root), str(scratch)],  # noqa: S607
                check=True,
                capture_output=True,
            )
        shutil.copytree(
            root,
            scratch,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                "__pycache__",
                ".coverage*",
                ".pytest_cache",
                ".nci-si-mcp",
                "tmp",
                "node_modules",
                ".ruff_cache",
            ),
        )
        # A separate object database/index prevents child git operations reaching the caller.
        subprocess.run(["git", "init", "-q", str(scratch)], check=True, capture_output=True)  # noqa: S603,S607
        yield scratch


def environment(scratch: Path, work: Path) -> dict[str, str]:
    prefixes = ("COVERAGE", "PYTEST", "GIT_", "NCI_SI_", "PDM_", "MUTATION_", "SELFTEST_")
    env = {key: value for key, value in os.environ.items() if not key.startswith(prefixes)}
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "NCI_SI_DATA_DIR": str(work / "data"),
            # tempfile owns unique child directories; nesting its root under evidence breaks
            # macOS AF_UNIX's 104-byte socket path limit in HTTP integration tests.
            "TMPDIR": tempfile.gettempdir(),
            "TMP": tempfile.gettempdir(),
            "TEMP": tempfile.gettempdir(),
            "PATH": os.pathsep.join((str(Path(sys.executable).parent), env.get("PATH", ""))),
        }
    )
    # PYTHONPATH reaches both imports in the pytest interpreter and installed console children.
    paths = [scratch / "src", scratch / "acceptance/src", scratch, Path(__file__).parents[2]]
    env["PYTHONPATH"] = os.pathsep.join(map(str, paths))
    return env


@contextmanager
def runner_termination():
    # Nested pytest workers normally die on SIGTERM without unwinding. Their child
    # sessions escape the scheduler's group, so the waiting owner must clean them.
    if current_thread() is not main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    try:
        exit_on_sigterm()
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def program(command: list[str], cwd: Path, env: dict[str, str], timeout: float, log: Path):
    with runner_termination(), log.open("wb") as stream:
        process = subprocess.Popen(  # noqa: S603 - fixed Python executable and argument arrays
            command,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        owned_group = False
        try:
            owned_group = os.getpgid(process.pid) == process.pid
            if not owned_group:
                raise ValueError(f"Child {process.pid} does not own its process group")
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        finally:
            # Never translate a broken launch contract into a signal to another
            # group. The direct Popen child is still ours to terminate and reap.
            stop(process, group=owned_group)


def classify(status: int | None, evidence: Path, mutated_file: Path | None = None) -> dict:
    if status is None:
        return {"outcome": "TIMEOUT"}
    rows = (
        [json.loads(line) for line in evidence.read_text().splitlines()]
        if evidence.exists()
        else []
    )
    return evidence_outcome(status, rows, mutated_file)


def evidence_outcome(status: int, rows: list[dict], mutated_file: Path | None = None) -> dict:
    failed = [row for row in rows if row["outcome"] == "failed"]
    kill = call_kill(status, failed) or import_kill(status, failed, mutated_file)
    if kill:
        return kill
    if status not in (0, 5) or failed:
        return {"outcome": "INFRA", "status": status, "failures": failed}
    return successful_calls(rows)


def successful_calls(rows: list[dict]) -> dict:
    calls = [row for row in rows if (row["phase"], row["outcome"]) == ("call", "passed")]
    return {"outcome": "SURVIVED" if calls else "NO_TESTS"}


def call_kill(status: int, failed: list[dict]) -> dict:
    if status == 1 and len(failed) == 1 and failed[0]["phase"] == "call":
        return {
            "outcome": "KILLED",
            "node": failed[0]["node"],
            "phase": "call",
            "kind": "assertion" if failed[0]["assertion"] else "error",
        }
    return {}


def import_kill(status: int, failed: list[dict], mutated_file: Path | None) -> dict:
    if (
        status not in (ExitCode.INTERRUPTED, ExitCode.USAGE_ERROR)
        or not mutated_file
        or len(failed) != 1
    ):
        return {}
    row = failed[0]
    if row["phase"] != "collection" or row.get("exception", "").startswith("SystemExit"):
        return {}
    if str(mutated_file.resolve()) not in row.get("traceback_paths", []):
        return {}
    return {"outcome": "KILLED", "kind": "import", "phase": "collection", "node": row["node"]}


def run_domain(
    scratch: Path,
    domain: str,
    selected: list[str],
    timeout: float,
    *,
    baseline: bool = False,
    mutated_file: Path | None = None,
) -> tuple[dict, Path]:
    directory, default = DOMAINS[domain]
    work = Path(tempfile.mkdtemp(prefix=f"evidence-{domain}-", dir=scratch.parent))
    # Keep instrumentation independent of the subject, including probe self-mutations.
    shutil.copyfile(Path(__file__).with_name("mutation_probe.py"), work / "_mutation_evidence.py")
    env = environment(scratch, work)
    evidence = work / "events.jsonl"
    env["MUTATION_EVIDENCE"] = str(evidence)
    env["COVERAGE_FILE"] = str(work / ".coverage")
    args = [
        sys.executable,
        "-c",
        f"import sys; sys.path.insert(0, {str(work)!r}); "
        "import pytest; raise SystemExit(pytest.main(sys.argv[1:]))",
        "-p",
        "_mutation_evidence",
        "-n",
        "4" if baseline and domain in ("selftest", "fixture") else "0",
        "-x",
        "--rootdir",
        str(scratch / directory),
        *(selected or [default]),
    ]
    args += coverage_args(scratch, work, baseline, env)
    if domain == "fixture":
        env.update(FIXTURE)
        args += ["--report=fixture.json"]
    log = work / "pytest.log"
    started = monotonic()
    status = program(args, scratch / directory, env, timeout, log)
    elapsed = monotonic() - started
    result = classify(status, evidence, mutated_file)
    if baseline and result["outcome"] == "KILLED":
        result["outcome"] = "INFRA"
    result.update(domain=domain, log=log.read_text(errors="replace"), elapsed_seconds=elapsed)
    return result, work / ".coverage"


def coverage_args(scratch: Path, work: Path, baseline: bool, env: dict[str, str]) -> list[str]:
    if not baseline:
        return ["--no-cov"]
    config = work / ".coveragerc"
    env["COVERAGE_PROCESS_START"] = str(config)
    sources = [str(scratch / name) for name in ("src", "scripts", "acceptance/src")]
    config.write_text(
        "[run]\nbranch = True\ncore = ctrace\npatch = subprocess\n"
        "context = ${MUTATION_COVERAGE_CONTEXT-}\n"
        + "source =\n    "
        + "\n    ".join(sources)
        + "\n"
    )
    return [
        f"--cov-config={config}",
        *(f"--cov={source}" for source in sources),
        "--cov-context=test",
        "--cov-fail-under=0",
        "--cov-report=",
    ]


def coverage_map(data_file: Path, scratch: Path) -> dict[str, dict[int, list[str]]]:
    if not data_file.exists():
        raise ValueError(f"Missing baseline coverage: {data_file}")
    data = CoverageData(basename=str(data_file))
    data.read()
    result = {}
    for name in data.measured_files():
        path = Path(name).resolve()
        if path.is_relative_to(scratch):
            result[path.relative_to(scratch).as_posix()] = data.contexts_by_lineno(name)
    return result


def baseline(root: Path, timeout: float, *, timings: list[dict] | None = None) -> dict:
    result = {}
    with snapshot(root) as scratch:
        for domain in ("unit", "selftest", "fixture"):
            outcome, data = run_domain(scratch, domain, [], timeout, baseline=True)
            if timings is not None:
                timings.append(
                    {key: outcome[key] for key in ("domain", "outcome", "elapsed_seconds")}
                )
            if outcome["outcome"] != "SURVIVED":
                raise ValueError(f"Baseline {domain} failed: {outcome}")
            result[domain] = coverage_map(data, scratch)
    return result


def selection(mapping: dict, file: str, line: int, end_line: int) -> dict[str, list[str]]:
    result = {}
    for domain, files in mapping.items():
        lines = files.get(file, {})
        contexts = line_contexts(lines, line, end_line)
        if not contexts:
            continue
        if "" in contexts:
            # Collection imports cannot identify every consumer of constants or decorators.
            contexts = {context for values in lines.values() for context in values}
        result[domain] = context_tests(contexts)
    return result


def context_tests(contexts: set[str]) -> list[str]:
    tests = set()
    for context in contexts:
        if not context:
            continue
        node, _, phase = context.rpartition("|")
        if "::" not in node or phase not in {"run", "setup", "teardown"}:
            raise ValueError(f"Unrecognized coverage context: {context!r}")
        if phase == "run":
            tests.add(node)
    # Import/setup/teardown alone have no call owner and retain the whole-domain fallback.
    return sorted(tests)


def line_contexts(lines: dict, start: int, end: int) -> set[str]:
    return {
        context
        for number, values in lines.items()
        if start <= int(number) <= end
        for context in values
    }
