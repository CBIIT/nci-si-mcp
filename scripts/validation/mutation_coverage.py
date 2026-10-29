"""Same-tree CI coverage fragments; explicit imports never recompute a baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath

from scripts.validation.mutation_run import context_tests, coverage_args, coverage_map

SHARDS = {"unit": 4, "selftest": 3, "fixture": 1}
VERSION = 1


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(  # noqa: S603 - fixed Git reads and explicit repository root
        ["git", *arguments],  # noqa: S607
        cwd=root,
        stderr=subprocess.PIPE,
    )


def identity(root: Path) -> dict:
    """Commit plus actual tracked inputs: dirty content cannot borrow a clean tree's map."""
    digest = hashlib.sha256()
    names = git(root, "ls-files", "-z").decode().split("\0")
    for name in sorted(filter(None, names)):
        path = root / name
        digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    return {"sha": git(root, "rev-parse", "HEAD").decode().strip(), "inputs": digest.hexdigest()}


def canonical_file(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and str(path) == name


def validate_lines(lines: object, domain: str) -> None:
    if not isinstance(lines, dict):
        raise ValueError("Invalid coverage line mapping")
    prefix = {"unit": "tests/", "selftest": "selftests/", "fixture": "tests/"}[domain]
    for line, contexts in lines.items():
        if not str(line).isdigit() or int(line) < 1 or not isinstance(contexts, list):
            raise ValueError("Invalid coverage line/context shape")
        validate_contexts(contexts, prefix)


def validate_contexts(contexts: list, prefix: str) -> None:
    for context in contexts:
        if not isinstance(context, str):
            raise ValueError("Invalid coverage context type")
        context_tests({context})
        if context:
            node = context.rpartition("|")[0]
            if not node.startswith(prefix) or not canonical_file(node.split("::")[0]):
                raise ValueError("Coverage context belongs to another domain")


def validate_mapping(mapping: object, domain: str) -> None:
    if not isinstance(mapping, dict):
        raise ValueError("Missing coverage mapping")
    for name, lines in mapping.items():
        if not canonical_file(name):
            raise ValueError("Invalid coverage source path")
        validate_lines(lines, domain)
    if not has_calls(mapping):
        raise ValueError("Coverage has no per-test call contexts")


def has_calls(mapping: dict) -> bool:
    contexts = {
        value for lines in mapping.values() for values in lines.values() for value in values
    }
    return any(context.endswith("|run") for context in contexts)


def export(root: Path, data: Path, domain: str, shard: str, output: Path) -> None:
    mapping = coverage_map(data, root)
    validate_mapping(mapping, domain)
    index, count = (int(part) for part in shard.split("/"))
    if count != SHARDS[domain] or not 1 <= index <= count:
        raise ValueError("Invalid coverage shard membership")
    fragment = {
        "version": VERSION,
        **identity(root),
        "domain": domain,
        "shard": shard,
        "mapping": mapping,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fragment) + "\n")


def read_fragment(path: Path, expected: dict) -> dict:
    value = json.loads(path.read_text())
    keys = {"version", "sha", "inputs", "domain", "shard", "mapping"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Invalid coverage fragment schema")
    actual = {key: value[key] for key in expected}
    if type(value["version"]) is not int or (value["version"], actual) != (VERSION, expected):
        raise ValueError("Coverage fragment version or tree identity mismatch")
    if not valid_membership_types(value):
        raise ValueError("Invalid coverage domain/shard")
    validate_mapping(value["mapping"], value["domain"])
    return value


def valid_membership_types(value: dict) -> bool:
    return (
        isinstance(value["domain"], str)
        and value["domain"] in SHARDS
        and isinstance(value["shard"], str)
    )


def load(root: Path, directory: Path) -> dict:
    """Validate complete same-run producer membership before using any imported selection."""
    if not directory.is_dir():
        raise ValueError(f"Missing coverage directory: {directory}")
    expected = identity(root)
    remaining = shard_keys()
    result = {domain: {} for domain in SHARDS}
    for path in sorted(directory.rglob("*.json")):
        fragment = read_fragment(path, expected)
        key = (fragment["domain"], fragment["shard"])
        if key not in remaining:
            raise ValueError("Duplicate or unexpected coverage shard")
        remaining.remove(key)
        merge(result[key[0]], fragment["mapping"])
    if remaining:
        raise ValueError(f"Missing coverage shards: {sorted(remaining)}")
    return result


def shard_keys() -> set[tuple[str, str]]:
    return {
        (domain, f"{i}/{count}") for domain, count in SHARDS.items() for i in range(1, count + 1)
    }


def merge(destination: dict, mapping: dict) -> None:
    for name, lines in mapping.items():
        target = destination.setdefault(name, {})
        for line, contexts in lines.items():
            target[line] = sorted(set(target.get(line, [])) | set(contexts))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("configure", "export", "check"))
    parser.add_argument("domain", choices=SHARDS)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--data", type=Path)
    parser.add_argument("--shard")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        perform(args)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"outcome": "INFRA", "message": str(error)}))
        return 2
    return 0


def perform(args) -> None:
    root = args.root.resolve()
    if args.operation == "configure":
        args.output.mkdir(parents=True, exist_ok=True)
        coverage_args(root, args.output.resolve(), True, {})
    elif args.operation == "check":
        load(root, args.output)
    else:
        if args.data is None or args.shard is None:
            raise ValueError("Coverage export requires --data and --shard")
        export(root, args.data.resolve(), args.domain, args.shard, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
