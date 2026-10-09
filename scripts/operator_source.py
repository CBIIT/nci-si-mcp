"""Bounded committed-source snapshots for disposable validation jobs."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
import typing
from pathlib import Path, PurePosixPath

from scripts.operator_files import read_bytes

SOURCE_PATHS = ("src", "scripts", "acceptance", "spec", "pyproject.toml", "pdm.lock")
MAX_SOURCE_BYTES = 64 * 1024 * 1024


def head_commit(repository: Path) -> str:
    if (repository / "operator-source.json").exists():
        return _packaged(repository)[0]
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],  # noqa: S607 - local Git executable
        cwd=repository,
        capture_output=True,
        check=True,
        timeout=10,
        text=True,
    )
    commit = result.stdout.strip()
    _commit(commit)
    return commit


def _commit(commit: str) -> None:
    if re.fullmatch(r"[a-f0-9]{40}", commit) is None:
        raise ValueError("Validation source requires an exact commit")


def _archive(repository: Path, commit: str) -> bytes:
    command = ["git", "archive", "--format=tar", commit, *SOURCE_PATHS]
    with subprocess.Popen(  # noqa: S603 - fixed archive paths and validated commit, no shell
        command,
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        content = typing.cast("typing.BinaryIO", process.stdout).read(MAX_SOURCE_BYTES + 1)
        if len(content) > MAX_SOURCE_BYTES:
            process.kill()
            raise ValueError("Validation source exceeds its size bound")
        if process.wait(timeout=30):
            raise ValueError("Validation source could not be archived")
    return content


def archive_source(repository: Path, commit: str, output: Path) -> None:
    """Extract only committed source paths into a new owned directory, never symlinks."""
    _commit(commit)
    content = _content(repository, commit)
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        members = _regular_members(archive)
        if any(not _source_path(member.name) for member in members):
            raise ValueError("Validation archive contains an unapproved source path")
        output.mkdir(parents=True, exist_ok=False)
        archive.extractall(output, filter="data")


def _regular_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if any(not (member.isfile() or member.isdir()) for member in members):
        raise ValueError("Validation source must contain only regular files and directories")
    if sum(member.size for member in members) > MAX_SOURCE_BYTES:
        raise ValueError("Validation source exceeds its size bound")
    return members


def _source_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(path.parts)
        and path.parts[0] in SOURCE_PATHS
        and ".." not in path.parts
        and "\\" not in name
    )


def _content(repository: Path, commit: str) -> bytes:
    if (repository / "operator-source.json").exists():
        recorded, content = _packaged(repository)
        if recorded != commit:
            raise ValueError("Packaged source does not match the selected commit")
        return content
    return _archive(repository, commit)


def _packaged(repository: Path) -> tuple[str, bytes]:
    manifest = json.loads(read_bytes(repository / "operator-source.json", 4096))
    commit = _manifest_commit(manifest)
    content = read_bytes(repository / "operator-source.tar", MAX_SOURCE_BYTES)
    if hashlib.sha256(content).hexdigest() != manifest["sha256"]:
        raise ValueError("Packaged source digest mismatch")
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        if archive.pax_headers.get("comment") != commit:
            raise ValueError("Packaged archive commit mismatch")
    return commit, content


def _manifest_commit(manifest: typing.Any) -> str:
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "commit", "sha256"}:
        raise ValueError("Invalid packaged source identity")
    if type(manifest["schema"]) is not int or manifest["schema"] != 1:
        raise ValueError("Unsupported packaged source identity")
    commit = manifest["commit"]
    if not isinstance(commit, str):
        raise ValueError("Invalid packaged commit")
    _commit(commit)
    return commit


def package_source(repository: Path, output: Path) -> str:
    """Package fixed committed paths, without Git metadata or uncommitted source."""
    commit = head_commit(repository)
    content = _content(repository, commit)
    output.mkdir(parents=True, exist_ok=False)
    (output / "operator-source.tar").write_bytes(content)
    manifest = {"schema": 1, "commit": commit, "sha256": hashlib.sha256(content).hexdigest()}
    (output / "operator-source.json").write_text(json.dumps(manifest, sort_keys=True))
    return commit
