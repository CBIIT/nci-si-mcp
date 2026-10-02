"""The suite's fixture set loads, is what the manifest records, and holds no licensed content."""

import json
from pathlib import Path

import pytest
import yaml

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


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
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


def test_every_recorded_fixture_is_dated_and_pinned():
    recorded = [doc for name, doc in DOCUMENTS.items() if name.startswith("recorded/")]
    release = MANIFEST["evs"]["release"]

    assert {doc["kind"] for doc in recorded} == {"recorded"}
    assert all(doc["recorded_on"] for doc in recorded)
    concepts = [doc["response"]["body"] for name, doc in DOCUMENTS.items() if "/concepts/" in name]
    assert {f"ncit_{body['version']}" for body in concepts if "version" in body} == {release}


def test_every_derived_fixture_names_its_requirement_and_answers_as_its_recording():
    for entry in MANIFEST["record"].get("derived", []):
        document = DOCUMENTS[entry["fixture"]]
        assert (document["kind"], document["requirement"]) == ("crafted", entry["requirement"])
        assert document["response"] == DOCUMENTS[entry["from"]]["response"]


def test_the_fixture_directory_is_the_packages_own():
    assert Path(__file__).parents[1] / "fixtures" == FIXTURES
