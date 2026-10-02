"""The recorder writes the fixture set the manifest describes, or nothing when it does not hold."""

import json

import pytest
import yaml

from nci_si_acceptance import record
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures
from nci_si_acceptance.record import Recorder, RecordingError, live_fetch, plan, stale, write

RELEASE_QUERY = (
    "evs",
    "/api/v1/metadata/terminologies",
    "latest=true&tag=monthly&terminology=ncit",
)
PINNED = [{"terminology": "ncit", "terminologyVersion": "ncit_26.09d"}]
C1 = {
    "code": "C1",
    "name": "One",
    "synonyms": [{"name": "Uno", "source": "NCI"}],
    "maps": [{"targetName": "Eins", "targetTerminology": "MedDRA"}],
}
C2 = {"code": "C2", "name": "Two"}


def manifest(**record_section):
    return {
        "evs": {
            "release": "ncit_26.09d",
            "concepts": {
                "base": ["code", "name"],
                "default": "minimal",
                "include": {"minimal": [], "synonyms": ["synonyms"], "full": ["synonyms", "maps"]},
            },
        },
        "surfaces": {"evs": "https://evs.example"},
        "deny": {"terminologies": ["mdr", "MedDRA"], "mapsets": ["NCIt_Maps_To_MedDRA"]},
        "record": {
            "requests": [
                {
                    "fixture": "recorded/evs/version.json",
                    "surface": "evs",
                    "path": "/api/v1/version?detail=all",
                    "ignored": {"verbose": "evidence"},
                }
            ],
            "concepts": {"full": ["C1", "C2"]},
            "samples": ["/api/v1/concept/ncit_26.09d?list=C1,C2&include=synonyms"],
        }
        | record_section,
    }


class Upstream:
    """Answers by surface, path and query, as the live services would; records each call."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, surface, path, params):
        query = "&".join(f"{name}={','.join(values)}" for name, values in sorted(params.items()))
        self.calls.append((surface, path, query))
        return self.answers[surface, path, query]


def upstream(changes=None):
    answers = {
        RELEASE_QUERY: (200, PINNED),
        ("evs", "/api/v1/version", "detail=all"): (200, {"version": "2.5.0"}),
        ("evs", "/api/v1/concept/ncit_26.09d/C1", "include=full"): (200, C1),
        ("evs", "/api/v1/concept/ncit_26.09d/C2", "include=full"): (200, C2),
        ("evs", "/api/v1/concept/ncit_26.09d", "include=synonyms&list=C1,C2"): (
            200,
            [C2, {"code": "C1", "name": "One", "synonyms": C1["synonyms"]}],
        ),
    }
    return Upstream(answers | (changes or {}))


def test_each_request_becomes_a_dated_recorded_fixture_without_its_licensed_items():
    documents = Recorder(manifest(), upstream(), "2026-10-02").record(plan(manifest()))

    assert documents["recorded/evs/version.json"] == {
        "kind": "recorded",
        "recorded_on": "2026-10-02",
        "request": {
            "surface": "evs",
            "method": "GET",
            "path": "/api/v1/version",
            "params": {"detail": ["all"]},
            "ignored": {"verbose": "evidence"},
        },
        "response": {"status": 200, "body": {"version": "2.5.0"}},
    }
    concept = documents["recorded/evs/concepts/C1.json"]
    assert concept["request"]["params"] == {"include": ["full"]}
    assert concept["response"]["body"]["maps"] == []
    assert concept["redacted"] == ["/maps/0 (MedDRA)"]


def test_nothing_is_recorded_against_another_release_than_the_pinned_one():
    live = upstream({RELEASE_QUERY: (200, [{"terminologyVersion": "ncit_26.10d"}])})

    with pytest.raises(
        RecordingError, match=r"the live monthly NCIt release is \['ncit_26\.10d'\]"
    ):
        Recorder(manifest(), live, "2026-10-02").record(plan(manifest()))


def test_a_request_for_licensed_content_is_refused_unless_it_was_refused():
    entry = {"fixture": "recorded/evs/mdr.json", "surface": "evs", "path": "/api/v1/concept/mdr/1"}
    section = manifest(requests=[entry], concepts={}, samples=[])
    path = ("evs", "/api/v1/concept/mdr/1", "")

    with pytest.raises(RecordingError, match=r"recorded/evs/mdr\.json: names licensed content"):
        Recorder(section, upstream({path: (200, {"code": "1"})}), "2026-10-02").record(
            plan(section)
        )
    refused = Recorder(section, upstream({path: (403, {"message": "key"})}), "2026-10-02")
    response = refused.record(plan(section))["recorded/evs/mdr.json"]["response"]
    assert response == {"status": 403, "body": {"message": "key"}}


@pytest.mark.parametrize(
    ("sample", "answer", "problem"),
    [
        (
            "/api/v1/concept/ncit_26.09d?list=C1,C2&include=synonyms",
            (200, [C2]),
            "the composed answer differs from EVS's",
        ),
        (
            "/api/v1/concept/ncit_26.09d/C1?include=paths",
            (200, C1),
            "the recordings cannot answer it",
        ),
    ],
)
def test_a_sample_the_recordings_do_not_reproduce_stops_the_recording(sample, answer, problem):
    section = manifest(samples=[sample])
    path, _, query = sample.partition("?")
    query = "&".join(sorted(query.split("&")))
    live = upstream({("evs", path, query): answer})

    with pytest.raises(RecordingError, match=problem):
        Recorder(section, live, "2026-10-02").record(plan(section))


def test_files_the_manifest_does_not_produce_are_named(tmp_path):
    planned = plan(manifest())
    write(tmp_path, {"recorded/evs/version.json": {}, "recorded/evs/old.json": {}})

    assert stale(tmp_path, planned) == [
        "recorded/evs/old.json: not produced by manifest.yaml; remove it or add it there"
    ]


def write_manifest(tmp_path, section):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "manifest.yaml").write_text(yaml.safe_dump(section), encoding="utf-8")


def test_the_command_writes_the_set_the_fixture_server_then_loads(tmp_path, monkeypatch, capsys):
    write_manifest(tmp_path, manifest())
    monkeypatch.setattr(record, "live_fetch", lambda bases: upstream())

    assert record.main(["--fixtures", str(tmp_path)]) == 0

    assert "Recorded 3 fixtures on" in capsys.readouterr().out
    fixtures = load_fixtures(tmp_path)
    assert set(fixtures.recordings[None]) == {("ncit_26.09d", "C1"), ("ncit_26.09d", "C2")}
    assert json.loads((tmp_path / "recorded/evs/version.json").read_text(encoding="utf-8"))[
        "kind"
    ] == ("recorded")


def test_the_command_writes_nothing_when_the_recording_does_not_hold(tmp_path, monkeypatch, capsys):
    write_manifest(tmp_path, manifest())
    write(tmp_path, {"recorded/evs/old.json": {}})
    monkeypatch.setattr(record, "live_fetch", lambda bases: upstream())

    assert record.main(["--fixtures", str(tmp_path)]) == 1

    assert "Nothing was written:\nrecorded/evs/old.json: not produced" in capsys.readouterr().err
    assert not (tmp_path / "recorded/evs/version.json").exists()


def test_the_live_fetch_returns_status_and_parsed_body_errors_included(tmp_path):
    fixture = {
        "kind": "crafted",
        "requirement": "self-test",
        "request": {
            "surface": "evs-fhir",
            "method": "GET",
            "path": "/ValueSet/$expand",
            "params": {"url": ["http://x?fhir_vs=C1"]},
        },
        "response": {"status": 200, "body": {"total": 1}},
    }
    page = fixture | {
        "request": fixture["request"] | {"path": "/page", "params": {}},
        "response": {"status": 404, "body": "<html>gone</html>"},
    }
    for name, document in (("expand.json", fixture), ("page.json", page)):
        (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")
    with FixtureServer(load_fixtures(tmp_path)) as running:
        fetch = live_fetch({"evs-fhir": running.base_url("evs-fhir")})

        assert fetch("evs-fhir", "/ValueSet/$expand", {"url": ["http://x?fhir_vs=C1"]}) == (
            200,
            {"total": 1},
        )
        assert fetch("evs-fhir", "/page", {}) == (404, "<html>gone</html>")
        assert running.log()[0]["headers"]["Accept"] == "application/json"
