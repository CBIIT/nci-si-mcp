"""The suite's fixture set loads and is what the manifest records."""

import json
from pathlib import Path

import yaml

from nci_si_acceptance.craft import craft
from nci_si_acceptance.fixture_server import load_fixtures
from nci_si_acceptance.record import FIXTURES, plan, request_of, stale

MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))
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
    # Each recording holds the request its entry makes, headers and body included, and the
    # status its entry expects.
    assert [
        each.file
        for each in planned
        if (DOCUMENTS[each.file]["request"], DOCUMENTS[each.file]["response"]["status"])
        != (request_of(each), each.status)
    ] == []


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
