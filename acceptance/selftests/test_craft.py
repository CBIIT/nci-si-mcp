"""Each crafted scenario provokes the behaviour it is named for."""

import json
import shutil
from http import HTTPStatus
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest
import yaml

from nci_si_acceptance.craft import (
    EXCLUSION_ROLES,
    LICENCE_KEY,
    Recorded,
    craft,
    main,
    mismatched,
)
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures
from nci_si_acceptance.record import DISCOVERY, FIXTURES, reported_releases

CRAFTED = craft(FIXTURES)
MANIFEST = yaml.safe_load((FIXTURES / "manifest.yaml").read_text(encoding="utf-8"))
# The hard maximums of a traversal (docs/SPEC.md §4.3).
MAX_NODES, MAX_DEPTH = 1000, 4
PINNED, MISMATCHED_RELEASE = "26.09d", "26.08e"
CONCEPT = "/api/v1/concept/ncit_26.09d"


@pytest.fixture(scope="module")
def upstream():
    """The real fixture set, served."""

    with FixtureServer(load_fixtures(FIXTURES)) as running:
        yield running


def get(running, surface, path, headers=None):
    """Status and parsed body of one request to the fixture server."""

    url = running.base_url(surface) + quote(path, safe="/?=&,:$")
    try:
        with urlopen(Request(url, headers=headers or {}), timeout=10) as response:  # noqa: S310
            return response.status, json.loads(response.read() or "null")
    except HTTPError as error:
        return error.code, None


def serve(running, *scenarios):
    running.activate(*scenarios)
    return lambda path, surface="evs": get(running, surface, path)


def scenario(name):
    return {file: doc for file, doc in CRAFTED.items() if file.startswith(f"scenarios/{name}/")}


def body(file):
    return CRAFTED[file]["response"]["body"]


def test_every_crafted_fixture_names_its_requirement():
    fixtures = [doc for file, doc in CRAFTED.items() if not file.endswith("settings.json")]

    assert {doc["kind"] for doc in fixtures} == {"crafted"}
    assert all(doc["requirement"] for doc in fixtures)


def test_deep_fanout_exceeds_the_node_and_depth_maximums():
    recordings = {
        doc["response"]["body"]["code"]: doc["response"]["body"]
        for doc in scenario("traversal/deep-fanout").values()
    }
    root = recordings["C99000000"]

    assert len(root["children"]) > MAX_NODES
    assert all(child["code"] in recordings for child in root["children"])
    depth, node = 1, recordings[root["children"][0]["code"]]
    while node.get("children") and node["children"][0]["code"] in recordings:
        depth, node = depth + 1, recordings[node["children"][0]["code"]]
    assert depth > MAX_DEPTH
    assert node["children"]  # the walk could go deeper still


def test_deep_fanout_is_served_through_the_relation_endpoints(upstream):
    answer = serve(upstream, "traversal/deep-fanout")

    status, children = answer(f"{CONCEPT}/C99000000/children")
    depth, code = 1, "C99000001"
    while (below := answer(f"{CONCEPT}/{code}/children")[1]) and depth <= MAX_DEPTH:
        depth, code = depth + 1, below[0]["code"]

    assert (status, len(children)) == (200, MAX_NODES + 1)
    assert depth > MAX_DEPTH
    assert answer(f"{CONCEPT}/C99000001?include=parents")[1]["parents"][0]["code"] == "C99000000"
    assert [
        answer(f"{CONCEPT}/{code}?include=minimal")[1]["leaf"]
        for code in ("C99000000", "C99000001", "C99000002")
    ] == [False, False, True]


def test_exclusions_are_named_as_positive_roles_and_two_positive_roles_as_exclusions():
    roles = body("scenarios/traversal/exclusions/concepts/C4817.json")["roles"]
    catalogue = {
        role["code"]: role["name"] for role in body("scenarios/traversal/exclusions/roles.json")
    }
    negative = [role for role in roles if role["code"] in EXCLUSION_ROLES]
    named_negative = [role for role in roles if "Excludes" in role["type"]]

    assert negative
    assert not any("Excludes" in role["type"] for role in negative)
    assert {role["code"] for role in named_negative} == {"R108", "R116"}
    assert all(catalogue[role["code"]] == role["type"] for role in roles)


def test_the_served_exclusion_trap_reads_the_same_through_every_form(upstream):
    answer = serve(upstream, "traversal/exclusions")

    roles = answer(f"{CONCEPT}/C4817/roles")[1]
    full = answer(f"{CONCEPT}/C4817?include=roles")[1]["roles"]
    catalogue = {
        role["code"]: role["name"] for role in answer("/api/v1/metadata/ncit_26.09d/roles")[1]
    }

    assert roles == full
    assert {role["code"] for role in roles if "Excludes" in role["type"]} == {"R108", "R116"}
    assert all(catalogue[role["code"]] == role["type"] for role in roles)


def ordinary_requests():
    """Every ordinary request of the manifest, recorded or derived, with its surface."""

    record = MANIFEST["record"]
    surfaces = {entry["fixture"]: entry["surface"] for entry in record["requests"]}
    derived = [entry | {"surface": surfaces[entry["from"]]} for entry in record["derived"]]
    return [
        entry
        for entry in [*record["requests"], *derived]
        if not entry["fixture"].startswith("scenarios/")
    ]


def test_mismatch_serves_another_release_wherever_the_pinned_one_is_reported(upstream):
    pinned = serve(upstream)
    reporting = {
        entry["fixture"]: entry
        for entry in ordinary_requests()
        if PINNED in reported_releases(pinned(entry["path"], entry["surface"])[1])
    }
    concepts = [path.stem for path in (FIXTURES / "recorded/evs/concepts").glob("*.json")]
    answer = serve(upstream, "release/mismatch")

    served = {
        fixture: reported_releases(answer(entry["path"], entry["surface"])[1])
        for fixture, entry in reporting.items()
    }
    versions = {answer(f"{CONCEPT}/{code}?include=minimal")[1]["version"] for code in concepts}

    assert set(reporting) - DISCOVERY == set(mismatched(Recorded(FIXTURES))) - {
        f"recorded/evs/concepts/{code}.json" for code in concepts
    }
    assert {fixture: PINNED in releases for fixture, releases in served.items()} == {
        fixture: fixture in DISCOVERY for fixture in served
    }
    assert all(MISMATCHED_RELEASE in served[fixture] for fixture in set(served) - DISCOVERY)
    assert versions == {MISMATCHED_RELEASE}


def channels(rows):
    """The release of each channel among the latest NCIt rows, in their order."""

    latest = [row for row in rows if row["terminology"] == "ncit" and row["latest"]]
    return [(channel, row["version"]) for row in latest for channel in sorted(row["tags"])]


def test_two_latest_gives_each_channel_one_release_whatever_the_form_and_weekly_first(upstream):
    answer = serve(upstream, "release/two-latest")
    query = "/api/v1/metadata/terminologies?terminology=ncit&latest=true"

    forms = {
        "listing": channels(answer("/api/v1/metadata/terminologies")[1]),
        "latest": channels(answer(query)[1]),
        "monthly": channels(answer(f"{query}&tag=monthly")[1]),
        "weekly": channels(answer(f"{query}&tag=weekly")[1]),
    }

    both = [("weekly", "26.10a"), ("monthly", PINNED)]
    assert forms == {
        "listing": both,
        "latest": both,
        "monthly": [("monthly", PINNED)],
        "weekly": [("weekly", "26.10a")],
    }


def test_starvation_is_served_through_the_relation_endpoints(upstream):
    answer = serve(upstream, "traversal/starvation")

    roles = answer(f"{CONCEPT}/C99200000/roles")[1]
    associations = answer(f"{CONCEPT}/C99200000/associations")[1]

    assert (len(roles), len(associations)) == (300, 2)
    assert answer(f"{CONCEPT}/{roles[-1]['relatedCode']}?include=summary")[0] == HTTPStatus.OK


@pytest.mark.parametrize("name", list(MANIFEST["scenarios"]))
def test_every_scenario_activates_over_the_ordinary_set(upstream, name):
    upstream.activate(name)

    assert upstream.fixtures.scenarios.get(name) or upstream.fixtures.recordings.get(name)


def test_exclusion_missing_is_the_recorded_catalogue_less_one_exclusion_code():
    catalogue = body("scenarios/relationships/exclusion-missing/roles.json")
    recorded = json.loads((FIXTURES / "recorded/evs/roles.json").read_text(encoding="utf-8"))[
        "response"
    ]["body"]

    missing = [role for role in recorded if role not in catalogue]

    assert len(missing) == 1
    assert missing[0]["code"] in EXCLUSION_ROLES
    assert len(catalogue) == len(recorded) - 1


def test_starvation_has_many_roles_few_associations_and_every_target_recorded():
    documents = scenario("traversal/starvation")
    hub = body("scenarios/traversal/starvation/concepts/C99200000.json")
    targets = [item["relatedCode"] for item in hub["roles"] + hub["associations"]]

    assert (len(hub["roles"]), len(hub["associations"])) == (300, 2)
    assert {f"scenarios/traversal/starvation/concepts/{code}.json" for code in targets} <= set(
        documents
    )


def test_unavailable_closes_then_fails_then_outlasts_the_servers_timeout():
    settings = CRAFTED["scenarios/upstream/unavailable/settings.json"]
    for file, doc in scenario("upstream/unavailable").items():
        if file.endswith("settings.json"):
            continue
        close, failure, silence = doc["responses"]
        assert (close, failure["status"]) == ({"fault": "close"}, 503)
        assert silence["fault"] == "close"
        assert silence["delay_seconds"] > int(settings["NCI_SI_TIMEOUT_SECONDS"])


def test_rate_limited_asks_to_wait_then_answers_as_recorded():
    limited, answer = CRAFTED["scenarios/upstream/rate-limited/release.json"]["responses"]
    recorded = json.loads(
        (FIXTURES / "recorded/evs/release-monthly.json").read_text(encoding="utf-8")
    )

    assert (limited["status"], limited["headers"]) == (429, {"Retry-After": "1"})
    assert answer == recorded["response"]


def fetch(url, headers=None):
    try:
        with urlopen(Request(url, headers=headers or {}), timeout=10) as response:  # noqa: S310
            return response.status
    except HTTPError as error:
        return error.code


def test_the_licence_key_of_the_scenarios_settings_is_what_grants_the_licensed_answer():
    settings = CRAFTED["scenarios/license/restricted/settings.json"]
    with FixtureServer(load_fixtures(FIXTURES)) as running:
        running.activate("license/restricted")
        url = running.base_url("evs") + "/api/v1/concept/mdr_29_0/10000000?include=summary"
        granted = fetch(url, {"X-EVSRESTAPI-License-Key": settings["NCI_SI_EVS_LICENSE_KEY"]})
        refused = fetch(url)

    assert settings["NCI_SI_EVS_LICENSE_KEY"] == LICENCE_KEY
    assert (granted, refused) == (200, 403)


@pytest.mark.parametrize(
    "name", ["release/unknown", "batch/silent-drop", "retired/with-replacement"]
)
def test_the_recorded_scenarios_are_not_crafted(name):
    assert scenario(name) == {}


def test_the_command_writes_what_craft_makes(tmp_path, capsys):
    for directory in ("recorded", "crafted"):
        shutil.copytree(FIXTURES / directory, tmp_path / directory)

    assert main(["--fixtures", str(tmp_path)]) == 0

    assert f"Crafted {len(CRAFTED)} fixtures." in capsys.readouterr().out
    written = tmp_path / "scenarios/release/two-latest/latest.json"
    assert (
        json.loads(written.read_text(encoding="utf-8"))
        == CRAFTED["scenarios/release/two-latest/latest.json"]
    )
