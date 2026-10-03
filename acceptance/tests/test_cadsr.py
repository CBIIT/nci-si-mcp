"""The caDSR tools' own requirements (spec/requirements.yaml), and the cross-cutting ones only a
caDSR answer shows, each test against its tool.

What a test expects it reads from the recordings (the `recorded` fixture). A fact it cannot
read there is named beside it, with the fixture file that holds it.
"""

import json
import re
from http import HTTPStatus

import pytest

from nci_si_acceptance.results import error_code
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
LISTING = "recorded/cadsr-ftp/cde-xml-listing.json"
EXPORT = "releasedCDEsXML-OD.zip"
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


@pytest.mark.tool("resolve_registry_release")
@pytest.mark.requirement("resolve_registry_release-1")
def test_without_a_registry_release_the_export_date_stands_for_it(tools, recorded):
    listing = recorded(LISTING)["response"]["body"]
    (dated,) = re.findall(rf">{re.escape(EXPORT)}</a>\s+(\d{{4}}-\d{{2}}-\d{{2}})", listing)
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
    stated = ("publicId", "version", "longName", "workflowStatus", "registrationStatus")
    assert {key: content.get(key) for key in stated} == {key: element[key] for key in stated}


def _concepts(element):
    """The data element concept's concepts, by code and role."""

    concept = element["DataElementConcept"]
    return {
        (each["conceptCode"], role)
        for role, part in (("objectClass", "ObjectClass"), ("property", "Property"))
        for each in concept[part]["Concepts"]
    }


def _section(element, include):
    """What a section holds, read from the recording, in the form the test compares."""

    domain = element["ValueDomain"]
    sections = {
        "permissibleValues": lambda: [
            (value["publicId"], value["value"], value["ValueMeaning"]["publicId"])
            for value in domain["PermissibleValues"]
        ],
        "valueDomain": lambda: {k: v for k, v in domain.items() if k != "PermissibleValues"},
        "conceptAssociations": lambda: _concepts(element),
        "alternateNames": lambda: element["AlternateNames"],
        "classificationSchemes": lambda: [
            (scheme["publicId"], [item["publicId"] for item in scheme["ClassificationSchemeItems"]])
            for scheme in element["ClassificationSchemes"]
        ],
    }
    return sections[include]()


def _returned(content, include):
    """What a section of a result holds, in the form the test compares."""

    found = content.get(include)
    forms = {
        "permissibleValues": lambda: [
            (
                value.get("publicId"),
                value.get("value"),
                (value.get("valueMeaning") or {}).get("publicId"),
            )
            for value in found
        ],
        "conceptAssociations": lambda: {(c.get("conceptCode"), c.get("role")) for c in found},
        "classificationSchemes": lambda: [
            (scheme.get("publicId"), [item.get("publicId") for item in scheme.get("items", [])])
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


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("get_data_element-2")
def test_a_question_text_one_data_element_has_finds_it(tools, recorded):
    found = recorded("recorded/cadsr/question-text-sex-of-a-person.json")["response"]["body"]
    (element,) = found["DataElements"]

    content = _ok(tools.call("get_data_element", {"questionText": "Sex of a Person"}))

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


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-21")
def test_without_a_registry_release_an_item_names_the_registry_and_no_release(tools):
    content = _ok(tools.call("get_data_element", {"publicId": DATA_ELEMENT}))

    assert (content.get("provenance") or {}).get("release") == {"registry": "cadsr"}


@pytest.mark.tool("get_data_element")
@pytest.mark.requirement("X-21")
def test_a_registry_release_cadsr_does_not_publish_fails_closed(tools, recorded):
    # caDSR publishes none (registry-releases.json: 404), so any is one it does not publish.
    assert (
        recorded("recorded/cadsr/registry-releases.json")["response"]["status"]
        == HTTPStatus.NOT_FOUND
    )

    result = tools.call(
        "get_data_element", {"publicId": DATA_ELEMENT, "registryRelease": "2026.07.02"}
    )

    assert error_code(result) == "release_not_available", result.content


FORM = "recorded/cadsr/form-5406471.json"


@pytest.mark.tool("get_form")
@pytest.mark.requirement("get_form-1")
def test_a_form_returns_its_modules_and_its_status_unchanged_a_retired_one_too(tools, recorded):
    form = recorded(FORM)["response"]["body"]["form"]
    assert form["workflowStatus"] == "RETIRED ARCHIVED"

    content = _ok(tools.call("get_form", {"publicId": form["publicID"]}))

    assert (content.get("publicId"), content.get("version")) == (form["publicID"], form["version"])
    assert content.get("workflowStatus") == form["workflowStatus"]
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


# The fields of a matched item the recording gives directly.
MATCHED = ("itemType", "publicId", "concept", "evsSource")


def _matched(match):
    """A vmMatch match as the record names it, a null field absent."""

    return {
        "itemType": match["itemType"],
        "publicId": match["itemId"],
        "concept": match["concept"],
        "evsSource": match["evsSource"],
    }


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
        {key: value for key, value in _matched(match).items() if value is not None}
        for match in matches
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
    entry = next(entry for entry in _crdc(recorded) if entry.get("permissibleValues"))
    values = [
        (value["Permissible Value"], value["Concept Code"]) for value in entry["permissibleValues"]
    ]

    content = _ok(tools.call("get_code_map", {"dataElementId": entry["CDE Public ID"]}))

    (found,) = content.get("codeMaps", [])
    assert found.get("dataElement") == {
        "publicId": entry["CDE Public ID"],
        "version": entry["Version"],
    }
    assert (found.get("crdcName"), found.get("usedBy")) == (entry["CRDC Name"], _used_by(entry))
    assert found.get("valueLevelBinding") is True
    assert found.get("coverage") == sum(1 for _, code in values if code)
    assert [(v.get("value"), v.get("conceptCode")) for v in found.get("values", [])] == values


@pytest.mark.tool("get_code_map")
@pytest.mark.requirement("get_code_map-1")
def test_a_data_element_without_value_level_binding_says_so(tools, recorded):
    entry = next(entry for entry in _crdc(recorded) if "permissibleValues" not in entry)

    content = _ok(tools.call("get_code_map", {"dataElementId": entry["CDE Public ID"]}))

    (found,) = content.get("codeMaps", [])
    assert (found.get("valueLevelBinding"), found.get("values", [])) == (False, [])


@pytest.mark.tool("get_code_map")
@pytest.mark.requirement("get_code_map-1")
def test_a_context_selects_the_code_maps_it_uses(tools, recorded):
    users = [entry["CDE Public ID"] for entry in _crdc(recorded) if "GDC" in _used_by(entry)]
    # Within one page of the limit's default, so that the whole selection shows.
    assert 1 < len(users) <= TOOLS["get_code_map"]["bounds"]["limit"]["default"]

    content = _ok(tools.call("get_code_map", {"targetContext": "GDC"}))

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
