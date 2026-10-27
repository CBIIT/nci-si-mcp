"""Deterministic changed-site mutation gate. JSON lines retain every outcome and kill owner."""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

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


def execute_job(path: Path, index: int) -> None:
    jobs = json.loads(path.read_text())
    mutant = Mutant(**jobs["mutants"][index])
    with snapshot(Path(jobs["root"])) as scratch:
        target = scratch / mutant.file
        original = target.read_bytes()
        try:
            target.write_bytes(
                original[: mutant.start] + mutant.after.encode() + original[mutant.end :]
            )
            result = measure(scratch, mutant, jobs)
        finally:
            target.write_bytes(original)
            if digest(target.read_bytes().hex()) != digest(original.hex()):
                raise ValueError(f"Scratch restoration failed: {mutant.file}")
    row = {**asdict(mutant), **result, "restored": True}
    (path.parent / f"result-{index}.json").write_text(json.dumps(row))


def measure(scratch: Path, mutant: Mutant, jobs: dict) -> dict:
    selected = selection(jobs["mapping"], mutant.file, mutant.line, mutant.end_line)
    if not selected:
        return {"outcome": "NO_TESTS"}
    for domain, tests in selected.items():
        result, _ = run_domain(scratch, domain, tests, jobs["timeout"])
        if result["outcome"] != "SURVIVED":
            return result
    result, _ = run_domain(scratch, "fixture", [], jobs["timeout"])
    result["confirmation"] = "fixture"
    return result


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


def schedule(root: Path, mutants: list[Mutant], mapping: dict, args) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="mutation-jobs-") as temporary:
        work = Path(temporary)
        manifest = work / "mutations.json"
        manifest.write_text(
            json.dumps(
                {
                    "root": str(root),
                    "mutants": list(map(asdict, mutants)),
                    "mapping": mapping,
                    "timeout": args.timeout,
                }
            )
        )
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
            str(args.workers),
            str(manifest),
        ]
        env = environment(root, work)
        status = program(
            command, work, env, args.timeout * (len(mutants) + 2) * 3, work / "scheduler.log"
        )
        if status != 0:
            raise ValueError(f"Mutant scheduler failed: {(work / 'scheduler.log').read_text()}")
        return [
            json.loads((work / f"result-{index}.json").read_text()) for index in range(len(mutants))
        ]


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
        print(json.dumps(row, sort_keys=True))
    return int(failed)


def exemption_matches(key: dict, exemptions: list[dict]) -> list[dict]:
    return [
        entry for entry in exemptions if all(entry[name] == value for name, value in key.items())
    ]


def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)
    if min(args.timeout, args.baseline_timeout) <= 0 or args.workers < 1:
        parser.error("timeout and workers must be positive")
    root = args.root.resolve()
    exit_on_sigterm()
    try:
        exemptions = allowlist(root, root / args.allowlist)
        mutants, diagnostics = collect(root, scope(root, args.paths, args.base, args.head))
        for row in diagnostics:
            print(json.dumps(row, sort_keys=True))
        if not mutants:
            print(json.dumps({"outcome": "EMPTY", "mutants": 0}))
            return 0
        random.Random(args.seed).shuffle(mutants)  # noqa: S311 - reproducible scheduling
        mapping = baseline(root, args.baseline_timeout)
        return verdict(schedule(root, mutants, mapping, args), exemptions)
    except (ValueError, OSError, subprocess.SubprocessError, SyntaxError) as error:
        print(json.dumps({"outcome": "INFRA", "message": str(error)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
