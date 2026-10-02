"""The EVS tools' own requirements (spec/requirements.yaml), each test against its tool.

What a test expects it reads from the recordings (the `recorded` fixture). A fact it cannot
read there is named beside it, with the fixture file that holds it.
"""

import json

import pytest

from nci_si_acceptance.results import error_code, requests_naming
from nci_si_acceptance.spec import RECORDS, TOOLS, items_of

# Recorded in full: recorded/evs/concepts/C4817.json.
CONCEPT = "C4817"
CURRENT = "recorded/evs/concepts/C4817.json"
# Retired in the retired/with-replacement scenario: its concepts/C154421.json.
RETIRED = "scenarios/retired/with-replacement/concepts/C154421.json"
# Recorded with their summary sections, so a batch at the default include is answered; the
# fixture server answers a batch rotated by one (concepts.py), so request order is not its.
BATCH = ["C4817", "C12578", "C116977"]
# A code EVS does not know, which a batch leaves out (batch/silent-drop).
UNKNOWN = "CBOGUS999999"
SECTIONS = TOOLS["get_concept"]["values"]["include"]
ALWAYS = [name for name, field in RECORDS["concept"]["fields"].items() if not field.get("optional")]


def _release(tools, pinned, **arguments):
    return tools.call("resolve_release", {"terminology": pinned["terminology"], **arguments})


@pytest.mark.scenario("release/two-latest")
@pytest.mark.tool("resolve_release")
@pytest.mark.requirement("resolve_release-1")
@pytest.mark.parametrize("channel", ["monthly", "weekly"])
def test_a_channel_s_release_is_the_row_its_tag_names(tools, pinned, recorded, channel):
    # The scenario's answer to each channel's query names that channel's release; the weekly
    # row comes first wherever both are listed.
    (row,) = recorded(f"scenarios/release/two-latest/{channel}.json")["response"]["body"]

    result = _release(tools, pinned, channel=channel)

    assert not result.is_error, result.content
    assert (result.content.get("channel"), result.content.get("version")) == (
        channel,
        row["version"],
    )


@pytest.mark.scenario("release/duplicate-tag")
@pytest.mark.tool("resolve_release")
@pytest.mark.requirement("resolve_release-2")
def test_a_channel_whose_query_names_two_releases_fails_closed(tools, pinned, recorded):
    rows = recorded("scenarios/release/duplicate-tag/monthly.json")["response"]["body"]

    result = _release(tools, pinned, channel="monthly")

    assert error_code(result) == "release_not_available", result.content
    details = json.dumps(result.content["error"].get("details"))
    assert [row["version"] for row in rows if row["version"] not in details] == []


@pytest.mark.tool("resolve_release")
@pytest.mark.requirement("resolve_release-3")
def test_a_resolved_release_is_never_cached(tools, pinned):
    result = _release(tools, pinned)

    assert not result.is_error, result.content
    assert result.meta.get("ttlMs") == 0


def _concept(tools, pinned, code, **arguments):
    return tools.call("get_concept", {**pinned, "code": code, **arguments})


@pytest.mark.tool("get_concept")
@pytest.mark.requirement("get_concept-1")
@pytest.mark.parametrize("section", SECTIONS)
def test_an_include_value_returns_its_section_and_no_other(tools, pinned, section):
    result = _concept(tools, pinned, CONCEPT, include=[section])

    assert not result.is_error, result.content
    assert {name for name in SECTIONS if name in result.content} == {section}


@pytest.mark.tool("get_concept")
@pytest.mark.requirement("get_concept-2")
def test_descendants_is_no_include_value(tools, pinned):
    result = _concept(tools, pinned, CONCEPT, include=["descendants"])

    assert error_code(result) == "invalid_request", result.content


@pytest.mark.tool("get_concept")
@pytest.mark.requirement("get_concept-3")
@pytest.mark.parametrize(
    ("code", "recording"),
    [
        pytest.param(CONCEPT, CURRENT, id="current"),
        pytest.param(
            "C154421", RETIRED, id="retired", marks=pytest.mark.scenario("retired/with-replacement")
        ),
    ],
)
def test_a_concept_carries_its_identity_and_the_status_the_platform_publishes(
    tools, pinned, recorded, code, recording
):
    body = recorded(recording)["response"]["body"]

    result = _concept(tools, pinned, code)

    assert not result.is_error, result.content
    assert [name for name in ALWAYS if name not in result.content] == []
    returned = [result.content[name] for name in ("code", "terminology", "name", "active")]
    assert returned == [body[name] for name in ("code", "terminology", "name", "active")]
    # EVS publishes the status as conceptStatus; the record passes it on unchanged.
    assert result.content["status"] == body["conceptStatus"]


def _batch(tools, pinned, codes):
    return tools.call("get_concepts", {**pinned, "codes": codes})


# A server that answered the same batch before may serve it from its cache.
@pytest.mark.own_server
@pytest.mark.tool("get_concepts")
@pytest.mark.requirement("get_concepts-1")
def test_a_batch_is_one_upstream_request(tools, upstream, pinned):
    result = _batch(tools, pinned, BATCH)

    assert not result.is_error, result.content
    assert len(requests_naming(upstream.log(), BATCH)) == 1


@pytest.mark.scenario("batch/silent-drop")
@pytest.mark.tool("get_concepts")
@pytest.mark.requirement("get_concepts-2")
def test_a_code_the_platform_leaves_out_of_a_batch_is_named_missing(tools, pinned):
    result = _batch(tools, pinned, [CONCEPT, UNKNOWN])

    assert not result.is_error, result.content
    codes = [concept.get("code") for concept in items_of("get_concepts", result.content)]
    assert (codes, result.content.get("missing")) == ([CONCEPT], [UNKNOWN])


@pytest.mark.tool("get_concepts")
@pytest.mark.requirement("get_concepts-3")
@pytest.mark.parametrize("order", [BATCH, BATCH[::-1]], ids=["as-listed", "reversed"])
def test_a_batch_comes_back_in_request_order(tools, pinned, order):
    result = _batch(tools, pinned, order)

    assert not result.is_error, result.content
    assert [concept.get("code") for concept in items_of("get_concepts", result.content)] == order


@pytest.mark.tool("list_terminologies")
@pytest.mark.requirement("list_terminologies-1")
def test_every_terminology_the_platform_serves_is_listed_with_its_current_release(
    tools, pinned, recorded
):
    listing = recorded("recorded/evs/terminologies.json")["response"]["body"]

    result = tools.call("list_terminologies", {})

    assert not result.is_error, result.content
    listed = {
        item.get("terminology"): item.get("release")
        for item in items_of("list_terminologies", result.content)
    }
    assert {row["terminology"] for row in listing} - set(listed) == set()
    # NCIt's current release is the monthly one the fixture set is pinned to.
    assert listed[pinned["terminology"]] == pinned["release"]
