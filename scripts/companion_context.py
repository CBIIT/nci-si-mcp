"""Prepare separate, allowlisted container contexts from one clean source commit."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.companion_lock import render
from scripts.docs_site import build_site
from scripts.operator_source import archive_source, head_commit, package_source
from scripts.site_assets import FONT_FILES


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


def prepare_context(root: Path, output: Path, *, site: Path | None = None) -> None:
    """No recursive checkout copy; Git/config, local evidence and credentials stay outside."""
    if output.exists() or output.is_symlink():
        raise FileExistsError("Choose a fresh companion context directory")
    commit = clean_commit(root)
    if site is not None:
        _check_site(site, commit)
    requirements = render(tomllib.loads((root / "pdm.lock").read_text()))
    if requirements != (root / "container/companion-requirements.txt").read_text():
        raise ValueError("Companion dependency lock drifted")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="companion-", dir=output.parent) as temporary:
        staging = Path(temporary) / "context"
        staging.mkdir()
        _populate(root, staging, requirements, commit, site)
        if clean_commit(root) != commit:
            raise ValueError("Checkout changed while companion inputs were built")
        staging.rename(output)


def _check_site(site: Path, commit: str) -> None:
    if not site.is_dir():
        raise ValueError("Prebuilt site directory is missing")
    if site.is_symlink() or any(path.is_symlink() for path in site.rglob("*")):
        raise ValueError("Prebuilt site cannot contain or follow a symlink")
    identity = json.loads((site / "build.json").read_text())
    if identity.get("source_commit") != commit or identity.get("dirty") is not False:
        raise ValueError("Prebuilt site must name this clean source commit")


def _populate(root: Path, output: Path, requirements: str, commit: str, site: Path | None) -> None:
    docs, admin = output / "docs", output / "admin"
    docs.mkdir()
    admin.mkdir()
    if site is None:
        build_site(root, docs / "site")
    else:
        shutil.copytree(site, docs / "site")
    shutil.copyfile(root / "scripts/static_server.py", docs / "static_server.py")
    shutil.copyfile(root / "scripts/companion_relay.py", docs / "companion_relay.py")
    shutil.copyfile(root / "container/Docs.Dockerfile", docs / "Dockerfile")
    shutil.copyfile(root / "container/Admin.Dockerfile", admin / "Dockerfile")
    (admin / "requirements.txt").write_text(requirements)
    package_source(root, admin / "bundle")
    archive_source(admin / "bundle", commit, admin / "app")
    fonts = admin / "app/docs/site-assets/fonts"
    fonts.mkdir(parents=True)
    for name in FONT_FILES:
        shutil.copyfile(root / "docs/site-assets/fonts" / name, fonts / name)
    _wheels(root, admin / "wheels")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("tmp/companion-context"))
    parser.add_argument(
        "--site", type=Path, help="Reuse a clean public site built from this commit"
    )
    args = parser.parse_args()
    prepare_context(
        Path(__file__).resolve().parents[1],
        args.output.resolve(),
        site=args.site.absolute() if args.site is not None else None,
    )


if __name__ == "__main__":
    main()
