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
# NCIt's semantic-type property, by its code (the concept record's semanticType).
SEMANTIC_TYPE = "P106"
ALWAYS = [name for name, field in RECORDS["concept"]["fields"].items() if not field.get("optional")]
# The concept record's fields that are not include sections: status and those always present.
BASE = [name for name in RECORDS["concept"]["fields"] if name not in SECTIONS]


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


def _recorded_section(body, section):
    """A section as the concept record defines it, from EVS's recording of the concept."""

    if section == "semanticType":
        return [entry["value"] for entry in body["properties"] if entry["code"] == SEMANTIC_TYPE]
    return body[section]


@pytest.mark.tool("get_concept")
@pytest.mark.requirement("get_concept-1")
@pytest.mark.parametrize("section", SECTIONS)
def test_an_include_value_returns_its_section_and_no_other(tools, pinned, recorded, section):
    body = recorded(CURRENT)["response"]["body"]

    result = _concept(tools, pinned, CONCEPT, include=[section])

    assert not result.is_error, result.content
    # Any other key is another section, whether the record names it or not (parents, roles).
    assert set(result.content) - set(BASE) == {section}
    assert result.content[section] == _recorded_section(body, section)


@pytest.mark.tool("get_concept")
@pytest.mark.requirement("get_concept-2")
def test_descendants_is_no_include_value(tools, pinned):
    # A server whose input schema lists the include values refuses this in the SDK's argument
    # validation, as text with no error record; that fails here, since M3.2 asks for the record.
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
    # EVS publishes a status for every concept, as conceptStatus; the record passes it on.
    assert result.content.get("status") == body["conceptStatus"]


def _batch(tools, pinned, codes):
    return tools.call("get_concepts", {**pinned, "codes": codes})


# A server that answered the same batch before may serve it from its cache.
@pytest.mark.own_server
@pytest.mark.tool("get_concepts")
@pytest.mark.requirement("get_concepts-1")
def test_a_batch_is_one_upstream_request(tools, upstream, pinned):
    result = _batch(tools, pinned, BATCH)

    assert not result.is_error, result.content
    requests = requests_naming(upstream.log(), BATCH)
    assert len(requests) == 1, requests
    # One request for one code, the others from elsewhere, is no batch.
    assert [code for code in BATCH if not requests_naming(requests, [code])] == []


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


# Lexical search is EVS's type=contains and typeahead its type=startsWith, each recorded a page
# of 10 at a time with highlights asked for (fixtures/manifest.yaml); EVS returns highlights for
# contains only. The lexical search is recorded for its first two pages.
LEXICAL = ["recorded/evs/search-contains.json", "recorded/evs/search-contains-page-2.json"]
TYPEAHEAD = "recorded/evs/search-starts-with.json"


def _search(tools, pinned, recording, mode, **arguments):
    """The search a recording answers, by its term and page size, in `mode`."""

    params = recording["request"]["params"]
    query, (size,) = params["term"][0], params["pageSize"]
    search = {"query": query, "mode": mode, "limit": int(size)}
    return tools.call("search_concepts", {**pinned, **search, **arguments})


def _matches(result):
    """Each result's code, its matchedOn and whether it carries a score."""

    assert not result.is_error, result.content
    return [
        ((entry.get("concept") or {}).get("code"), entry.get("matchedOn"), "score" in entry)
        for entry in result.content.get("results", [])
    ]


def _recorded_matches(recording):
    """What EVS answered, as the search result record passes it on: no score from EVS."""

    concepts = recording["response"]["body"].get("concepts", [])
    return [(concept["code"], concept.get("highlight"), False) for concept in concepts]


@pytest.mark.tool("search_concepts")
@pytest.mark.requirement("search_concepts-1")
def test_lexical_search_returns_the_platform_s_matches_in_its_order_a_page_at_a_time(
    tools, pinned, recorded
):
    first, second = (recorded(file) for file in LEXICAL)

    page = _search(tools, pinned, first, "lexical")
    assert _matches(page) == _recorded_matches(first)
    following = _search(tools, pinned, first, "lexical", cursor=page.content.get("nextCursor"))

    assert _matches(following) == _recorded_matches(second)


@pytest.mark.tool("search_concepts")
@pytest.mark.requirement("search_concepts-2")
def test_typeahead_returns_the_platform_s_prefix_matches_in_its_order(tools, pinned, recorded):
    recording = recorded(TYPEAHEAD)

    result = _search(tools, pinned, recording, "typeahead")

    assert _matches(result) == _recorded_matches(recording)


# Value set C85492 (CDISC SDTM Method Terminology), recorded whole: FHIR $expand ignores count,
# offset and activeOnly (fixtures/manifest.yaml).
VALUE_SET = "C85492"
EXPANSION = "recorded/evs-fhir/expand-c85492.json"
COUNT = 10


def _expand(tools, pinned, **arguments):
    return tools.call("expand_value_set", {**pinned, "valueSet": VALUE_SET, **arguments})


def _members(result):
    """Each member's code and name, and the total."""

    assert not result.is_error, result.content
    members = items_of("expand_value_set", result.content)
    pairs = [(member.get("code"), member.get("name")) for member in members]
    return pairs, result.content.get("total")


def _recorded_members(contains):
    return [(member["code"], member["display"]) for member in contains]


@pytest.mark.tool("expand_value_set")
@pytest.mark.requirement("expand_value_set-1")
# An offset below zero counts from the end: the last page, which holds fewer than COUNT.
@pytest.mark.parametrize("offset", [0, 100, -4], ids=["first", "middle", "last"])
def test_count_and_offset_select_the_members_and_total_counts_them_all(
    tools, pinned, recorded, offset
):
    expansion = recorded(EXPANSION)["response"]["body"]["expansion"]
    contains = expansion["contains"]
    offset %= len(contains)

    result = _expand(tools, pinned, count=COUNT, offset=offset)

    assert _members(result) == (
        _recorded_members(contains[offset : offset + COUNT]),
        expansion["total"],
    )


INACTIVE = "scenarios/valueset/inactive-members/expand.json"


def _marked_inactive(members):
    """The codes of the members marked inactive, as FHIR and the member record mark them."""

    return {member.get("code") for member in members if member.get("inactive") is True}


@pytest.mark.scenario("valueset/inactive-members")
@pytest.mark.tool("expand_value_set")
@pytest.mark.requirement("expand_value_set-2")
@pytest.mark.parametrize("active_only", [True, False], ids=["active-only", "all"])
def test_active_only_leaves_out_the_members_marked_inactive(tools, pinned, recorded, active_only):
    contains = recorded(INACTIVE)["response"]["body"]["expansion"]["contains"]
    inactive = _marked_inactive(contains)
    kept = [member for member in contains if not active_only or member["code"] not in inactive]

    result = _expand(tools, pinned, count=COUNT, offset=0, activeOnly=active_only)

    assert _members(result) == (_recorded_members(kept[:COUNT]), len(kept))
    returned = _marked_inactive(items_of("expand_value_set", result.content))
    assert returned == (set() if active_only else inactive)
