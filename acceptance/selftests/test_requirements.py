"""Every requirement is cited by a test or planned in an issue; every test cites requirements."""

import re
from pathlib import Path

import pytest
import yaml

from nci_si_acceptance.requirements import citations, load_requirements, never_runs, problems

pytest_plugins = ["pytester"]

SUITE_TESTS = Path(__file__).parent.parent / "tests"
REQUIREMENTS = {
    "X-1": {"statement": "a", "basis": ["A3.1"], "planned": "#52"},
    "X-2": {"statement": "b", "basis": ["A3.4"]},
}


def test_the_suite_and_the_requirements_agree(pytester, monkeypatch):
    monkeypatch.setenv("NCI_SI_ACCEPTANCE_MODE", "fixture")
    items, _ = pytester.inline_genitems(str(SUITE_TESTS), "-p", "no:cacheprovider")
    cited = citations(items)
    idle = [item.nodeid for item in items if never_runs(item)]

    assert cited
    assert problems(cited, load_requirements(), idle) == []


def test_a_test_citing_nothing_or_an_unknown_id_and_an_unanswered_requirement_are_named():
    cited = {"t.py::a": (), "t.py::b": ("X-1", "X-9")}

    assert problems(cited, REQUIREMENTS) == [
        "t.py::a cites no requirement",
        "t.py::b cites X-9, which spec/requirements.yaml does not hold",
        "X-2 is neither cited by a test that runs nor planned",
    ]
    assert problems({"t.py::c": ("X-2",)}, REQUIREMENTS) == []
    assert problems({"t.py::c": ("X-2",)}, REQUIREMENTS, idle=["t.py::c"]) == [
        "X-2 is neither cited by a test that runs nor planned"
    ]


def write(tmp_path, requirements):
    path = tmp_path / "requirements.yaml"
    path.write_text(yaml.safe_dump(requirements), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        ("text", "has only basis, planned, statement"),
        (
            {"statement": "s", "basis": ["A1"], "sow": "item 5"},
            "has only basis, planned, statement",
        ),
        ({"statement": " ", "basis": ["A1"]}, "states the behaviour"),
        ({"basis": ["A1"]}, "states the behaviour"),
        ({"statement": "s", "basis": []}, "names its basis"),
        ({"statement": "s", "basis": [3]}, "names its basis"),
        ({"statement": "s", "basis": "A1"}, "names its basis"),
        ({"statement": "s", "basis": ["A1"], "planned": "52"}, "is planned in an issue"),
        ({"statement": "s", "basis": ["A1"], "planned": "#52 later"}, "is planned in an issue"),
    ],
)
def test_an_entry_without_statement_basis_or_a_valid_plan_is_refused(tmp_path, entry, problem):
    with pytest.raises(ValueError, match=f"requirements.yaml: X-1 {re.escape(problem)}"):
        load_requirements(write(tmp_path, {"X-1": entry}))


def test_every_problem_is_reported_at_once_and_a_good_file_loads(tmp_path):
    with pytest.raises(ValueError) as refused:
        load_requirements(write(tmp_path, {"X-1": {"basis": ["A1"]}, "X-2": "text"}))

    assert str(refused.value) == (
        "requirements.yaml: X-1 states the behaviour; X-2 has only basis, planned, statement"
    )
    assert load_requirements(write(tmp_path, REQUIREMENTS)) == REQUIREMENTS


SUITE = """
import pytest

pytestmark = pytest.mark.requirement("X-1")


@pytest.mark.requirement("P-1", "P-2")
@pytest.mark.requirement("P-3")
def test_cited():
    pass


@pytest.mark.skip(reason="for good")
@pytest.mark.requirement("P-4")
def test_skipped():
    pass


@pytest.mark.skipif(True, reason="never")
@pytest.mark.requirement("P-6")
def test_skipped_if_true():
    pass


@pytest.mark.skipif(False, reason="runs")
@pytest.mark.requirement("P-7")
def test_skipped_if_false():
    pass


@pytest.mark.xfail
@pytest.mark.requirement("P-99")
def test_expected_to_fail():
    pass
"""


def test_every_test_cites_but_only_one_that_runs_covers(pytester):
    pytester.makepyfile(test_cites=SUITE)
    pytester.makeini("[pytest]\nmarkers =\n    requirement: cites")

    items, _ = pytester.inline_genitems("-p", "no:cacheprovider")
    cited = citations(items)
    idle = sorted(item.nodeid.split("::")[1] for item in items if never_runs(item))

    assert sorted(cited["test_cites.py::test_cited"]) == ["P-1", "P-2", "P-3", "X-1"]
    assert sorted(cited["test_cites.py::test_expected_to_fail"]) == ["P-99", "X-1"]
    assert idle == ["test_expected_to_fail", "test_skipped", "test_skipped_if_true"]
