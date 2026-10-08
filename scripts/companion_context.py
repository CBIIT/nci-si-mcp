"""Prepare separate, allowlisted container contexts from one clean source commit."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.companion_lock import render
from scripts.docs_site import build_site
from scripts.operator_source import archive_source, head_commit, package_source


def clean_commit(root: Path) -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"],  # noqa: S607 - local checkout tooling
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    if status.stdout:
        raise ValueError("Commit all companion source changes before packaging")
    return head_commit(root)


def _wheels(root: Path, output: Path) -> None:
    subprocess.run(  # noqa: S603 - fixed build command and owned output directory
        ["pdm", "build", "--no-sdist", "--dest", str(output)],  # noqa: S607
        cwd=root,
        check=True,
        timeout=120,
    )
    subprocess.run(  # noqa: S603 - fixed build command and owned output directory
        ["pdm", "build", "-p", "acceptance", "--no-sdist", "--no-clean", "--dest", str(output)],  # noqa: S607
        cwd=root,
        check=True,
        timeout=120,
    )


def prepare_context(root: Path, output: Path) -> None:
    """No recursive checkout copy; Git/config, local evidence and credentials stay outside."""
    if output.exists() or output.is_symlink():
        raise FileExistsError("Choose a fresh companion context directory")
    commit = clean_commit(root)
    requirements = render(tomllib.loads((root / "pdm.lock").read_text()))
    if requirements != (root / "container/companion-requirements.txt").read_text():
        raise ValueError("Companion dependency lock drifted")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="companion-", dir=output.parent) as temporary:
        staging = Path(temporary) / "context"
        staging.mkdir()
        _populate(root, staging, requirements, commit)
        if clean_commit(root) != commit:
            raise ValueError("Checkout changed while companion inputs were built")
        staging.rename(output)


def _populate(root: Path, output: Path, requirements: str, commit: str) -> None:
    docs, admin = output / "docs", output / "admin"
    docs.mkdir()
    admin.mkdir()
    build_site(root, docs / "site")
    shutil.copyfile(root / "scripts/static_server.py", docs / "static_server.py")
    shutil.copyfile(root / "container/Docs.Dockerfile", docs / "Dockerfile")
    shutil.copyfile(root / "container/Admin.Dockerfile", admin / "Dockerfile")
    (admin / "requirements.txt").write_text(requirements)
    package_source(root, admin / "bundle")
    archive_source(admin / "bundle", commit, admin / "app")
    _wheels(root, admin / "wheels")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("tmp/companion-context"))
    args = parser.parse_args()
    prepare_context(Path(__file__).resolve().parents[1], args.output.resolve())


if __name__ == "__main__":
    main()
