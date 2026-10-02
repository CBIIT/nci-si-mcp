"""The concept rules answer every projection and batch the recordings cover, and nothing else."""

import json
import re
from http import HTTPStatus
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
import yaml

from nci_si_acceptance.concepts import ConceptRules, Recording, recording_key
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures

RULES = {
    "base": ["code", "name"],
    "default": "summary",
    "include": {
        "minimal": [],
        "summary": ["synonyms"],
        "parents": ["parents"],
        "roles": ["roles"],
        "full": ["synonyms", "parents", "roles"],
    },
}
C1 = {"code": "C1", "name": "One", "synonyms": [{"name": "Uno"}], "parents": [{"code": "C9"}]}


def concept(code, **fields):
    return {"code": code, "name": f"Concept {code}", **fields}


def write_manifest(root, rules=RULES):
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.yaml").write_text(yaml.safe_dump({"evs": {"concepts": rules}}), "utf-8")


def recording(root, code, include="full", status=200, body=None, where="recorded/evs/concepts"):
    """Write a concept recording of `code`, recorded at `include`."""

    path = root / where / f"{code}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "kind": "recorded",
        "recorded_on": "2026-10-02",
        "request": {
            "surface": "evs",
            "method": "GET",
            "path": f"/api/v1/concept/ncit_26.09d/{code}",
            "params": {"include": [include]},
        },
        "response": {"status": status, "body": concept(code) if body is None else body},
    }
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def rules():
    return ConceptRules.from_manifest(RULES)


def finder(*recordings):
    table = {("ncit_26.09d", each.body["code"]): each for each in recordings}
    return lambda terminology, code: table.get((terminology, code))


def full(body):
    return Recording(
        f"{body['code']}.json",
        200,
        body,
        frozenset({"code", "name", "synonyms", "parents", "roles"}),
    )


def test_a_single_request_gets_the_base_and_the_keys_its_include_names(rules):
    find = finder(full(C1))

    def answer(query):
        params = {"include": [query]} if query else {}
        return rules.answer("/api/v1/concept/ncit_26.09d/C1", params, find).body

    assert answer("minimal") == {"code": "C1", "name": "One"}
    assert answer("parents,summary") == C1
    assert answer(None) == {"code": "C1", "name": "One", "synonyms": [{"name": "Uno"}]}
    assert answer("roles") == {"code": "C1", "name": "One"}


def test_a_batch_holds_each_code_once_never_in_the_order_requested(rules):
    find = finder(full(C1), full(concept("C2")), full(concept("C3")))

    answer = rules.answer(
        "/api/v1/concept/ncit_26.09d", {"list": ["C1,C2,C3,C1"], "include": ["minimal"]}, find
    )

    assert [each["code"] for each in answer.body] == ["C2", "C3", "C1"]
    assert answer.names == ["C2.json", "C3.json", "C1.json"]


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/concept/ncit_26.09d/C1", {"include": ["paths"]}),
        ("/api/v1/concept/ncit_26.09d/C1", {"include": ["minimal"], "limit": ["5"]}),
        ("/api/v1/concept/ncit_26.09d/C1", {"include": ["minimal", "summary"]}),
        ("/api/v1/concept/ncit_26.09d/C7", {"include": ["minimal"]}),
        ("/api/v1/concept/ncit_26.09d", {"include": ["minimal"]}),
        ("/api/v1/concept/ncit_26.09d", {"list": ["C1,C7"], "include": ["minimal"]}),
        ("/api/v1/concept/ncit_26.09d", {"list": ["C1,,C1"]}),
        ("/api/v1/concept/ncit_26.09d/C1/children", {}),
        ("/api/v1/concept/ncit_99.99z/C1", {"include": ["minimal"]}),
    ],
)
def test_what_the_recordings_do_not_cover_goes_unanswered(rules, path, params):
    assert rules.answer(path, params, finder(full(C1))) is None


def test_a_key_outside_a_recordings_include_goes_unanswered(rules):
    partial = Recording("C1.json", 200, C1, frozenset({"code", "name", "synonyms"}))

    assert rules.answer("/api/v1/concept/ncit_26.09d/C1", {"include": ["summary"]}, finder(partial))
    assert (
        rules.answer("/api/v1/concept/ncit_26.09d/C1", {"include": ["parents"]}, finder(partial))
        is None
    )


def test_a_recorded_404_answers_alone_and_drops_out_of_a_batch(rules):
    unknown = Recording("C404.json", 404, {"message": "C404 not found"}, frozenset())
    table = {("ncit_26.09d", "C1"): full(C1), ("ncit_26.09d", "C404"): unknown}

    def find(terminology, code):
        return table.get((terminology, code))

    single = rules.answer("/api/v1/concept/ncit_26.09d/C404", {"include": ["full"]}, find)
    batch = rules.answer("/api/v1/concept/ncit_26.09d", {"list": ["C404,C1"]}, find)

    assert (single.status, single.body) == (404, {"message": "C404 not found"})
    assert (batch.status, [each["code"] for each in batch.body]) == (200, ["C1"])
    assert batch.names == ["C1.json", "C404.json"]


@pytest.mark.parametrize(
    "section",
    [
        {"base": ["code"], "include": {"minimal": []}},
        {"base": ["code"], "include": [], "default": "x"},
    ],
)
def test_the_rules_need_base_include_and_default(section):
    with pytest.raises(ValueError, match=r"evs\.concepts has base, include and default"):
        ConceptRules.from_manifest(section)


def test_the_default_is_one_of_the_include_values():
    with pytest.raises(ValueError, match=r"evs\.concepts\.default is one of its include values"):
        ConceptRules.from_manifest(RULES | {"default": "everything"})


def test_a_recording_key_is_the_terminology_and_code_of_its_path():
    assert recording_key("/api/v1/concept/ncit_26.09d/C4817") == ("ncit_26.09d", "C4817")
    with pytest.raises(ValueError, match="is not the path of one concept"):
        recording_key("/api/v1/concept/ncit_26.09d")


def fetch_json(url):
    try:
        with urlopen(url, timeout=10) as response:  # noqa: S310 - a local test server
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_fixture_server_composes_answers_and_logs_the_recordings_used(tmp_path):
    write_manifest(tmp_path)
    recording(tmp_path, "C1", body=C1)
    recording(tmp_path, "C2")
    with FixtureServer(load_fixtures(tmp_path)) as running:
        base = running.base_url("evs") + "/api/v1/concept/ncit_26.09d"
        single = fetch_json(base + "/C1?include=parents")
        batch = fetch_json(base + "?list=C1%2CC2&include=minimal")
        missing = fetch_json(base + "/C3?include=minimal")
        logged = [entry["fixture"] for entry in running.log()]

    assert single == (200, {"code": "C1", "name": "One", "parents": [{"code": "C9"}]})
    assert batch == (200, [concept("C2"), {"code": "C1", "name": "One"}])
    assert missing[0] == HTTPStatus.NOT_IMPLEMENTED
    assert logged == [
        "recorded/evs/concepts/C1.json",
        "recorded/evs/concepts/C2.json, recorded/evs/concepts/C1.json",
        None,
    ]


def test_an_exact_fixture_and_a_scenarios_recording_come_before_the_ordinary_recording(tmp_path):
    write_manifest(tmp_path)
    recording(tmp_path, "C1", body=C1)
    recording(
        tmp_path, "C1", body=C1 | {"name": "Changed"}, where="scenarios/release/mismatch/concepts"
    )
    with FixtureServer(load_fixtures(tmp_path)) as running:
        url = running.base_url("evs") + "/api/v1/concept/ncit_26.09d/C1?include=minimal"
        ordinary = fetch_json(url)[1]["name"]
        running.activate("release/mismatch")
        during = fetch_json(url)[1]["name"]

    assert (ordinary, during) == ("One", "Changed")


def test_two_active_scenarios_recording_one_concept_are_refused(tmp_path):
    write_manifest(tmp_path)
    for scenario in ("release/a", "release/b"):
        recording(tmp_path, "C1", where=f"scenarios/{scenario}/concepts")

    with (
        FixtureServer(load_fixtures(tmp_path)) as running,
        pytest.raises(
            ValueError, match=r"the scenarios release/a, release/b answer the same request"
        ),
    ):
        running.activate("release/a", "release/b")


def test_two_recordings_of_one_concept_are_refused(tmp_path):
    write_manifest(tmp_path)
    recording(tmp_path, "C1")
    recording(tmp_path, "C1", where="crafted/concepts")

    with pytest.raises(ValueError, match=r"record the same concept"):
        load_fixtures(tmp_path)


def test_recordings_need_the_manifests_rules(tmp_path):
    recording(tmp_path, "C1")

    with pytest.raises(
        ValueError, match=r"concept recordings need the concept rules of manifest\.yaml"
    ):
        load_fixtures(tmp_path)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"include": "paths"}, "include paths is outside the manifest's table"),
        ({"status": 500}, "a concept recording answers 200 or 404"),
        ({"body": concept("C2")}, "a concept recording's body is the concept C1"),
        ({"body": ["C1"]}, "a concept recording's body is the concept C1"),
    ],
)
def test_an_unusable_recording_is_refused_naming_its_problem(tmp_path, change, problem):
    write_manifest(tmp_path)
    recording(tmp_path, "C1", **change)

    with pytest.raises(ValueError, match=re.escape(f"C1.json: {problem}")):
        load_fixtures(tmp_path)


@pytest.mark.parametrize(
    ("request_change", "problem"),
    [
        ({"surface": "evs-fhir"}, "a concept recording is a GET of /api/v1/concept/"),
        (
            {"path": "/api/v1/concept/ncit_26.09d"},
            "a concept recording is a GET of /api/v1/concept/",
        ),
        ({"params": {}}, "a concept recording names exactly the include it was recorded with"),
        (
            {"params": {"include": ["full"], "limit": ["5"]}},
            "a concept recording names exactly the include it was recorded with",
        ),
    ],
)
def test_a_recording_is_a_plain_concept_request(tmp_path, request_change, problem):
    write_manifest(tmp_path)
    recording(tmp_path, "C1")
    path = tmp_path / "recorded/evs/concepts/C1.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["request"] |= request_change
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=problem):
        load_fixtures(tmp_path)


def test_a_recording_has_one_plain_response(tmp_path):
    write_manifest(tmp_path)
    recording(tmp_path, "C1")
    path = tmp_path / "recorded/evs/concepts/C1.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["response"]["delay_seconds"] = 1
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        ValueError, match="a concept recording has one response, of status and body"
    ):
        load_fixtures(tmp_path)
