"""The caDSR tools' own requirements (spec/requirements.yaml), and the cross-cutting ones only a
caDSR answer shows, each test against its tool.

What a test expects it reads from the recordings (the `recorded` fixture). A fact it cannot
read there is named beside it, with the fixture file that holds it.
"""

import json
from http import HTTPStatus

import pytest

from nci_si_acceptance.results import (
    EXPORT,
    EXPORT_LISTING,
    error_code,
    export_date,
    requests_naming,
)
from nci_si_acceptance.spec import RECORDS, TOOLS

# Recorded both ways: recorded/cadsr/data-element-2200604.json answers a request that names
# Accept: application/json, data-element-2200604-html.json, with HTML, any other.
DATA_ELEMENT = "2200604"
# Unknown to caDSR, which answers HTTP 200 with DataElement null (data-element-unknown.json).
UNKNOWN = "99999999"
# No number, which caDSR refuses with HTTP 200 and apiResponse type E (data-element-refused.json).
REFUSED = "notanumber"


def _accepts(log):
    """The Accept header of each caDSR request in `log`."""

    return [
        {name.lower(): value for name, value in entry["headers"].items()}.get("accept")
        for entry in log
        if entry["surface"] == "cadsr"
    ]


# A server that answered the same call before may serve it from its cache, asking nothing.
@pytest.mark.own_server
@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-15")
def test_a_server_that_leaves_out_accept_gets_html_and_never_parses_it(tools, upstream):
    before = len(upstream.log())

    result = tools.call("get_data_element", {"publicId": DATA_ELEMENT})

    accepts = _accepts(upstream.log()[before:])
    assert accepts
    if result.is_error:
        assert error_code(result) == "upstream_unavailable", result.content
    else:
        # The content came from JSON: every request asked for it, as the contracts prescribe.
        assert set(accepts) == {"application/json"}
        assert result.content.get("publicId") == DATA_ELEMENT


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-15")
def test_a_failure_inside_an_http_200_is_an_error_never_an_empty_success(tools, recorded):
    answer = recorded("recorded/cadsr/data-element-unknown.json")["response"]
    # caDSR answers the unknown id with HTTP 200, no data element and a note that says so.
    assert (answer["status"], answer["body"]["DataElement"]) == (200, None)

    result = tools.call("get_data_element", {"publicId": UNKNOWN})

    assert error_code(result) == "not_found", result.content


@pytest.mark.scenario("cadsr/html-for-json")
@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-15")
def test_html_where_json_was_asked_for_is_an_upstream_error(tools, recorded):
    answer = recorded("scenarios/cadsr/html-for-json/data-element-2200604.json")["response"]
    # The scenario answers the request for JSON with HTML and HTTP 200.
    assert answer["status"] == HTTPStatus.OK
    assert answer["body"].startswith("<BODY")

    result = tools.call("get_data_element", {"publicId": DATA_ELEMENT})

    assert error_code(result) == "upstream_unavailable", result.content


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-15")
def test_a_refusal_inside_an_http_200_is_an_invalid_request(tools, recorded):
    answer = recorded("recorded/cadsr/data-element-refused.json")["response"]
    assert (answer["status"], answer["body"]["apiResponse"]["type"]) == (200, "E")

    result = tools.call("get_data_element", {"publicId": REFUSED})

    assert error_code(result) == "invalid_request", result.content


# The registry's state. The export folder dates releasedCDEsXML-OD.zip; /registry/releases
# answers 404 (registry-releases.json).
ELEMENT = "recorded/cadsr/data-element-2200604.json"
VERSION_1 = "recorded/cadsr/data-element-2200604-version-1.json"
# What a data element record holds without include, and the sections include adds.
SECTIONS = TOOLS["get_data_element"]["values"]["include"]
OWN = [name for name in RECORDS["data_element"]["fields"] if name not in SECTIONS]


def _element(recorded, fixture=ELEMENT):
    return recorded(fixture)["response"]["body"]["DataElement"]


def _ok(result):
    assert not result.is_error, result.content
    return result.content


# A server that answered the same call before may serve it from its cache, asking nothing.
@pytest.mark.own_server
@pytest.mark.tool("resolve_registry_release")
@pytest.mark.requirement("resolve_registry_release-1")
def test_without_a_registry_release_the_export_date_stands_for_it(tools, upstream, recorded):
    dated = export_date(recorded(EXPORT_LISTING)["response"]["body"])
    assert (
        recorded("recorded/cadsr/registry-releases.json")["response"]["status"]
        == HTTPStatus.NOT_FOUND
    )

    result = tools.call("resolve_registry_release", {})

    content = _ok(result)
    assert (content.get("published"), "identifier" in content) == (False, False)
    assert str(content.get("generatedAt", "")).startswith(dated)
    assert EXPORT in str(content.get("sourceDistribution"))
    assert result.meta.get("ttlMs") == 0
    asked = [(entry["surface"], entry["path"], str(entry["params"])) for entry in upstream.log()]
    assert len(asked) == len(set(asked))


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("get_data_element-1")
@pytest.mark.parametrize("version", [None, "1"], ids=["latest", "version-1"])
def test_a_data_element_is_its_own_fields_alone_with_its_version_and_statuses(
    tools, recorded, version
):
    element = _element(recorded, VERSION_1 if version else ELEMENT)
    pinned = {"version": version} if version else {}

    content = _ok(tools.call("get_data_element", {"publicId": DATA_ELEMENT, **pinned}))

    assert set(content) - set(OWN) == set()
    assert _own(content) == _own(element)


def _own(element):
    """A data element's own fields, provenance aside, as the record names them."""

    return {key: element.get(key) for key in OWN if key != "provenance"}


def _concepts(element):
    """The data element concept's concepts, by code, name and role."""

    concept = element["DataElementConcept"]
    return {
        (each["conceptCode"], each["longName"], role)
        for role, part in (("objectClass", "ObjectClass"), ("property", "Property"))
        for each in concept[part]["Concepts"]
    }


def _section(element, include):
    """What a section holds, read from the recording, in the form the test compares."""

    domain = element["ValueDomain"]
    sections = {
        "permissibleValues": lambda: [
            (value["publicId"], value["value"], _meaning(value["ValueMeaning"]))
            for value in domain["PermissibleValues"]
        ],
        "valueDomain": lambda: {k: v for k, v in domain.items() if k != "PermissibleValues"},
        "conceptAssociations": lambda: _concepts(element),
        "alternateNames": lambda: element["AlternateNames"],
        "classificationSchemes": lambda: [
            (
                _fields(scheme, SCHEME),
                [_fields(i, ITEM) for i in scheme["ClassificationSchemeItems"]],
            )
            for scheme in element["ClassificationSchemes"]
        ],
    }
    return sections[include]()


# The fields of a classification scheme and of its items the record names.
SCHEME, ITEM = ("publicId", "version", "longName", "context"), ("publicId", "version", "longName")


def _fields(entry, names):
    return {name: entry.get(name) for name in names}


def _meaning(meaning):
    """A value meaning by its identity and name, with its concepts by code and name."""

    concepts = [
        (concept.get("conceptCode"), concept.get("longName"))
        for concept in meaning.get("Concepts", meaning.get("concepts")) or []
    ]
    return _fields(meaning, ("publicId", "version", "longName")), concepts


def _returned(content, include):
    """What a section of a result holds, in the form the test compares."""

    found = content.get(include)
    forms = {
        "permissibleValues": lambda: [
            (value.get("publicId"), value.get("value"), _meaning(value.get("valueMeaning") or {}))
            for value in found
        ],
        "conceptAssociations": lambda: {
            (c.get("conceptCode"), c.get("longName"), c.get("role")) for c in found
        },
        "classificationSchemes": lambda: [
            (_fields(scheme, SCHEME), [_fields(i, ITEM) for i in scheme.get("items", [])])
            for scheme in found
        ],
    }
    return forms.get(include, lambda: found)() if isinstance(found, (list, dict)) else found


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("get_data_element-1")
@pytest.mark.parametrize("include", SECTIONS)
def test_each_include_returns_its_section_as_the_platform_gives_it(tools, recorded, include):
    expected = _section(_element(recorded), include)
    assert expected

    content = _ok(tools.call("get_data_element", {"publicId": DATA_ELEMENT, "include": [include]}))

    assert _returned(content, include) == expected
    assert [name for name in SECTIONS if name != include and name in content] == []
    # A permissible value and a scheme are items of their own, each with its provenance.
    nested = content[include] if include in ("permissibleValues", "classificationSchemes") else []
    assert [(entry.get("provenance") or {}).get("release") for entry in nested] == [
        {"registry": "cadsr"}
    ] * len(nested)


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("get_data_element-2")
def test_a_question_text_one_data_element_has_finds_it(tools, recorded):
    found = recorded("recorded/cadsr/question-text-sex-of-a-person.json")["response"]["body"]
    (element,) = found["DataElements"]

    content = _ok(tools.call("get_data_element", {"questionText": "Sex of a Person"}))

    # The full data element, not the search's header record.
    assert set(content) - set(OWN) == set()
    assert _own(content) == _own(_element(recorded))
    assert content.get("publicId") == element["publicId"]


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("get_data_element-2")
def test_a_question_text_several_have_is_an_invalid_request_naming_them(tools, recorded):
    found = recorded("recorded/cadsr/question-text-date-of-birth.json")["response"]["body"]
    candidates = [element["publicId"] for element in found["DataElements"]]
    assert len(candidates) > 1

    result = tools.call("get_data_element", {"questionText": "Date of birth"})

    assert error_code(result) == "invalid_request", result.content
    said = json.dumps(result.content["error"])
    assert [
        candidate
        for candidate in candidates
        if f'"{candidate}"' not in said and f" {candidate}" not in said
    ] == []


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("get_data_element-2")
def test_a_long_name_lookup_is_unavailable_never_empty(tools, recorded):
    result = tools.call("get_data_element", {"longName": _element(recorded)["longName"]})

    assert error_code(result) == "capability_unavailable", result.content


FORM = "recorded/cadsr/form-5406471.json"


@pytest.mark.tool("get_form")
@pytest.mark.requirement("get_form-1")
def test_a_form_returns_its_modules_and_its_status_unchanged_a_retired_one_too(tools, recorded):
    form = recorded(FORM)["response"]["body"]["form"]
    assert form["workflowStatus"] == "RETIRED ARCHIVED"

    content = _ok(tools.call("get_form", {"publicId": form["publicID"]}))

    named = [name for name in RECORDS["form"]["fields"] if name not in ("provenance", "modules")]
    expected = {name: form.get(name) for name in named} | {"publicId": form["publicID"]}
    assert {name: content.get(name) for name in named} == expected
    assert content.get("modules") == form["modules"]


@pytest.mark.tool("get_form")
@pytest.mark.requirement("get_form-1")
def test_a_form_without_its_modules_has_none(tools, recorded):
    form = recorded(FORM)["response"]["body"]["form"]

    content = _ok(tools.call("get_form", {"publicId": form["publicID"], "includeModules": False}))

    assert content.get("publicId") == form["publicID"]
    assert "modules" not in content


@pytest.mark.tool("get_form")
@pytest.mark.requirement("get_form-1")
def test_a_form_keyword_is_an_invalid_request_saying_an_identifier_is_needed(tools):
    result = tools.call("get_form", {"keyword": "Patient Safety Event Report"})

    assert error_code(result) == "invalid_request", result.content
    assert "identifier" in result.content["error"]["message"].lower()


@pytest.mark.tool("get_form")
@pytest.mark.requirement("X-15")
def test_an_unknown_form_answered_inside_an_http_200_is_not_found(tools, recorded):
    answer = recorded("recorded/cadsr/form-unknown.json")["response"]
    # The Form API says type E for an id it does not know, where the data element API says I.
    assert (answer["status"], answer["body"]["form"], answer["body"]["apiResponse"]["type"]) == (
        200,
        None,
        "E",
    )

    result = tools.call("get_form", {"publicId": "99999999"})

    assert error_code(result) == "not_found", result.content


VM_MATCH = "recorded/cadsr/vm-match-male.json"


# The fields of a matched item the record names, with vmMatch's name for each.
MATCHED = {
    "itemType": "itemType",
    "publicId": "itemId",
    "version": "version",
    "name": "matchedName",
    "concept": "concept",
    "evsSource": "evsSource",
    "context": "context",
    "workflowStatus": "workflowStatus",
    "registrationStatus": "registrationStatus",
}


def _matched(match):
    """A vmMatch match as the record names it, a null field absent."""

    return {key: match[name] for key, name in MATCHED.items() if match[name] is not None}


@pytest.mark.tool("match_value_meanings")
@pytest.mark.requirement("match_value_meanings-1")
def test_value_meaning_matches_are_the_platform_s_in_its_order_with_their_rule(tools, recorded):
    (answer,) = recorded(VM_MATCH)["response"]["body"]["matchResults"]
    matches = answer["matches"]
    # Every recorded crosswalk is NA: none of the matches has one.
    assert {match["crosswalkCode"] for match in matches} == {"NA"}

    content = _ok(tools.call("match_value_meanings", {"values": [answer["name"]]}))

    found = content.get("matches", [])
    items = [match.get("item") or {} for match in found]
    assert [{key: item[key] for key in MATCHED if key in item} for item in items] == [
        _matched(match) for match in matches
    ]
    # What vmMatch says of each item's origin, passed through (A4.3).
    assert [(item.get("provenance") or {}).get("upstream") for item in items] == [
        {"itemId": match["itemId"], "version": match["version"]} for match in matches
    ]
    assert [match.get("rule") for match in found] == [match["ruleDescription"] for match in matches]
    # vmMatch scores nothing, and NA is no crosswalk: absent, never null.
    assert [key for match in found for key in ("score", "crosswalk") if key in match] == []


@pytest.mark.tool("match_value_meanings")
@pytest.mark.requirement("match_value_meanings-1")
def test_more_values_than_the_tool_takes_is_an_invalid_request(tools):
    most = TOOLS["match_value_meanings"]["lists"]["values"]

    result = tools.call("match_value_meanings", {"values": ["Male"] * (most + 1)})

    assert error_code(result) == "invalid_request", result.content


CRDC = "recorded/cadsr/crdc-list.json"


def _crdc(recorded):
    return recorded(CRDC)["response"]["body"]["CRDCDataElements"]


def _used_by(entry):
    return [name.strip() for name in (entry["Used By"] or "").split(",") if name.strip()]


@pytest.mark.tool("get_code_map")
@pytest.mark.requirement("get_code_map-1")
def test_a_code_map_is_a_data_element_s_values_users_and_coverage(tools, recorded):
    # A data element with an uncoded value and a colon-joined code, so that both show.
    entry = next(entry for entry in _crdc(recorded) if _uncoded_and_joined(entry))
    values = [_value(value) for value in entry["permissibleValues"]]

    content = _ok(tools.call("get_code_map", {"dataElementId": entry["CDE Public ID"]}))

    (found,) = content.get("codeMaps", [])
    assert found.get("dataElement") == {
        "publicId": entry["CDE Public ID"],
        "version": entry["Version"],
    }
    assert (found.get("crdcName"), found.get("usedBy")) == (entry["CRDC Name"], _used_by(entry))
    assert found.get("valueLevelBinding") is True
    assert found.get("coverage") == sum(1 for value in values if "conceptCode" in value)
    assert found.get("values") == values
    upstream = (found.get("provenance") or {}).get("upstream")
    assert upstream == {"CDE Public ID": entry["CDE Public ID"], "Version": entry["Version"]}


def _codes(entry):
    return [value.get("Concept Code") for value in entry.get("permissibleValues") or []]


def _uncoded_and_joined(entry):
    codes = _codes(entry)
    return None in codes and any(":" in (code or "") for code in codes)


def _value(value):
    """A value as the record names it, its code absent where the platform gives none."""

    code = value["Concept Code"]
    return {"value": value["Permissible Value"]} | ({"conceptCode": code} if code else {})


@pytest.mark.tool("get_code_map")
@pytest.mark.requirement("get_code_map-1")
def test_a_data_element_without_value_level_binding_says_so(tools, recorded):
    entry = next(entry for entry in _crdc(recorded) if "permissibleValues" not in entry)

    content = _ok(tools.call("get_code_map", {"dataElementId": entry["CDE Public ID"]}))

    (found,) = content.get("codeMaps", [])
    assert (found.get("valueLevelBinding"), found.get("values")) == (False, [])


@pytest.mark.tool("get_code_map")
@pytest.mark.requirement("get_code_map-1")
# CIP: a name that is part of another (NCIP), which a match on part of the text would confuse.
@pytest.mark.parametrize("context", ["GDC", "CIP"])
def test_a_context_selects_the_code_maps_it_uses(tools, recorded, context):
    users = [entry["CDE Public ID"] for entry in _crdc(recorded) if context in _used_by(entry)]
    # Within one page of the limit's default, so that the whole selection shows.
    assert 1 < len(users) <= TOOLS["get_code_map"]["bounds"]["limit"]["default"]

    content = _ok(tools.call("get_code_map", {"targetContext": context}))

    found = [(m.get("dataElement") or {}).get("publicId") for m in content.get("codeMaps", [])]
    assert sorted(found) == sorted(users)


@pytest.mark.tool("get_code_map")
@pytest.mark.requirement("get_code_map-1")
def test_a_source_system_other_than_crdc_is_an_invalid_request(tools):
    assert TOOLS["get_code_map"]["values"]["sourceSystem"] == ["CRDC"]

    result = tools.call("get_code_map", {"sourceSystem": "GDC"})

    assert error_code(result) == "invalid_request", result.content


# Capabilities the platform does not serve yet: each answers capability_unavailable (M1.2).
UNAVAILABLE = [
    pytest.param(
        "get_permissible_value",
        {"permissibleValueId": "9192925"},
        id="permissible-value",
        marks=[
            pytest.mark.tool("get_permissible_value"),
            pytest.mark.requirement("get_permissible_value-1"),
        ],
    ),
    pytest.param(
        "list_classification_schemes",
        {},
        id="classification-schemes",
        marks=[
            pytest.mark.tool("list_classification_schemes"),
            pytest.mark.requirement("list_contexts-1"),
        ],
    ),
    *[
        pytest.param(
            "search_data_elements",
            {"query": "gender", "mode": mode},
            id=f"search-{mode}",
            marks=[
                pytest.mark.tool("search_data_elements"),
                pytest.mark.requirement("search_data_elements-1"),
            ],
        )
        for mode in ("semantic", "hybrid")
    ],
]


@pytest.mark.parametrize(("name", "arguments"), UNAVAILABLE)
def test_a_capability_the_platform_lacks_is_unavailable_never_empty(tools, name, arguments):
    result = tools.call(name, arguments)

    assert error_code(result) == "capability_unavailable", result.content


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("get_data_element-2")
def test_a_question_text_no_data_element_has_is_not_found(tools, recorded):
    found = recorded("recorded/cadsr/question-text-unmatched.json")["response"]["body"]
    assert found["DataElements"] == []

    result = tools.call("get_data_element", {"questionText": "qqxyzzyqq"})

    assert error_code(result) == "not_found", result.content


# Crafted (PASS fixture only): no keyword search exists today, and the crafted answer of the
# inventory's form holds the contract's cap of 1,000 data elements.
OVER_CAP = "crafted/OP-C03/search-over-cap.json"


@pytest.mark.tool("search_data_elements")
@pytest.mark.requirement("search_data_elements-1")
def test_a_search_s_page_is_the_limit_given(tools, recorded):
    capped = len(recorded(OVER_CAP)["response"]["body"]["DataElements"])
    limit = 25
    assert limit < capped

    content = _ok(tools.call("search_data_elements", {"query": "patient", "limit": limit}))

    assert len(content.get("results", [])) == limit


@pytest.mark.tool("search_data_elements")
@pytest.mark.requirement("search_data_elements-1")
def test_a_search_the_platform_caps_reports_the_cap_and_no_total(tools, recorded):
    capped = len(recorded(OVER_CAP)["response"]["body"]["DataElements"])

    content = _ok(tools.call("search_data_elements", {"query": "patient"}))

    truncation = content.get("truncation") or {}
    assert (truncation.get("occurred"), truncation.get("bound")) == (True, "upstream_cap")
    assert (truncation.get("limit"), truncation.get("exact")) == (capped, False)
    assert truncation.get("omitted", 0) >= 1
    assert truncation.get("reached") == capped
    assert "totalKnown" not in content
    assert (
        len(content.get("results", []))
        == TOOLS["search_data_elements"]["bounds"]["limit"]["default"]
    )


# Crafted to the published contracts (PASS fixture only until credentials let the recorder
# replace them): CDE Match and the context list refuse an anonymous caller.
CREDENTIALED = "scenarios/cadsr/credentialed"


@pytest.mark.scenario("cadsr/credentialed")
@pytest.mark.tool("match_data_elements")
@pytest.mark.requirement("match_data_elements-1")
def test_data_element_matches_are_scored_and_rule_attributed_as_the_platform_says(tools, recorded):
    answer = recorded(f"{CREDENTIALED}/cde-match.json")["response"]["body"]["matchResults"]

    content = _ok(tools.call("match_data_elements", {"entities": [{"name": answer["entity"]}]}))

    found = [
        (
            m.get("entity"),
            m.get("score"),
            m.get("rule"),
            m.get("matchedText"),
            (m.get("dataElement") or {}).get("publicId"),
        )
        for m in content.get("matches", [])
    ]
    assert found == [
        (answer["entity"], m["score"], m["ruleDescription"], m["matchedText"], m["publicId"])
        for m in answer["matches"]
    ]


@pytest.mark.scenario("cadsr/credentialed")
@pytest.mark.tool("match_data_elements")
@pytest.mark.requirement("match_data_elements-1")
def test_each_entity_s_matches_are_named_for_it_in_the_order_given(tools, recorded):
    answers = [
        recorded(f"{CREDENTIALED}/{file}")["response"]["body"]["matchResults"]
        for file in ("cde-match.json", "cde-match-donor.json")
    ]
    entities = [{"name": answer["entity"]} for answer in answers]

    content = _ok(tools.call("match_data_elements", {"entities": entities}))

    found = [
        (m.get("entity"), (m.get("dataElement") or {}).get("publicId"))
        for m in content.get("matches", [])
    ]
    assert found == [(a["entity"], m["publicId"]) for a in answers for m in a["matches"]]


@pytest.mark.scenario("cadsr/credentialed")
@pytest.mark.tool("match_data_elements")
@pytest.mark.requirement("match_data_elements-1")
@pytest.mark.parametrize(
    "arguments",
    [
        {"modelVariant": "default"},
        {"similarityThreshold": 0.5},
        {
            "entities": [{"name": "Patient Gender"}]
            * (TOOLS["match_data_elements"]["lists"]["entities"] + 1)
        },
    ],
    ids=["model-variant", "similarity-threshold", "too-many-entities"],
)
def test_what_the_platform_does_not_take_is_an_invalid_request_never_ignored(tools, arguments):
    call = {"entities": [{"name": "Patient Gender"}]} | arguments

    result = tools.call("match_data_elements", call)

    assert error_code(result) == "invalid_request", result.content


@pytest.mark.scenario("cadsr/credentialed")
@pytest.mark.tool("match_data_elements")
@pytest.mark.requirement("match_data_elements-1")
def test_at_most_match_limit_matches_come_for_an_entity(tools, recorded):
    answer = recorded(f"{CREDENTIALED}/cde-match.json")["response"]["body"]["matchResults"]
    assert len(answer["matches"]) > 1

    content = _ok(
        tools.call(
            "match_data_elements", {"entities": [{"name": answer["entity"]}], "matchLimit": 1}
        )
    )

    found = [(m.get("dataElement") or {}).get("publicId") for m in content.get("matches", [])]
    assert found == [answer["matches"][0]["publicId"]]


TIMEOUTS = [
    pytest.param(
        name,
        arguments,
        id=name,
        marks=[pytest.mark.tool(name), pytest.mark.requirement(f"{name}-1")],
    )
    for name, arguments in [
        ("match_data_elements", {"entities": [{"name": "Patient Gender"}]}),
        ("match_value_meanings", {"values": ["Male"]}),
    ]
]


@pytest.mark.scenario("cadsr/match-timeout")
@pytest.mark.parametrize(("name", "arguments"), TIMEOUTS)
def test_matching_slower_than_the_match_timeout_is_a_timeout(tools, recorded, name, arguments):
    settings = recorded("scenarios/cadsr/match-timeout/settings.json")
    delays = {
        recorded(f"scenarios/cadsr/match-timeout/{file}")["response"]["delay_seconds"]
        for file in ("vm-match.json", "cde-match.json")
    }
    assert min(delays) > float(settings["NCI_SI_MATCH_TIMEOUT_SECONDS"])

    result = tools.call(name, arguments)

    assert error_code(result) == "timeout", result.content


@pytest.mark.tool("match_value_meanings")
@pytest.mark.requirement("match_value_meanings-1")
def test_a_match_with_no_concept_has_none_never_null(tools, recorded):
    crafted = recorded("crafted/OP-M02/vm-match-no-concept.json")
    (value,) = crafted["request"]["body"]
    (match,) = crafted["response"]["body"]["matchResults"][0]["matches"]
    assert (match["concept"], match["evsSource"]) == (None, None)

    content = _ok(tools.call("match_value_meanings", {"values": [value["name"]]}))

    (found,) = content.get("matches", [])
    item = found.get("item") or {}
    assert item.get("publicId") == match["itemId"]
    assert [key for key in ("concept", "evsSource") if key in item] == []


@pytest.mark.scenario("cadsr/credentialed")
@pytest.mark.tool("list_contexts")
@pytest.mark.requirement("list_contexts-1")
def test_the_contexts_are_the_registry_s_context_names(tools, recorded):
    names = recorded(f"{CREDENTIALED}/context-names.json")["response"]["body"]["contextNames"]
    assert len(names) <= TOOLS["list_contexts"]["bounds"]["limit"]["default"]

    content = _ok(tools.call("list_contexts", {}))

    contexts = content.get("contexts", [])
    assert [context.get("name") for context in contexts] == names
    assert [(c.get("provenance") or {}).get("release") for c in contexts] == [
        {"registry": "cadsr"}
    ] * len(names)


# Crafted (PASS fixture only): caDSR publishes no registry release today.
PUBLISHED = "scenarios/cadsr/with-registry-release/registry-releases.json"


def _published(recorded):
    (release,) = [
        r for r in recorded(PUBLISHED)["response"]["body"]["registryReleases"] if r["latest"]
    ]
    return release


@pytest.mark.scenario("cadsr/with-registry-release")
@pytest.mark.tool("resolve_registry_release")
@pytest.mark.requirement("resolve_registry_release-1")
def test_a_published_registry_release_is_returned(tools, recorded):
    release = _published(recorded)

    result = tools.call("resolve_registry_release", {})

    content = _ok(result)
    assert (content.get("published"), content.get("identifier")) == (True, release["identifier"])
    assert content.get("generatedAt") == release["generatedAt"]
    assert result.meta.get("ttlMs") == 0


@pytest.mark.scenario("cadsr/with-registry-release")
@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-21")
def test_a_published_registry_release_is_asked_for_and_named_with_its_date(
    tools, upstream, recorded
):
    release = _published(recorded)

    content = _ok(
        tools.call(
            "get_data_element", {"publicId": DATA_ELEMENT, "registryRelease": release["identifier"]}
        )
    )

    named = (content.get("provenance") or {}).get("release") or {}
    assert (named.get("registry"), named.get("identifier")) == ("cadsr", release["identifier"])
    assert str(named.get("date", "")).startswith(release["generatedAt"][:10])
    # Asked for, not only named: a content request carried the release.
    content_requests = [e for e in upstream.log() if "registry/releases" not in e["path"]]
    assert requests_naming(content_requests, [release["identifier"]])


@pytest.mark.scenario("cadsr/with-registry-release")
@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-21")
def test_a_registry_release_cadsr_does_not_list_fails_closed_where_it_lists_some(tools, recorded):
    listed = [r["identifier"] for r in recorded(PUBLISHED)["response"]["body"]["registryReleases"]]
    unlisted = "2026.06.01"
    assert unlisted not in listed

    result = tools.call("get_data_element", {"publicId": DATA_ELEMENT, "registryRelease": unlisted})

    assert error_code(result) == "release_not_available", result.content


@pytest.mark.scenario("cadsr/with-registry-release")
@pytest.mark.tool("get_code_map")
@pytest.mark.requirement("X-17")
@pytest.mark.parametrize(
    "pinned_first", [True, False], ids=["pinned-then-not", "unpinned-then-pinned"]
)
def test_a_cursor_keeps_the_registry_release_it_was_issued_with(
    tools, upstream, recorded, pinned_first
):
    pin = {"registryRelease": _published(recorded)["identifier"]}
    first_arguments = {"limit": 40} | (pin if pinned_first else {})
    first = _ok(tools.call("get_code_map", first_arguments))
    asked = requests_naming(
        [e for e in upstream.log() if "registry/releases" not in e["path"]],
        [pin["registryRelease"]],
    )
    assert bool(asked) is pinned_first
    cursor = first.get("nextCursor")
    assert cursor, "no nextCursor"

    presented = {"limit": 40, "cursor": cursor} | ({} if pinned_first else pin)
    result = tools.call("get_code_map", presented)

    assert error_code(result) == "invalid_request", result.content
