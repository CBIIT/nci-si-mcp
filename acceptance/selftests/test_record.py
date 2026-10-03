"""The recorder writes the fixture set the manifest describes, or nothing when it does not hold."""

import json
from unittest import mock

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
                "relations": ["maps"],
            },
        },
        "surfaces": {"evs": "https://evs.example"},
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

    def __call__(self, surface, path, params, **sent):
        query = "&".join(f"{name}={','.join(values)}" for name, values in sorted(params.items()))
        self.calls.append((surface, path, query, *([sent] if sent else [])))
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


def test_each_request_becomes_a_dated_recorded_fixture_as_served():
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
    assert concept["response"]["body"] == C1


MATCH = {
    "fixture": "recorded/cadsr/match.json",
    "surface": "cadsr",
    "method": "POST",
    "path": "/vmMatch/v1/vmMatch",
    "headers": {"Accept": "application/json", "matchType": "Restricted"},
    "body": [{"name": "Male"}],
}
HTML = {"fixture": "recorded/cadsr/page.json", "surface": "cadsr", "path": "/page", "headers": {}}


def test_a_request_s_method_headers_and_body_are_sent_as_given_and_kept_in_its_fixture():
    section = manifest(requests=[MATCH, HTML])
    answers = upstream(
        {
            ("cadsr", "/vmMatch/v1/vmMatch", ""): (200, {"matchResults": []}),
            ("cadsr", "/page", ""): (200, "<BODY>"),
        }
    )

    documents = Recorder(section, answers, "2026-10-03").record(plan(section))

    sent = {call[1]: call[3] for call in answers.calls if call[0] == "cadsr"}
    assert sent == {
        "/vmMatch/v1/vmMatch": {k: MATCH[k] for k in ("method", "headers", "body")},
        "/page": {"method": "GET", "headers": {}, "body": None},
    }
    assert documents["recorded/cadsr/match.json"]["request"] == {
        key: MATCH[key] for key in ("surface", "method", "path", "headers", "body")
    }
    # No header named, none recorded: the fixture answers a request that names none.
    assert "headers" not in documents["recorded/cadsr/page.json"]["request"]
    assert documents["recorded/cadsr/page.json"]["response"]["body"] == "<BODY>"


def test_a_derived_fixture_is_the_prescribed_request_with_a_recordings_answer():
    entry = {
        "fixture": "crafted/OP-E06/concept.json",
        "from": "recorded/evs/concepts/C1.json",
        "path": "/api/v1/concept/ncit_26.09d_x/C1?include=full",
        "requirement": "OP-E06",
        "ignored": {"limit": "evidence"},
    }
    section = manifest(derived=[entry])

    documents = Recorder(section, upstream(), "2026-10-02").record(plan(section))

    recorded = documents["recorded/evs/concepts/C1.json"]
    assert documents["crafted/OP-E06/concept.json"] == {
        "kind": "crafted",
        "requirement": "OP-E06",
        "derived_from": "recorded/evs/concepts/C1.json",
        "request": {
            "surface": "evs",
            "method": "GET",
            "path": "/api/v1/concept/ncit_26.09d_x/C1",
            "params": {"include": ["full"]},
            "ignored": {"limit": "evidence"},
        },
        "response": recorded["response"],
    }


def test_a_derived_fixture_needs_its_recording():
    entry = {"fixture": "crafted/x.json", "from": "recorded/evs/none.json", "path": "/x"}
    section = manifest(derived=[entry | {"requirement": "R"}])

    with pytest.raises(RecordingError, match=r"crafted/x\.json: recorded/evs/none\.json is not"):
        Recorder(section, upstream(), "2026-10-02").record(plan(section))


def test_nothing_is_recorded_against_another_release_than_the_pinned_one():
    live = upstream({RELEASE_QUERY: (200, [{"terminologyVersion": "ncit_26.10d"}])})

    with pytest.raises(RecordingError, match=r"answered 200 with \['ncit_26\.10d'\]"):
        Recorder(manifest(), live, "2026-10-02").record(plan(manifest()))


@pytest.mark.parametrize(
    "answer",
    [
        (200, [{"terminologyVersion": "ncit_26.09d"}, {"terminologyVersion": "ncit_26.09d"}]),
        (503, [{"terminologyVersion": "ncit_26.09d"}]),
        (200, []),
    ],
)
def test_the_pin_holds_only_for_one_row_naming_the_pinned_release(answer):
    with pytest.raises(RecordingError, match="re-pinning is a re-recording"):
        Recorder(manifest(), upstream({RELEASE_QUERY: answer}), "2026-10-02").record(
            plan(manifest())
        )


def test_an_answer_with_another_status_than_expected_stops_the_recording():
    live = upstream({("evs", "/api/v1/version", "detail=all"): (503, "<html>busy</html>")})

    with pytest.raises(RecordingError, match=r"version\.json: answered 503, where 200 is expected"):
        Recorder(manifest(), live, "2026-10-02").record(plan(manifest()))


def test_a_404_where_200_is_expected_stops_the_recording():
    live = upstream({("evs", "/api/v1/version", "detail=all"): (404, {"message": "gone"})})

    with pytest.raises(RecordingError, match=r"version\.json: answered 404, where 200"):
        Recorder(manifest(), live, "2026-10-02").record(plan(manifest()))


def test_a_pin_answer_that_is_not_a_list_of_rows_is_a_problem():
    live = upstream({RELEASE_QUERY: (200, {"terminologyVersion": "ncit_26.09d"})})

    with pytest.raises(RecordingError, match=r"answered 200 with \[\], the fixture set"):
        Recorder(manifest(), live, "2026-10-02").record(plan(manifest()))


def test_an_entry_may_expect_another_status():
    entry = {"fixture": "scenarios/x/y/gone.json", "surface": "evs", "path": "/gone", "status": 404}
    section = manifest(requests=[entry], concepts={}, samples=[])
    live = upstream({("evs", "/gone", ""): (404, {"message": "gone"})})

    documents = Recorder(section, live, "2026-10-02").record(plan(section))

    assert documents["scenarios/x/y/gone.json"]["response"] == {
        "status": 404,
        "body": {"message": "gone"},
    }


def test_a_concept_recording_the_fixture_server_would_refuse_stops_the_recording():
    section = manifest(concepts={"paths": ["C1"]}, samples=[])
    live = upstream({("evs", "/api/v1/concept/ncit_26.09d/C1", "include=paths"): (200, C1)})

    with pytest.raises(RecordingError, match=r"C1\.json: include paths is outside the manifest"):
        Recorder(section, live, "2026-10-02").record(plan(section))


def test_an_unusable_concept_answer_is_withheld_and_named_even_where_a_sample_needs_it():
    page = "<html>Down for maintenance</html>"
    live = upstream({("evs", "/api/v1/concept/ncit_26.09d/C1", "include=full"): (200, page)})

    with pytest.raises(RecordingError) as raised:
        Recorder(manifest(), live, "2026-10-02").record(plan(manifest()))

    assert "recorded/evs/concepts/C1.json: a concept recording's body is the concept C1" in (
        raised.value.problems
    )


def test_an_unreachable_service_is_a_problem_naming_the_request():
    def unreachable(surface, path, params):
        raise OSError("Connection refused")

    with pytest.raises(RecordingError) as raised:
        Recorder(manifest(), unreachable, "2026-10-02").record(plan(manifest()))

    assert "the release query: Connection refused" in raised.value.problems
    assert "recorded/evs/version.json: Connection refused" in raised.value.problems


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
        (
            "/api/v1/concept/ncit_26.09d?list=C1,C2&include=synonyms",
            (200, [C2, C2, {"code": "C1", "name": "One", "synonyms": C1["synonyms"]}]),
            "the composed answer differs from EVS's",
        ),
        (
            "/api/v1/concept/ncit_26.09d/C1/maps",
            (200, [{"targetName": "Zwei", "targetTerminology": "NCI"}, *C1["maps"]]),
            "the composed answer differs from EVS's",
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


def test_a_batch_sample_compares_without_order():
    live = upstream(
        {
            ("evs", "/api/v1/concept/ncit_26.09d", "include=synonyms&list=C1,C2"): (
                200,
                [{"code": "C1", "name": "One", "synonyms": C1["synonyms"]}, C2],
            ),
            ("evs", "/api/v1/concept/ncit_26.09d/C1/maps", ""): (200, C1["maps"]),
        }
    )
    section = manifest(
        samples=[
            "/api/v1/concept/ncit_26.09d?list=C1,C2&include=synonyms",
            "/api/v1/concept/ncit_26.09d/C1/maps",
        ]
    )

    assert Recorder(section, live, "2026-10-02").record(plan(section))


def test_a_sample_beyond_what_a_recording_covers_cannot_be_answered():
    section = manifest(
        concepts={"synonyms": ["C1", "C2"]}, samples=["/api/v1/concept/ncit_26.09d/C1/maps"]
    )
    live = upstream(
        {
            ("evs", "/api/v1/concept/ncit_26.09d/C1", "include=synonyms"): (200, C1),
            ("evs", "/api/v1/concept/ncit_26.09d/C2", "include=synonyms"): (200, C2),
            ("evs", "/api/v1/concept/ncit_26.09d/C1/maps", ""): (200, []),
        }
    )

    with pytest.raises(RecordingError, match="maps: the recordings cannot answer it"):
        Recorder(section, live, "2026-10-02").record(plan(section))


def test_a_derived_fixture_from_the_pinned_release_is_written():
    entry = {
        "fixture": "crafted/OP-E06/version.json",
        "from": "recorded/evs/version.json",
        "path": "/api/v1/version_pinned",
        "requirement": "OP-E06",
    }
    live = upstream({("evs", "/api/v1/version", "detail=all"): (200, {"version": "26.09d"})})
    section = manifest(derived=[entry])

    documents = Recorder(section, live, "2026-10-02").record(plan(section))

    assert documents["crafted/OP-E06/version.json"]["response"]["body"] == {"version": "26.09d"}


def test_a_derived_fixture_from_another_release_than_the_pinned_one_stops_the_recording():
    entry = {
        "fixture": "crafted/OP-E06/version.json",
        "from": "recorded/evs/version.json",
        "path": "/api/v1/version_pinned",
        "requirement": "OP-E06",
    }
    live = upstream({("evs", "/api/v1/version", "detail=all"): (200, {"version": "26.08e"})})

    with pytest.raises(RecordingError, match=r"version\.json serves 26\.08e"):
        Recorder(manifest(derived=[entry]), live, "2026-10-02").record(plan(manifest()))


def test_files_the_manifest_does_not_produce_are_named(tmp_path):
    write(
        tmp_path,
        {
            "recorded/evs/version.json": {},
            "recorded/evs/old.json": {},
            "crafted/OP-E06/old.json": {"derived_from": "recorded/evs/old.json"},
            "crafted/A1/hand.json": {"kind": "crafted"},
        },
    )

    assert stale(tmp_path, manifest()) == [
        "recorded/evs/old.json: not produced by manifest.yaml; remove it or add it there",
        "crafted/OP-E06/old.json: not produced by manifest.yaml; remove it or add it there",
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
        assert [
            name
            for entry in running.log()
            for name in entry["headers"]
            if name.lower() == "x-evsrestapi-license-key"
        ] == []


def test_the_live_fetch_sends_the_method_headers_and_body_it_is_given(tmp_path):
    fixture = {
        "kind": "crafted",
        "requirement": "self-test",
        "request": {
            "surface": "cadsr",
            "method": "POST",
            "path": "/vmMatch/v1/vmMatch",
            "headers": {"matchType": "Restricted"},
            "body": [{"name": "Male"}],
        },
        "response": {"status": 200, "body": {"matched": True}},
    }
    (tmp_path / "match.json").write_text(json.dumps(fixture), encoding="utf-8")
    with FixtureServer(load_fixtures(tmp_path)) as running:
        fetch = live_fetch({"cadsr": running.base_url("cadsr")})

        answer = fetch(
            "cadsr",
            "/vmMatch/v1/vmMatch",
            {},
            "POST",
            {"matchType": "Restricted"},
            [{"name": "Male"}],
        )

        assert answer == (200, {"matched": True})
        assert "Accept" not in running.log()[0]["headers"]


def test_the_live_fetch_keeps_a_colon_in_a_path_as_it_is():
    # caDSR's form API lives at a path with a colon, and answers 404 to it encoded (%3A).
    path = "/NCIFormAPI.v2_0:NciFormApiRad/Form/5406471"
    with (
        mock.patch.object(record, "urlopen", side_effect=OSError("stopped")) as opened,
        pytest.raises(OSError, match="stopped"),
    ):
        live_fetch({"cadsr": "https://cadsr.example/rad"})("cadsr", path, {})

    assert opened.call_args.args[0].full_url == f"https://cadsr.example/rad{path}"
