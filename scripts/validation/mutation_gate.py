"""Deterministic changed-site mutation gate. JSON lines retain every outcome and kill owner."""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from time import monotonic

from scripts.processes import exit_on_sigterm
from scripts.validation.mutation_run import (
    baseline,
    environment,
    program,
    run_domain,
    selection,
    snapshot,
)
from scripts.validation.mutation_source import Mutant, allowlist, candidates, digest, scope


class SchedulerError(ValueError):
    """A failed controller, carrying all completed and explicitly missing results."""

    def __init__(self, message: str, rows: list[dict]):
        super().__init__(message)
        self.rows = rows


def execute_job(path: Path, index: int) -> None:
    started = monotonic()
    jobs = json.loads(path.read_text())
    item = jobs["mutants"][index]
    source = jobs["sources"][item["file"]].encode()
    mutant = Mutant(
        **{key: value for key, value in item.items() if key != "selected"},
        before=source[item["start"] : item["end"]].decode(),
    )
    restored = False
    try:
        with snapshot(Path(jobs["root"])) as scratch:
            target = scratch / mutant.file
            original = target.read_bytes()
            try:
                target.write_bytes(
                    original[: mutant.start] + mutant.after.encode() + original[mutant.end :]
                )
                result = measure(scratch, item["selected"], jobs["timeout"], mutated_file=target)
            finally:
                target.write_bytes(original)
                if digest(target.read_bytes().hex()) != digest(original.hex()):
                    raise ValueError(f"Scratch restoration failed: {mutant.file}")
                restored = True
    except (OSError, subprocess.SubprocessError) as error:
        result = {"outcome": "INFRA", "message": str(error), "domains": []}
    row = {
        **asdict(mutant),
        **result,
        "fallback_domains": sorted(
            domain for domain, tests in item["selected"].items() if not tests
        ),
        "restored": restored,
        "elapsed_seconds": monotonic() - started,
    }
    (path.parent / f"result-{index}.json").write_text(json.dumps(row))


def measure(
    scratch: Path, selected: dict, timeout: float, *, mutated_file: Path | None = None
) -> dict:
    if not selected:
        return {"outcome": "NO_TESTS", "domains": []}
    timings = []
    result = {}
    for domain, tests in selected.items():
        result, _ = run_domain(scratch, domain, tests, timeout, mutated_file=mutated_file)
        timings.append({key: result[key] for key in ("domain", "outcome", "elapsed_seconds")})
        if domain == "fixture":
            result["confirmation"] = "fixture"
        if result["outcome"] != "SURVIVED":
            return {**result, "domains": timings}
    result["confirmation"] = "fixture" if "fixture" in selected else "unreached"
    return {**result, "domains": timings}


def collect(root: Path, selected: dict) -> tuple[list[Mutant], list[dict]]:
    mutants, diagnostics = [], []
    for file, lines in sorted(selected.items()):
        found, rejected = candidates(file, (root / file).read_bytes())
        mutants.extend(
            mutant
            for mutant in found
            if lines is None
            or any(line in lines for line in range(mutant.line, mutant.end_line + 1))
        )
        diagnostics.extend(rejected)
    return mutants, diagnostics


def manifest_data(root: Path, mutants: list[Mutant], mapping: dict, timeout: float) -> dict:
    return {
        "root": str(root),
        "sources": {
            file: (root / file).read_bytes().decode() for file in sorted({m.file for m in mutants})
        },
        "mutants": [
            {
                **{key: value for key, value in asdict(mutant).items() if key != "before"},
                "selected": selection(mapping, mutant.file, mutant.line, mutant.end_line),
            }
            for mutant in mutants
        ],
        "timeout": timeout,
    }


def schedule(root: Path, mutants: list[Mutant], mapping: dict, args) -> list[dict]:
    destination = root / "tmp/mutation-runs"
    destination.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=destination)).resolve()
    print(json.dumps({"outcome": "EVIDENCE", "directory": str(work)}), file=sys.stderr, flush=True)
    data = manifest_data(root, mutants, mapping, args.timeout)
    try:
        (work / "scheduler.log").touch()
        status = run_scheduler(root, work, data, args.workers)
        log = (work / "scheduler.log").read_text(errors="replace")
        rows = scheduler_rows(work, mutants, data, log)
        if status != 0 or any(not row["restored"] for row in rows):
            raise SchedulerError(f"Mutant scheduler failed: {log}", rows)
        return rows
    except (
        BaseExceptionGroup,
        KeyboardInterrupt,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        # Record before reading results: a second diagnostic failure must not erase
        # the original cancellation/cleanup traceback. Never resume this directory.
        diagnostic = traceback.format_exc()
        (work / "traceback.txt").write_text(diagnostic)
        if isinstance(error, SchedulerError):
            raise
        rows = scheduler_rows(work, mutants, data, str(error))
        raise SchedulerError(f"Mutant scheduler failed: {error}", rows) from error


def run_scheduler(root: Path, work: Path, data: dict, workers: int) -> int | None:
    manifest = work / "mutations.json"
    manifest.write_text(json.dumps(data))
    config = work / "pytest.ini"
    config.write_text("[pytest]\n")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(config),
        "-p",
        "scripts.validation.mutation_probe",
        "--no-cov",
        "-n",
        str(workers),
        str(manifest),
    ]
    return program(
        command,
        work,
        environment(root, work),
        data["timeout"] * (len(data["mutants"]) + 2) * 3,
        work / "scheduler.log",
    )


def scheduler_rows(work: Path, mutants: list[Mutant], data: dict, log: str) -> list[dict]:
    rows = []
    for index, mutant in enumerate(mutants):
        result = work / f"result-{index}.json"
        try:
            rows.append(json.loads(result.read_text()))
        except FileNotFoundError:
            rows.append(
                {
                    **asdict(mutant),
                    "outcome": "INFRA",
                    "message": "Controller produced no result for this candidate",
                    "log": log,
                    "restored": False,
                    "domains": [],
                    "fallback_domains": sorted(
                        name
                        for name, tests in data["mutants"][index]["selected"].items()
                        if not tests
                    ),
                }
            )
            result.write_text(json.dumps(rows[-1]))
    return rows


def verdict(rows: list[dict], exemptions: list[dict]) -> int:
    failed = False
    for row in sorted(
        rows, key=lambda row: (row["file"], row["line"], row["operator"], row["site"])
    ):
        key = Mutant(**{name: row[name] for name in Mutant.__dataclass_fields__}).key()
        matches = exemption_matches(key, exemptions)
        if row["outcome"] == "SURVIVED" and matches:
            row.update(outcome="ALLOWLISTED", reason=matches[0]["reason"])
        failed |= row["outcome"] not in {"KILLED", "TIMEOUT", "ALLOWLISTED"}
        print(json.dumps(row))
    return int(failed)


def exemption_matches(key: dict, exemptions: list[dict]) -> list[dict]:
    return [
        entry for entry in exemptions if all(entry[name] == value for name, value in key.items())
    ]


def shard_argument(value: str) -> tuple[int, int]:
    try:
        index, count = map(int, value.split("/"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected K/N with 1 <= K <= N") from error
    if not 1 <= index <= count:
        raise argparse.ArgumentTypeError("expected K/N with 1 <= K <= N")
    return index, count


def partition(mutants: list[Mutant], shard: tuple[int, int]) -> list[Mutant]:
    index, count = shard
    return [
        mutant
        for mutant in mutants
        if int(digest(json.dumps(mutant.key(), sort_keys=True)), 16) % count + 1 == index
    ]


def shard_catalogue(mutants: list[Mutant], args) -> list[Mutant]:
    args.catalogue = {}
    if args.shard is None:
        return mutants
    selected = partition(mutants, args.shard)
    index, count = args.shard
    args.catalogue = {
        "shard": f"{index}/{count}",
        "full_mutants": len(mutants),
        "shard_mutants": len(selected),
    }
    print(json.dumps({"outcome": "CATALOGUE", **args.catalogue}))
    return selected


def report_results(rows: list[dict], exemptions: list[dict], args, started: float) -> int:
    status = verdict(rows, exemptions)
    if args.shard is not None:
        counts = Counter(row["outcome"] for row in rows)
        kinds = Counter(row.get("kind") for row in rows if row["outcome"] == "KILLED")
        print(
            json.dumps(
                {
                    "outcome": "SUMMARY",
                    **args.catalogue,
                    "counts": dict(counts),
                    "kill_kinds": {kind: kinds[kind] for kind in ("assertion", "error", "import")},
                    "baseline_domains": args.baseline_domains,
                    **execution_costs(rows),
                    "elapsed_seconds": monotonic() - started,
                },
            )
        )
    return status


def execution_costs(rows: list[dict]) -> dict:
    fallbacks = Counter(domain for row in rows for domain in row["fallback_domains"])
    seconds = Counter()
    for row in rows:
        for domain in row["domains"]:
            seconds[domain["domain"]] += domain["elapsed_seconds"]
    total = sum(seconds.values())
    return {
        "fallback_count": sum(bool(row["fallback_domains"]) for row in rows),
        "fallback_domains": dict(fallbacks),
        "runner_seconds_by_domain": dict(seconds),
        "runner_seconds": total,
        "runner_hours": total / 3600,
    }


def main(argv: list[str] | None = None) -> int:
    started = monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--paths", nargs="+", default=[])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument(
        "--allowlist", type=Path, default=Path("scripts/validation/mutation-allowlist.yaml")
    )
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--baseline-timeout", type=float, default=1800)
    parser.add_argument("-n", "--workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--shard", type=shard_argument, metavar="K/N")
    parser.add_argument("--coverage-dir", type=Path)
    args = parser.parse_args(argv)
    if min(args.timeout, args.baseline_timeout) <= 0 or args.workers < 1:
        parser.error("timeout and workers must be positive")
    root = args.root.resolve()
    exit_on_sigterm()
    exemptions = []
    try:
        mapping = imported_coverage(root, args.coverage_dir)
        exemptions = allowlist(root, root / args.allowlist)
        mutants, diagnostics = collect(root, scope(root, args.paths, args.base, args.head))
        for row in diagnostics:
            print(json.dumps(row))
        mutants = shard_catalogue(mutants, args)
        if not mutants:
            print(json.dumps({"outcome": "EMPTY", "mutants": 0, **args.catalogue}))
            return 0
        random.Random(args.seed).shuffle(mutants)  # noqa: S311 - reproducible scheduling
        args.baseline_domains = []
        mapping = resolve_coverage(root, mapping, args)
        return report_results(schedule(root, mutants, mapping, args), exemptions, args, started)
    except SchedulerError as error:
        report_results(error.rows, exemptions, args, started)
        print(json.dumps({"outcome": "INFRA", "message": str(error)}))
        return 2
    except (ValueError, OSError, subprocess.SubprocessError, SyntaxError) as error:
        print(json.dumps({"outcome": "INFRA", "message": str(error)}))
        return 2


def imported_coverage(root: Path, directory: Path | None) -> dict | None:
    if directory is None:
        return None
    from scripts.validation.mutation_coverage import load  # noqa: PLC0415 - optional CI input

    return load(root, directory)


def resolve_coverage(root: Path, mapping: dict | None, args) -> dict:
    if mapping is not None:
        return mapping
    return baseline(root, args.baseline_timeout, timings=args.baseline_domains)


if __name__ == "__main__":
    sys.exit(main())
