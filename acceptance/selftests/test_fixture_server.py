"""The fixture server answers from fixtures, refuses what it cannot answer, and records it all."""

import json
import time
from http import HTTPStatus
from http.client import RemoteDisconnected
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures, request_key

VERSION = request_key("evs", "GET", "/api/v1/version", {})
DELAY = 0.3


def fixture_file(directory, name, **document):
    """Write a fixture: a recorded answer to GET /evs/api/v1/version unless told otherwise.

    A field given as None is left out.
    """

    document = {
        "kind": "recorded",
        "recorded_on": "2026-10-02",
        "request": {"surface": "evs", "method": "GET", "path": "/api/v1/version"},
        "response": {"status": 200, "body": {"version": "2.5.0"}},
    } | document
    document = {field: value for field, value in document.items() if value is not None}
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def fetch(url, method="GET", headers=None):
    """Status, content type and body of one request, HTTP errors included."""

    request = Request(url, method=method, headers=headers or {})  # noqa: S310 - a local test server
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, response.headers["Content-Type"], response.read()
    except HTTPError as error:
        return error.code, error.headers["Content-Type"], error.read()


@pytest.fixture
def server(tmp_path):
    fixture_file(tmp_path, "recorded/evs/version.json")
    fixture_file(
        tmp_path,
        "recorded/evs/concept.json",
        request={
            "surface": "evs",
            "method": "GET",
            "path": "/api/v1/concept/ncit_26.09d",
            "params": {"list": ["C1,C2"], "include": ["summary"]},
        },
        response={"status": 200, "body": [{"code": "C1"}]},
    )
    fixture_file(
        tmp_path,
        "crafted/cadsr/html.json",
        kind="crafted",
        requirement="C-7",
        request={"surface": "cadsr", "method": "GET", "path": "/rad/contexts"},
        response={"status": 200, "body": "<html>no JSON</html>"},
    )
    with FixtureServer(load_fixtures(tmp_path)) as running:
        yield running


def test_a_request_is_answered_by_its_fixture_and_recorded(server):
    status, content_type, body = fetch(
        server.base_url("evs") + "/api/v1/version", headers={"Accept": "application/json"}
    )

    assert (status, content_type, json.loads(body)) == (
        200,
        "application/json",
        {"version": "2.5.0"},
    )
    (entry,) = server.log()
    assert entry["surface"] == "evs"
    assert entry["path"] == "/api/v1/version"
    assert entry["headers"]["Accept"] == "application/json"
    assert entry["fixture"] == "recorded/evs/version.json"


def test_query_parameters_match_decoded_and_in_any_order(server):
    url = server.base_url("evs") + "/api/v1/concept/ncit_26.09d?include=summary&list=C1%2CC2"

    status, _, body = fetch(url)

    assert (status, json.loads(body)) == (200, [{"code": "C1"}])
    assert server.log()[0]["params"] == {"include": ["summary"], "list": ["C1,C2"]}


def test_a_blank_parameter_is_part_of_the_request(server):
    status, _, _ = fetch(server.base_url("evs") + "/api/v1/version?q=")

    assert status == HTTPStatus.NOT_IMPLEMENTED
    assert server.log()[0]["params"] == {"q": [""]}


def test_paths_match_decoded(tmp_path):
    fixture_file(
        tmp_path,
        "search.json",
        request={"surface": "evs", "method": "GET", "path": "/api/v1/concept/ncit/C 1"},
    )
    with FixtureServer(load_fixtures(tmp_path)) as running:
        status, _, _ = fetch(running.base_url("evs") + "/api/v1/concept/ncit/C%201")

    assert status == HTTPStatus.OK


def test_a_request_of_any_method_is_recorded_with_its_body(server):
    request = Request(  # noqa: S310 - a local test server
        server.base_url("ssis-sparql") + "/sparql", data=b"query=ASK{}", method="POST"
    )
    try:
        urlopen(request, timeout=10)  # noqa: S310
    except HTTPError as error:
        status = error.code

    assert status == HTTPStatus.NOT_IMPLEMENTED
    (entry,) = server.log()
    assert (entry["method"], entry["surface"], entry["body"]) == (
        "POST",
        "ssis-sparql",
        "query=ASK{}",
    )


def test_a_request_without_a_fixture_is_refused_with_501_and_recorded(server):
    status, _, body = fetch(server.base_url("evs") + "/api/v1/version?extra=1")

    assert status == HTTPStatus.NOT_IMPLEMENTED
    assert json.loads(body)["error"] == "no fixture answers this request"
    assert server.log()[0]["fixture"] is None


def test_a_text_body_is_sent_as_html(server):
    status, content_type, body = fetch(server.base_url("cadsr") + "/rad/contexts")

    assert (status, content_type, body) == (
        200,
        "text/html; charset=utf-8",
        b"<html>no JSON</html>",
    )


def test_the_headers_of_a_fixture_are_sent_and_override_the_content_type(tmp_path):
    fixture_file(
        tmp_path,
        "limited.json",
        kind="crafted",
        requirement="E-7",
        response={
            "status": 429,
            "headers": {"Retry-After": "2", "content-type": "application/problem+json"},
            "body": {"title": "slow down"},
        },
    )
    with FixtureServer(load_fixtures(tmp_path)) as running:
        url = running.base_url("evs") + "/api/v1/version"
        try:
            urlopen(Request(url), timeout=10)  # noqa: S310 - a local test server
        except HTTPError as error:
            status, headers, body = error.code, error.headers, error.read()

    assert (status, headers["Retry-After"], headers.get_all("Content-Type")) == (
        429,
        "2",
        ["application/problem+json"],
    )
    assert json.loads(body) == {"title": "slow down"}


def test_a_head_request_gets_the_headers_of_its_fixture_without_the_body(tmp_path):
    fixture_file(
        tmp_path,
        "export.json",
        request={"surface": "cadsr-ftp", "method": "HEAD", "path": "/releasedCDEsXML-OD.zip"},
        response={"status": 200, "headers": {"Last-Modified": "Wed, 30 Sep 2026 04:00:00 GMT"}},
    )
    with FixtureServer(load_fixtures(tmp_path)) as running:
        url = running.base_url("cadsr-ftp") + "/releasedCDEsXML-OD.zip"
        request = Request(url, method="HEAD")  # noqa: S310 - a local test server
        with urlopen(request, timeout=10) as response:  # noqa: S310
            status, modified, body = (
                response.status,
                response.headers["Last-Modified"],
                response.read(),
            )
        refused, _, _ = fetch(running.base_url("cadsr-ftp") + "/releasedCDEsXML-OD.zip")

    assert (status, modified, body) == (200, "Wed, 30 Sep 2026 04:00:00 GMT", b"")
    assert refused == HTTPStatus.NOT_IMPLEMENTED


def test_a_delete_outside_the_log_is_an_upstream_request(server):
    status, _, _ = fetch(server.base_url("evs") + "/api/v1/version", method="DELETE")

    assert status == HTTPStatus.NOT_IMPLEMENTED
    assert server.log()[0]["method"] == "DELETE"


def test_the_log_is_read_and_cleared_over_http(server):
    fetch(server.base_url("evs") + "/api/v1/version")

    _, _, body = fetch(server.url + "/_log")
    assert [entry["path"] for entry in json.loads(body)] == ["/api/v1/version"]
    status, _, _ = fetch(server.url + "/_log", method="DELETE")
    assert status == HTTPStatus.NO_CONTENT
    assert server.log() == []


def test_two_fixtures_for_the_same_request_are_refused(tmp_path):
    fixture_file(tmp_path, "a.json")
    fixture_file(tmp_path, "b.json")

    with pytest.raises(ValueError, match=r"b\.json and a\.json answer the same request"):
        load_fixtures(tmp_path)


def test_a_fixture_directory_must_exist(tmp_path):
    with pytest.raises(ValueError, match="is not a fixture directory"):
        load_fixtures(tmp_path / "missing")


@pytest.mark.parametrize(
    ("document", "problem"),
    [
        (
            {"response": {"status": 200, "headers": {"Transfer-Encoding": "chunked"}}},
            "the server frames the response; remove transfer-encoding",
        ),
        ({"recorded_on": None}, "a recorded fixture names recorded_on"),
        ({"kind": "crafted"}, "a crafted fixture names its requirement"),
        ({"kind": "invented"}, "kind is recorded or crafted"),
        ({"responses": [{"status": 200}]}, "a fixture has either a response or responses"),
        ({"response": {"fault": "hang"}}, "a fault is one of reset"),
    ],
)
def test_an_unusable_fixture_is_refused_naming_its_problem(tmp_path, document, problem):
    fixture_file(tmp_path, "f.json", **document)

    with pytest.raises(ValueError, match=f"f.json: {problem}"):
        load_fixtures(tmp_path)


def test_a_loaded_fixture_is_keyed_by_its_request(tmp_path):
    fixture_file(tmp_path, "recorded/evs/version.json")

    fixtures = load_fixtures(tmp_path)

    assert list(fixtures.ordinary) == [VERSION]
    assert fixtures.ordinary[VERSION].name == "recorded/evs/version.json"
    assert fixtures.scenarios == {}


def test_a_scenario_answers_in_place_of_the_ordinary_fixture_only_while_active(tmp_path):
    fixture_file(tmp_path, "recorded/evs/version.json")
    fixture_file(
        tmp_path,
        "scenarios/release/unknown/version.json",
        kind="crafted",
        requirement="A3.4",
        response={"status": 404, "body": {"message": "Terminology not found"}},
    )
    url = None
    with FixtureServer(load_fixtures(tmp_path)) as running:
        url = running.base_url("evs") + "/api/v1/version"
        ordinary = fetch(url)[0]
        running.activate("release/unknown")
        during = fetch(url)[0]
        logged = running.log()[-1]["fixture"]
        running.activate()
        after = fetch(url)[0]

    assert (ordinary, during, after) == (200, 404, 200)
    assert logged == "scenarios/release/unknown/version.json"


def test_an_unknown_scenario_is_refused(server):
    with pytest.raises(ValueError, match="no such scenario: nothing/here"):
        server.activate("nothing/here")


def test_a_scenario_fixture_lies_two_levels_below_scenarios(tmp_path):
    fixture_file(tmp_path, "scenarios/release/version.json")

    with pytest.raises(ValueError, match="a scenario fixture lies in scenarios/<group>/<name>/"):
        load_fixtures(tmp_path)


def test_the_same_request_may_have_a_fixture_in_each_scenario(tmp_path):
    for scenario in ("release/unknown", "release/mismatch"):
        fixture_file(
            tmp_path, f"scenarios/{scenario}/version.json", kind="crafted", requirement="A3.4"
        )

    fixtures = load_fixtures(tmp_path)

    assert sorted(fixtures.scenarios) == ["release/mismatch", "release/unknown"]


def test_responses_answer_in_turn_the_last_repeating_and_a_reset_rewinds(tmp_path):
    fixture_file(
        tmp_path,
        "limited.json",
        kind="crafted",
        requirement="E-7",
        response=None,
        responses=[
            {"status": 429, "headers": {"Retry-After": "1"}},
            {"status": 200, "body": {"version": "2.5.0"}},
        ],
    )
    with FixtureServer(load_fixtures(tmp_path)) as running:
        url = running.base_url("evs") + "/api/v1/version"
        first = [fetch(url)[0] for _ in range(3)]
        running.reset()
        again = fetch(url)[0]

    assert (first, again) == ([429, 200, 200], 429)


def test_a_reset_fault_closes_the_connection_without_an_answer(tmp_path):
    fixture_file(
        tmp_path, "down.json", kind="crafted", requirement="A5.3", response={"fault": "reset"}
    )
    with (
        FixtureServer(load_fixtures(tmp_path)) as running,
        pytest.raises(RemoteDisconnected),
    ):
        urlopen(running.base_url("evs") + "/api/v1/version", timeout=10)  # noqa: S310


def test_a_delayed_response_waits_before_it_is_sent(tmp_path):
    fixture_file(
        tmp_path,
        "slow.json",
        kind="crafted",
        requirement="A5.3",
        response={"status": 200, "delay_seconds": DELAY},
    )
    with FixtureServer(load_fixtures(tmp_path)) as running:
        started = time.monotonic()
        fetch(running.base_url("evs") + "/api/v1/version")
        waited = time.monotonic() - started

    assert waited >= DELAY


def test_a_fixture_with_a_body_answers_only_that_body_and_one_without_answers_the_rest(tmp_path):
    match = {"surface": "cadsr", "method": "POST", "path": "/rad/cdeMatch"}
    fixture_file(
        tmp_path,
        "timeout.json",
        kind="crafted",
        requirement="C-6",
        request=match | {"body": '{"description": "slow"}'},
        response={"status": 504},
    )
    fixture_file(tmp_path, "match.json", request=match, response={"status": 200, "body": []})
    with FixtureServer(load_fixtures(tmp_path)) as running:
        url = running.base_url("cadsr") + "/rad/cdeMatch"
        statuses = [
            fetch_post(url, b'{"description": "slow"}'),
            fetch_post(url, b'{"description": "other"}'),
        ]

    assert statuses == [504, 200]


def fetch_post(url, body):
    try:
        with urlopen(Request(url, data=body, method="POST"), timeout=10) as response:  # noqa: S310
            return response.status
    except HTTPError as error:
        return error.code


def test_a_selected_scenario_wins_even_where_an_ordinary_fixture_names_the_exact_body(tmp_path):
    match = {"surface": "cadsr", "method": "POST", "path": "/rad/cdeMatch"}
    fixture_file(tmp_path, "recorded/match.json", request=match | {"body": {"q": "x"}})
    fixture_file(
        tmp_path,
        "scenarios/upstream/unavailable/match.json",
        kind="crafted",
        requirement="A5.3",
        request=match,
        response={"status": 503},
    )
    with FixtureServer(load_fixtures(tmp_path)) as running:
        running.activate("upstream/unavailable")
        status = fetch_post(running.base_url("cadsr") + "/rad/cdeMatch", b'{"q": "x"}')

    assert status == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.parametrize(
    ("fixture_body", "request_body"),
    [
        ({"description": "age", "top": 5}, b'{ "top":5,\n  "description": "age" }'),
        ("SELECT ?s WHERE {\n  ?s ?p ?o\n}", b"SELECT ?s   WHERE { ?s ?p ?o }"),
    ],
)
def test_bodies_match_as_json_or_with_whitespace_collapsed(tmp_path, fixture_body, request_body):
    match = {"surface": "ssis-sparql", "method": "POST", "path": "/sparql", "body": fixture_body}
    fixture_file(tmp_path, "query.json", request=match)
    with FixtureServer(load_fixtures(tmp_path)) as running:
        status = fetch_post(running.base_url("ssis-sparql") + "/sparql", request_body)

    assert status == HTTPStatus.OK


def test_a_parameter_declared_ignored_is_left_out_of_the_match_and_kept_in_the_log(tmp_path):
    evidence = "STATUS.md, 11 September 2026: $expand accepts count and ignores it"
    fixture_file(
        tmp_path,
        "expand.json",
        request={
            "surface": "evs-fhir",
            "method": "GET",
            "path": "/ValueSet/$expand",
            "params": {"url": ["http://example/vs"]},
            "ignored": {"count": evidence},
        },
    )
    with FixtureServer(load_fixtures(tmp_path)) as running:
        url = running.base_url("evs-fhir") + "/ValueSet/$expand?url=http://example/vs"
        statuses = [fetch(url)[0], fetch(url + "&count=50")[0], fetch(url + "&offset=1")[0]]
        logged = running.log()[1]["params"]

    assert statuses == [200, 200, 501]
    assert logged["count"] == ["50"]


@pytest.mark.parametrize(
    ("ignored", "params", "problem"),
    [
        ({"count": ""}, {}, "an ignored parameter names the evidence that the service ignores it"),
        ({"count": "seen"}, {"count": ["5"]}, "an ignored parameter is not also matched"),
    ],
)
def test_an_ignored_parameter_is_declared_with_evidence_and_only_once(
    tmp_path, ignored, params, problem
):
    request = {
        "surface": "evs",
        "method": "GET",
        "path": "/x",
        "params": params,
        "ignored": ignored,
    }
    fixture_file(tmp_path, "f.json", request=request)

    with pytest.raises(ValueError, match=f"f.json: {problem}"):
        load_fixtures(tmp_path)
