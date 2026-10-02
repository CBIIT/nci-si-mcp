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
# A field a record leaves out where the source says nothing: null or false is not absent.
ABSENT = "(absent)"


def _search(tools, pinned, recording, mode, **arguments):
    """The search a recording answers, by its term and page size, in `mode`."""

    params = recording["request"]["params"]
    query, (size,) = params["term"][0], params["pageSize"]
    search = {"query": query, "mode": mode, "limit": int(size)}
    return tools.call("search_concepts", {**pinned, **search, **arguments})


def _matches(result):
    """Each result's code, its matchedOn (ABSENT where it has none) and whether it carries a
    score."""

    assert not result.is_error, result.content
    return [
        (
            (entry.get("concept") or {}).get("code"),
            entry.get("matchedOn", ABSENT),
            "score" in entry,
        )
        for entry in result.content.get("results", [])
    ]


def _recorded_matches(recording):
    """What EVS answered, as the search result record passes it on: no score from EVS."""

    concepts = recording["response"]["body"].get("concepts", [])
    return [(concept["code"], concept.get("highlight", ABSENT), False) for concept in concepts]


@pytest.mark.tool("search_concepts")
@pytest.mark.requirement("search_concepts-1")
def test_lexical_search_returns_the_platform_s_matches_in_its_order_a_page_at_a_time(
    tools, pinned, recorded
):
    first, second = (recorded(file) for file in LEXICAL)

    page = _search(tools, pinned, first, "lexical")
    assert _matches(page) == _recorded_matches(first)
    assert page.content.get("totalKnown") == first["response"]["body"]["total"]
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
    """Each member's code, name and inactive mark (ABSENT where it has none), and the total."""

    assert not result.is_error, result.content
    members = items_of("expand_value_set", result.content)
    found = [
        (member.get("code"), member.get("name"), member.get("inactive", ABSENT))
        for member in members
    ]
    return found, result.content.get("total")


def _recorded_members(contains):
    return [
        (member["code"], member["display"], member.get("inactive", ABSENT)) for member in contains
    ]


@pytest.mark.tool("expand_value_set")
@pytest.mark.requirement("expand_value_set-1")
@pytest.mark.parametrize("page", ["first", "middle", "last"])
def test_count_and_offset_select_the_members_and_total_counts_them_all(
    tools, pinned, recorded, page
):
    expansion = recorded(EXPANSION)["response"]["body"]["expansion"]
    contains = expansion["contains"]
    # The last page holds fewer members than COUNT.
    offset = {"first": 0, "middle": 100, "last": len(contains) - 4}[page]

    result = _expand(tools, pinned, count=COUNT, offset=offset)

    assert _members(result) == (
        _recorded_members(contains[offset : offset + COUNT]),
        expansion["total"],
    )


INACTIVE = "scenarios/valueset/inactive-members/expand.json"


@pytest.mark.scenario("valueset/inactive-members")
@pytest.mark.tool("expand_value_set")
@pytest.mark.requirement("expand_value_set-2")
@pytest.mark.parametrize(
    "active_only",
    [{"activeOnly": True}, {"activeOnly": False}, {}],
    ids=["true", "false", "default"],
)
def test_active_only_leaves_out_the_members_marked_inactive(tools, pinned, recorded, active_only):
    contains = recorded(INACTIVE)["response"]["body"]["expansion"]["contains"]
    inactive = [member for member in contains if member.get("inactive")]
    # The scenario marks members on the first page, or the test would show nothing.
    assert inactive and all(member in contains[:COUNT] for member in inactive)
    left_out = inactive if active_only.get("activeOnly") else []
    kept = [member for member in contains if member not in left_out]

    result = _expand(tools, pinned, count=COUNT, offset=0, **active_only)

    assert _members(result) == (_recorded_members(kept[:COUNT]), len(kept))


# Traversal. Each tool's defaults and maxima are its bounds in spec/tools.yaml; polarity's
# exclusion sets are the traversal record's.
HIERARCHY, NEIGHBORHOOD = "get_concept_hierarchy", "get_concept_neighborhood"
EXCLUSIONS = RECORDS["traversal"]["fields"]["polarity"]["exclusions"]
# traversal/deep-fanout: a root with 1,001 children, the first heading a chain deeper than the
# depth maximum.
FANOUT = "scenarios/traversal/deep-fanout/concepts"
FANOUT_ROOT, CHAIN_HEAD = "C99000000", "C99000001"
# traversal/starvation: two hubs over the same targets, one with 300 roles and 2 associations,
# the other the other way round.
HUBS = [
    f"scenarios/traversal/starvation/concepts/{code}.json" for code in ("C99200000", "C99200400")
]
# traversal/exclusions: C4817 with a role of every exclusion code, those named as positive ones,
# and two positive roles named as exclusions, in its recording and the catalogue alike.
EXCLUDED = "scenarios/traversal/exclusions/concepts/C4817.json"
# The paths from C4817 to the root, as EVS gives them.
PATHS = "recorded/evs/paths-to-root.json"
# Two steps over roles: far enough to show whether a negative edge's target is followed.
TWO_STEPS = 2


def _maximum(tool, argument):
    return TOOLS[tool]["bounds"][argument]["maximum"]


def _traverse(tools, pinned, tool, code, **arguments):
    result = tools.call(tool, {**pinned, "code": code, **arguments})
    assert not result.is_error, result.content
    return result


def _depth(item):
    return (item.get("provenance") or {}).get("depth")


def _relationship(edge):
    return (edge.get("provenance") or {}).get("relationship") or {}


def _polarity(edge):
    return (edge.get("provenance") or {}).get("polarity")


def _codes_of(items):
    return [item.get("code") for item in items]


def _unmarked(nodes):
    """The nodes without the status the node record requires; EVS publishes one for every
    concept, as conceptStatus."""

    return [
        node.get("code")
        for node in nodes
        if type(node.get("active")) is not bool or not node.get("status")
    ]


@pytest.mark.scenario("traversal/deep-fanout")
@pytest.mark.tool(HIERARCHY)
@pytest.mark.requirement("get_concept_hierarchy-1")
def test_a_depth_above_the_maximum_is_applied_as_the_maximum_and_reported(tools, pinned):
    maximum = _maximum(HIERARCHY, "depth")

    result = _traverse(tools, pinned, HIERARCHY, CHAIN_HEAD, direction="child", depth=maximum + 2)

    # The chain runs deeper than the maximum, so the walk reaches it and no further.
    nodes = result.content.get("nodes", [])
    depths = {_depth(node) for node in nodes}
    assert maximum in depths
    assert [depth for depth in depths if not isinstance(depth, int) or depth > maximum] == []
    truncation = result.content.get("truncation") or {}
    assert (truncation.get("bound"), truncation.get("limit")) == ("depth", maximum)
    assert _unmarked(nodes) == []


@pytest.mark.tool(HIERARCHY)
@pytest.mark.requirement("get_concept_hierarchy-2")
def test_paths_to_root_are_the_platform_s_paths_in_its_order(tools, pinned, recorded):
    paths = [_codes_of(path) for path in recorded(PATHS)["response"]["body"]]

    result = _traverse(tools, pinned, HIERARCHY, CONCEPT, direction="pathsToRoot")

    assert result.content.get("paths") == paths
    # Each concept on the paths once among the nodes, the one asked about not among them.
    reached = {code for path in paths for code in path} - {CONCEPT}
    nodes = result.content.get("nodes", [])
    assert sorted(_codes_of(nodes)) == sorted(reached)
    assert _unmarked(nodes) == []


@pytest.mark.tool(HIERARCHY)
@pytest.mark.requirement("get_concept_hierarchy-3")
def test_limit_is_a_page_the_cursor_continues_to_the_end(tools, pinned, recorded):
    children = _codes_of(recorded(CURRENT)["response"]["body"]["children"])
    limit = len(children) // 2 + 1

    first = _traverse(tools, pinned, HIERARCHY, CONCEPT, direction="child", limit=limit)
    cursor = first.content.get("nextCursor")
    second = _traverse(
        tools, pinned, HIERARCHY, CONCEPT, direction="child", limit=limit, cursor=cursor
    )

    pages = [_codes_of(page.content.get("nodes", [])) for page in (first, second)]
    assert pages == [children[:limit], children[limit:]]
    assert "nextCursor" not in second.content


# The kinds a starvation hub has, by the relation list each comes from.
LISTS = {"role": "roles", "association": "associations"}


@pytest.mark.scenario("traversal/starvation")
@pytest.mark.tool(NEIGHBORHOOD)
@pytest.mark.requirement("get_concept_neighborhood-1")
@pytest.mark.parametrize("hub", HUBS, ids=["roles", "associations"])
@pytest.mark.parametrize("kinds", [list(LISTS), list(LISTS)[::-1]], ids=["in-order", "reversed"])
def test_a_kind_that_reaches_its_budget_starves_no_other(tools, pinned, recorded, hub, kinds):
    body = recorded(hub)["response"]["body"]
    sizes = {kind: len(body[key]) for kind, key in LISTS.items()}
    # Fewer nodes than the large kind has, room enough for the small one.
    budget = max(sizes.values()) // 2
    assert min(sizes.values()) < budget // len(LISTS)
    large = max(sizes, key=sizes.__getitem__)

    result = _traverse(
        tools, pinned, NEIGHBORHOOD, body["code"], depth=1, kinds=kinds, maxNodes=budget
    )

    assert len(result.content.get("nodes", [])) <= budget
    kinds_found = {_relationship(edge).get("kind") for edge in result.content.get("edges", [])}
    assert kinds_found == set(LISTS)
    truncation = result.content.get("truncation") or {}
    assert truncation.get("occurred") is True
    per_kind = truncation.get("perKind") or {}
    assert {kind for kind, record in per_kind.items() if record.get("occurred")} == {large}


def _negative(code, terminology="ncit"):
    return code in EXCLUSIONS[terminology]


def _outward(result):
    """The edges from the concept asked about, C4817."""

    return [edge for edge in result.content.get("edges", []) if edge.get("sourceCode") == CONCEPT]


@pytest.mark.scenario("traversal/exclusions")
@pytest.mark.tool(NEIGHBORHOOD)
@pytest.mark.requirement("get_concept_neighborhood-2")
def test_polarity_follows_the_relationship_code_not_its_name(tools, pinned, recorded):
    roles = recorded(EXCLUDED)["response"]["body"]["roles"]
    expected = {
        (role["code"], role["relatedCode"]): "negative" if _negative(role["code"]) else "positive"
        for role in roles
    }
    # The scenario holds a role of every code in the set, so that leaving one out shows.
    assert set(EXCLUSIONS["ncit"]) <= {code for code, _ in expected}

    result = _traverse(tools, pinned, NEIGHBORHOOD, CONCEPT, depth=1, kinds=["role"])

    found = {
        (_relationship(edge).get("code"), edge.get("targetCode")): _polarity(edge)
        for edge in _outward(result)
    }
    assert found == expected


def _role_targets(recorded, codes):
    """The concepts the roles of the recorded concepts `codes` name."""

    return {
        role["relatedCode"]
        for code in codes
        for role in recorded(f"recorded/evs/concepts/{code}.json")["response"]["body"].get(
            "roles", []
        )
    }


def _two_steps(recorded):
    """C4817's negative role targets; the concepts only those reach at the second step; and
    those the positive ones reach there."""

    roles = recorded(CURRENT)["response"]["body"]["roles"]
    negative = {role["relatedCode"] for role in roles if _negative(role["code"])}
    positive = {role["relatedCode"] for role in roles if not _negative(role["code"])}
    past_positive = _role_targets(recorded, positive - negative)
    beyond = _role_targets(recorded, negative - positive) - negative - positive - past_positive
    return negative, beyond - {CONCEPT}, past_positive - {CONCEPT}


@pytest.mark.tool(NEIGHBORHOOD)
@pytest.mark.requirement("get_concept_neighborhood-3")
@pytest.mark.parametrize("include", [{}, {"includeNegative": True}], ids=["default", "included"])
def test_negative_edges_are_returned_marked_and_followed_only_when_included(
    tools, pinned, recorded, include
):
    negative, beyond, past_positive = _two_steps(recorded)
    assert beyond

    result = _traverse(
        tools,
        pinned,
        NEIGHBORHOOD,
        CONCEPT,
        depth=TWO_STEPS,
        kinds=["role"],
        maxNodes=_maximum(NEIGHBORHOOD, "maxNodes"),
        maxEdges=_maximum(NEIGHBORHOOD, "maxEdges"),
        **include,
    )

    marked = {edge.get("targetCode") for edge in _outward(result) if _polarity(edge) == "negative"}
    assert marked == negative
    reached = set(_codes_of(result.content.get("nodes", [])))
    assert past_positive - reached == set()
    assert reached & beyond == (beyond if include else set())


@pytest.mark.scenario("traversal/deep-fanout")
@pytest.mark.tool(NEIGHBORHOOD)
@pytest.mark.requirement("get_concept_neighborhood-4")
def test_a_node_limit_above_the_maximum_is_applied_as_the_maximum(tools, pinned, recorded):
    maximum = _maximum(NEIGHBORHOOD, "maxNodes")
    children = recorded(f"{FANOUT}/{FANOUT_ROOT}.json")["response"]["body"]["children"]
    assert len(children) + 1 > maximum

    result = _traverse(
        tools, pinned, NEIGHBORHOOD, FANOUT_ROOT, depth=1, kinds=["child"], maxNodes=maximum * 2
    )

    nodes = result.content.get("nodes", [])
    assert len(nodes) <= maximum
    truncation = result.content.get("truncation") or {}
    assert (truncation.get("bound"), truncation.get("limit")) == ("nodes", maximum)
    assert truncation.get("omitted", 0) >= 1
    assert _unmarked(nodes) == []
