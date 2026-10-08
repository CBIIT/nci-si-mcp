"""Bounded committed-source snapshots for disposable validation jobs."""

from __future__ import annotations

import io
import re
import subprocess
import tarfile
import typing
from pathlib import Path

SOURCE_PATHS = ("src", "scripts", "acceptance", "spec", "pyproject.toml", "pdm.lock")
MAX_SOURCE_BYTES = 64 * 1024 * 1024


def head_commit(repository: Path) -> str:
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
    content = _archive(repository, commit)
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        members = archive.getmembers()
        if any(not (member.isfile() or member.isdir()) for member in members):
            raise ValueError("Validation source must contain only regular files and directories")
        if sum(member.size for member in members) > MAX_SOURCE_BYTES:
            raise ValueError("Validation source exceeds its size bound")
        output.mkdir(parents=True, exist_ok=False)
        archive.extractall(output, filter="data")
