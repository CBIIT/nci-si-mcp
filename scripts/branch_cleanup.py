"""Delete issue refs contained in a merged milestone's reviewed head, recording each tip."""

import html
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argument lists, never shell evaluation
        [shutil.which(args[0]) or args[0], *args[1:]],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
    )


def remote_heads(pattern: str = "refs/heads/*") -> dict[str, str]:
    rows = run("git", "ls-remote", "--heads", "origin", pattern).stdout.splitlines()
    return {ref.removeprefix("refs/heads/"): tip for tip, ref in map(str.split, rows)}


def ancestor(tip: str, head: str) -> bool:
    result = run("git", "merge-base", "--is-ancestor", tip, head, check=False)
    if result.returncode == 1:
        return False
    result.check_returncode()
    return True


def record(status: str, name: str, tip: str, reason: str) -> None:
    # Git allows Markdown/HTML characters in ref names; keep summary rows literal.
    escaped = html.escape(name).replace("|", "&#124;")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(f"| {status} | <code>{escaped}</code> | {tip} | {reason} |\n")


def delete_unchanged(name: str, tip: str) -> None:
    current = remote_heads("refs/heads/" + name).get(name)
    if current != tip:
        record("Kept", name, current or "absent", "Changed since selection")
        return
    endpoint = f"repos/{os.environ['GITHUB_REPOSITORY']}/git/refs/heads/{quote(name, safe='/')}"
    try:
        run("gh", "api", "-X", "DELETE", endpoint)
    except subprocess.CalledProcessError:
        record("Failed", name, tip, "API deletion failed; check the ref before retrying")
        raise
    record("Deleted", name, tip, "Included in the reviewed milestone")


def cleanup(head: str, milestone: str) -> None:
    for name, tip in sorted(remote_heads().items()):
        if name.startswith("issue/"):
            selected = ancestor(tip, head)
        elif name == milestone:
            selected = tip == head
        else:
            continue
        if selected:
            delete_unchanged(name, tip)
        else:
            record("Kept", name, tip, "Not included in the reviewed milestone")


def main() -> None:
    head = os.environ["MERGED_HEAD"]
    milestone = os.environ["MILESTONE_BRANCH"]
    number = os.environ["PR_NUMBER"]
    # The workflow supplies these fields from the merge event, not shell source.
    if not number.isdecimal() or not milestone.startswith("milestone/"):
        raise ValueError("Expected a milestone pull request")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(
            "## Branch cleanup\n\n| Status | Branch | Tip | Reason |\n|---|---|---|---|\n"
        )
    # Squash merges lose the issue ancestry; the retained PR ref is an object source only.
    run("git", "fetch", "--no-tags", "origin", f"refs/pull/{number}/head")
    if run("git", "rev-parse", "FETCH_HEAD").stdout.strip() != head:
        raise ValueError("Retained pull request head differs from the merged event head")
    cleanup(head, milestone)


if __name__ == "__main__":
    main()
