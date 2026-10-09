"""QUICKSTART's tool table is generated from the specification's tool data and is current."""

import subprocess
import sys

import pytest

from nci_si_acceptance import quickstart
from nci_si_acceptance.quickstart import BEGIN, END, QUICKSTART, current, render
from nci_si_acceptance.spec import TOOLS


def test_the_table_has_a_row_per_tool_with_its_group_and_the_summary_of_the_specification():
    table = render()

    assert table.startswith(BEGIN)
    assert table.endswith(END)
    rows = [line for line in table.splitlines() if line.startswith("| `")]
    assert [row.split("`")[1] for row in rows] == list(TOOLS)
    assert "| `get_form` | caDSR | " + TOOLS["get_form"]["summary"].replace("|", "\\|") in table


def test_a_summary_cannot_break_the_table():
    tool = {"group": "evs", "summary": "one | two\nthree"}

    assert quickstart.row("t", tool) == "| `t` | EVS | one \\| two three |"


def test_only_the_text_between_the_markers_is_replaced():
    document = f"before\n{BEGIN}\nold\n{END}\nafter\n"

    assert current(document, f"{BEGIN}\nnew\n{END}") == f"before\n{BEGIN}\nnew\n{END}\nafter\n"


@pytest.mark.parametrize("document", ["no table here", f"a\n{BEGIN}\nb\n", f"a\n{END}\nb\n"])
def test_a_document_without_both_markers_is_refused(document):
    with pytest.raises(SystemExit, match="lacks the markers"):
        current(document, "table")


def run_quickstart(*options):
    return subprocess.run(  # noqa: S603 - this interpreter, a module of the suite
        [sys.executable, "-m", "nci_si_acceptance.quickstart", *options],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_quickstart_on_disk_is_what_the_specification_generates():
    ran = run_quickstart("--check")

    assert (ran.returncode, ran.stderr) == (0, "")


def test_a_quickstart_that_is_not_current_fails_the_check_and_is_rewritten_without_it(tmp_path):
    stale = tmp_path / "QUICKSTART.md"
    text = QUICKSTART.read_text(encoding="utf-8")
    stale.write_text(text.replace(BEGIN, BEGIN + "\nstale table"), encoding="utf-8")

    failed = run_quickstart("--check", "--quickstart", str(stale))
    written = run_quickstart("--quickstart", str(stale))

    assert failed.returncode == 1
    assert "is not current" in failed.stderr
    assert written.returncode == 0
    assert stale.read_text(encoding="utf-8") == text
