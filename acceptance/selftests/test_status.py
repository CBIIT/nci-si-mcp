"""The README's status table is generated from the expected outcomes and is current."""

import subprocess
import sys

import pytest

from nci_si_acceptance.status import BEGIN, END, README, current, render, tool_outcomes

# A gate, and tests of three tools.
ATTRIBUTION = {
    "t.py::gate": (None, True),
    "t.py::resolve": ("resolve_release", False),
    "t.py::concept": ("get_concept", False),
    "t.py::concept_again": ("get_concept", False),
    "t.py::form": ("get_form", False),
}


def test_a_tool_is_not_implemented_when_every_test_of_it_was_skipped_for_want_of_it():
    expected = {
        "t.py::gate": "passed",
        "t.py::resolve": "passed",
        "t.py::concept": "not_implemented",
        "t.py::concept_again": "not_implemented",
        "t.py::form": "failed",
    }

    outcomes = tool_outcomes(expected, ATTRIBUTION)

    assert outcomes["resolve_release"] == "PASS"
    assert outcomes["get_concept"] == "NOT IMPLEMENTED"
    assert outcomes["get_form"] == "FAIL"
    assert outcomes["list_contexts"] == "NO TESTS"


def test_a_failed_gate_fails_a_tool_that_passes():
    expected = dict.fromkeys(ATTRIBUTION, "passed") | {"t.py::gate": "failed"}

    outcomes = tool_outcomes(expected, ATTRIBUTION)

    assert outcomes["resolve_release"] == "FAIL"


def test_the_table_lists_each_groups_tools_under_their_outcome():
    expected = dict.fromkeys(ATTRIBUTION, "passed") | {"t.py::form": "failed"}

    table = render(expected, ATTRIBUTION)

    assert table.startswith(BEGIN)
    assert table.endswith(END)
    assert "of the 5 tests" in table
    assert "| EVS tools | 12 | PASS: `resolve_release`, `get_concept`<br>NO TESTS: " in table
    assert "| caDSR tools | 10 | FAIL: `get_form`<br>NO TESTS: " in table


def test_only_the_text_between_the_markers_is_replaced():
    readme = f"before\n{BEGIN}\nold\n{END}\nafter\n"

    assert current(readme, f"{BEGIN}\nnew\n{END}") == f"before\n{BEGIN}\nnew\n{END}\nafter\n"


def test_a_readme_without_the_markers_is_refused():
    with pytest.raises(SystemExit, match="lacks the markers"):
        current("no table here", "table")


def run_status(*options):
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "nci_si_acceptance.status", *options],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_readme_on_disk_is_what_the_expected_outcomes_generate():
    ran = run_status("--check")

    assert (ran.returncode, ran.stderr) == (0, "")


def test_a_readme_that_is_not_current_fails_the_check_and_is_rewritten_without_it(tmp_path):
    stale = tmp_path / "README.md"
    text = README.read_text(encoding="utf-8")
    stale.write_text(text.replace("NOT IMPLEMENTED:", "NOT DONE:"), encoding="utf-8")

    failed = run_status("--check", "--readme", str(stale))
    written = run_status("--readme", str(stale))

    assert failed.returncode == 1
    assert "is not current" in failed.stderr
    assert written.returncode == 0
    assert stale.read_text(encoding="utf-8") == text
