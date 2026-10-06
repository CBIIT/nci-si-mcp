"""Publish only an already verified image, then prove anonymous digest access."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(*args: str) -> str:
    command = [shutil.which(args[0]) or args[0], *args[1:]]
    return subprocess.check_output(command, text=True).strip()  # noqa: S603 - fixed commands, no shell


def main() -> None:
    tag = sys.argv[1]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Expected a release tag")
    repository = os.environ["GITHUB_REPOSITORY"]
    image = "ghcr.io/" + repository.lower()
    subprocess.run(  # noqa: S603 - token is stdin only; fixed executable and arguments
        [
            shutil.which("docker") or "docker",
            "login",
            "ghcr.io",
            "--username",
            os.environ["GITHUB_ACTOR"],
            "--password-stdin",
        ],
        input=os.environ["GH_TOKEN"],
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    run("docker", "tag", "nci-si-mcp:verified", f"{image}:{tag}")
    run("docker", "push", f"{image}:{tag}")
    digests = json.loads(
        run("docker", "image", "inspect", f"{image}:{tag}", "--format", "{{json .RepoDigests}}")
    )
    digest = next(value for value in digests if value.startswith(image + "@sha256:"))
    evidence = Path("tmp/image")
    (evidence / "image-digest.txt").write_text(digest + "\n")
    run(
        "gh",
        "release",
        "upload",
        tag,
        str(evidence / "scan.json"),
        str(evidence / "sbom.cdx.json"),
        str(evidence / "image-digest.txt"),
        "--clobber",
    )
    # New GHCR packages default private. Never claim public publication without this check.
    with tempfile.TemporaryDirectory(prefix="anonymous-", dir=evidence) as directory:
        run("docker", "--config", directory, "manifest", "inspect", digest)
    release = json.loads(run("gh", "release", "view", tag, "--json", "body"))
    marker = "\n<!-- container-image -->\n"
    body = release["body"].split(marker)[0]
    body += marker + f"Container: `{digest}` (tag `{tag}`).\n\n"
    body += (
        "SBOM: `sbom.cdx.json`; vulnerability report: `scan.json`; "
        "digest: `image-digest.txt` (release assets).\n"
    )
    notes = evidence / "release-notes.md"
    notes.write_text(body)
    run("gh", "release", "edit", tag, "--notes-file", str(notes))
    print(f"Published and verified public image: {digest}")


if __name__ == "__main__":
    main()
