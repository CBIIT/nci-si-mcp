"""Each crafted scenario provokes the behaviour it is named for (Acceptance Suite §2.3)."""

import json
import shutil
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from nci_si_acceptance.craft import EXCLUSION_ROLES, LICENCE_KEY, MISMATCHED, craft, main
from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures
from nci_si_acceptance.record import FIXTURES

CRAFTED = craft(FIXTURES)
# The hard maximums of a traversal (docs/SPEC.md §4.3).
MAX_NODES, MAX_DEPTH = 1000, 4


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


def test_mismatch_serves_every_payload_from_another_release():
    def versions(payload):
        if isinstance(payload, list):
            return set().union(*map(versions, payload))
        if isinstance(payload, dict):
            own = {payload["version"]} if "version" in payload else set()
            return own.union(*map(versions, payload.values()))
        return set()

    served = set().union(
        *(versions(doc["response"]["body"]) for doc in scenario("release/mismatch").values())
    )

    assert "26.09d" not in served
    assert "26.08e" in served
    assert len(scenario("release/mismatch")) == len(MISMATCHED)


def test_two_latest_answers_two_latest_rows_one_per_channel():
    rows = body("scenarios/release/two-latest/latest.json")
    listing = body("scenarios/release/two-latest/terminologies.json")

    assert [row["latest"] for row in rows] == [True, True]
    assert [set(row["tags"]) for row in rows] == [{"monthly"}, {"weekly"}]
    assert len([row for row in listing if row["terminology"] == "ncit" and row["latest"]]) > 1


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
        close, failure, slow = doc["responses"]
        assert (close, failure["status"]) == ({"fault": "close"}, 503)
        assert slow["delay_seconds"] > int(settings["NCI_SI_TIMEOUT_SECONDS"])


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
