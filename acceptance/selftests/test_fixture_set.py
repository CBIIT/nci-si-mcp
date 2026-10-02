"""The suite's fixture set loads, is what the manifest records, and holds no licensed content."""

import json
from pathlib import Path

import pytest
import yaml

from nci_si_acceptance.craft import craft
from nci_si_acceptance.fixture_server import load_fixtures
from nci_si_acceptance.licensing import Licensing
from nci_si_acceptance.record import FIXTURES, plan, stale

MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))
LICENSING = Licensing.from_manifest(MANIFEST["licensing"])
DOCUMENTS = {
    path.relative_to(FIXTURES).as_posix(): json.loads(path.read_text(encoding="utf-8"))
    for path in sorted(FIXTURES.rglob("*.json"))
    if path.name != "settings.json"
}


def test_the_fixture_set_loads():
    fixtures = load_fixtures(FIXTURES)

    assert fixtures.ordinary
    assert fixtures.recordings[None]


def test_the_recorded_set_is_exactly_what_the_manifest_records():
    planned = plan(MANIFEST)

    assert [each.file for each in planned if each.file not in DOCUMENTS] == []
    assert stale(FIXTURES, MANIFEST) == []


CRAFTED = craft(FIXTURES)


def test_the_crafted_scenarios_are_what_craft_py_makes_now():
    on_disk = {
        name: json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        for name in CRAFTED
        if (FIXTURES / name).is_file()
    }

    assert on_disk == CRAFTED


def test_every_scenario_file_is_recorded_or_crafted():
    produced = {each.file for each in plan(MANIFEST)} | set(CRAFTED)
    scenario_files = {
        path.relative_to(FIXTURES).as_posix() for path in (FIXTURES / "scenarios").rglob("*.json")
    }

    assert sorted(scenario_files - produced) == []


@pytest.mark.parametrize(
    "name", sorted(name for name in DOCUMENTS if "placeholder" not in DOCUMENTS[name])
)
def test_no_fixture_holds_licensed_content(name):
    document = DOCUMENTS[name]
    request = document["request"]
    statuses = [
        response.get("status", 200)
        for response in document.get("responses") or [document["response"]]
    ]
    bodies = [
        response.get("body") for response in document.get("responses") or [document["response"]]
    ]

    for status in statuses:
        assert LICENSING.request_problem(request["path"], request.get("params", {}), status) is None
    assert [name for body in bodies for name in LICENSING.licensed_names(body)] == []
    assert [name for body in bodies for name in LICENSING.undecided(body)] == []


PLACEHOLDERS = sorted(name for name in DOCUMENTS if "placeholder" in DOCUMENTS[name])


def test_there_is_placeholder_licensed_content_to_check():
    assert PLACEHOLDERS == ["scenarios/license/restricted/granted.json"]


@pytest.mark.parametrize("name", PLACEHOLDERS)
def test_placeholder_content_is_crafted_by_code_and_names_only_its_own_terminology(name):
    document = DOCUMENTS[name]
    bodies = [
        response.get("body") for response in document.get("responses") or [document["response"]]
    ]

    assert document == CRAFTED.get(name)
    assert {found for body in bodies for found in LICENSING.licensed_names(body)} <= {
        document["placeholder"]["terminology"]
    }
    assert [found for body in bodies for found in LICENSING.undecided(body)] == []


def test_every_recorded_fixture_is_dated_and_pinned():
    recorded = [doc for name, doc in DOCUMENTS.items() if name.startswith("recorded/")]
    release = MANIFEST["evs"]["release"]

    assert {doc["kind"] for doc in recorded} == {"recorded"}
    assert all(doc["recorded_on"] for doc in recorded)
    concepts = [
        doc["response"]["body"]
        for name, doc in DOCUMENTS.items()
        if name.startswith("recorded/") and "/concepts/" in name
    ]
    assert {f"ncit_{body['version']}" for body in concepts if "version" in body} == {release}


def test_every_derived_fixture_names_its_requirement_and_answers_as_its_recording():
    for entry in MANIFEST["record"].get("derived", []):
        document = DOCUMENTS[entry["fixture"]]
        assert (document["kind"], document["requirement"]) == ("crafted", entry["requirement"])
        assert document["response"] == DOCUMENTS[entry["from"]]["response"]


def test_the_fixture_directory_is_the_packages_own():
    assert Path(__file__).parents[1] / "fixtures" == FIXTURES
