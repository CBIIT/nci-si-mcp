"""Cross-cutting tests (the X requirements of spec/requirements.yaml), each against every
content-returning tool whose call is in calls.yaml."""

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from nci_si_acceptance.spec import RECORDS, TOOLS, items_of, parameters

CALLS = yaml.safe_load((Path(__file__).parent / "calls.yaml").read_text(encoding="utf-8"))
FIELDS = RECORDS["provenance"]["fields"] | RECORDS["traversal"]["fields"]
# What every item's provenance carries (X-7), and what an item reached by traversal adds.
CARRIED = ("release", "source", "retrievedAt", "servedBy")
REACHED = ("relationship", "direction", "polarity")
# The bare form NCIt publishes its codes in (A1.2: C4817); other terminologies are held only to
# carry no URI, since some publish punctuation in their codes (HGNC:3508, ICD-O-3 8001/3).
BARE = {"ncit": re.compile(r"[A-Z][0-9]+")}


def _per_tool(names):
    """One case per tool, counted for that tool in the report and run with its scenario."""

    return [
        pytest.param(
            name,
            id=name,
            marks=[pytest.mark.tool(name), *[pytest.mark.scenario(s) for s in _scenarios(name)]],
        )
        for name in names
    ]


def _scenarios(name):
    return [CALLS[name]["scenario"]] if "scenario" in CALLS[name] else []


CALLED = _per_tool(CALLS)
PINNED = _per_tool(name for name in CALLS if "release" in parameters(name)[0])


def _call(tools, pinned, name):
    """The tool's call in calls.yaml, pinned to the fixture set's release where it takes one."""

    taken = parameters(name)[0]
    arguments = {key: value for key, value in pinned.items() if key in taken}
    return tools.call(name, arguments | CALLS[name]["arguments"])


def _items(tools, pinned, name):
    """The items of a successful call; none is a failure, since every call returns content."""

    result = _call(tools, pinned, name)
    assert not result.is_error, result.content
    found = items_of(name, result.content)
    assert found, f"no item where {TOOLS[name]['items']} say: {result.content!r:.300}"
    return found


def _provenance(item):
    return item.get("provenance") or {} if isinstance(item, dict) else {}


def _wrong(provenance, names):
    """The fields among `names` that are missing, or hold a value outside their closed set."""

    return [name for name in names if not _valid(provenance.get(name), FIELDS[name])]


def _valid(value, field):
    return value is not None and value in field.get("values", [value])


def _codes(item):
    """Each code an item carries, with the terminology beside it."""

    if not isinstance(item, dict):
        return []
    coded = [key for key in item if key == "code" or key.endswith("Code")]
    return [(item[key], item.get(_beside(key))) for key in coded]


def _beside(key):
    """Where the terminology of a code is: `code` with `terminology`, `targetCode` with
    `targetTerminology`."""

    return "terminology" if key == "code" else key.removesuffix("Code") + "Terminology"


def _bare(code, terminology):
    code = str(code)
    pattern = BARE.get(str(terminology).lower())
    uri = "://" in code or code.lower().startswith("urn:")
    return not uri and (pattern is None or pattern.fullmatch(code) is not None)


def _timestamp(value):
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except (TypeError, ValueError):
        return False


@pytest.mark.requirement("X-1")
@pytest.mark.parametrize("name", PINNED)
def test_every_item_carries_the_release_requested(tools, pinned, name):
    found = _items(tools, pinned, name)

    releases = [_provenance(item).get("release") or {} for item in found]
    named = {(release.get("terminology"), release.get("identifier")) for release in releases}
    assert named == {(pinned["terminology"], pinned["release"])}


@pytest.mark.requirement("X-6")
@pytest.mark.parametrize("name", CALLED)
def test_a_result_validates_against_the_declared_output_schema(tools, pinned, name):
    result = _call(tools, pinned, name)

    assert not result.is_error, result.content
    schema = tools.available[result.tool].output_schema
    assert schema is not None
    errors = [error.message for error in Draft202012Validator(schema).iter_errors(result.content)]
    assert errors == []


@pytest.mark.requirement("X-14")
@pytest.mark.parametrize("name", CALLED)
def test_a_result_is_an_object(tools, pinned, name):
    assert isinstance(_call(tools, pinned, name).content, dict)


@pytest.mark.requirement("X-7")
@pytest.mark.parametrize("name", CALLED)
def test_every_item_carries_its_provenance(tools, pinned, name):
    provenances = [_provenance(item) for item in _items(tools, pinned, name)]

    assert [_wrong(provenance, CARRIED) for provenance in provenances] == [[]] * len(provenances)
    assert all(_timestamp(provenance.get("retrievedAt")) for provenance in provenances)


@pytest.mark.requirement("X-7")
@pytest.mark.parametrize("name", _per_tool(name for name in CALLS if TOOLS[name].get("traversal")))
def test_an_item_reached_by_traversal_says_how(tools, pinned, name):
    provenances = [_provenance(item) for item in _items(tools, pinned, name)]
    depths = [provenance.get("depth") for provenance in provenances]

    assert all(isinstance(depth, int) and depth >= 0 for depth in depths)
    assert max(depths) > 0, "the call reaches no item by traversal"
    reached = [provenance for provenance in provenances if provenance["depth"] > 0]
    assert [_wrong(provenance, REACHED) for provenance in reached] == [[]] * len(reached)


@pytest.mark.requirement("X-9")
@pytest.mark.parametrize("name", CALLED)
def test_codes_are_bare_with_their_terminology_beside_them(tools, pinned, name):
    # A1.2 is about the codes items carry, in `code` or a field such as `targetCode`; an item
    # without one, such as an edge, has none to check.
    codes = [pair for item in _items(tools, pinned, name) for pair in _codes(item)]

    assert [code for code, terminology in codes if not _bare(code, terminology)] == []
    assert [code for code, terminology in codes if not terminology] == []


# A server that answered the same call before may serve it from its cache, asking nothing.
@pytest.mark.own_server
@pytest.mark.requirement("X-11")
@pytest.mark.parametrize("name", CALLED)
def test_no_upstream_request_is_repeated_within_a_call(tools, upstream, pinned, name):
    _call(tools, pinned, name)

    requests = Counter(
        json.dumps([entry[key] for key in ("surface", "method", "path", "params", "body")])
        for entry in upstream.log()
    )
    assert requests, "the call made no upstream request"
    assert [request for request, count in requests.items() if count > 1] == []


@pytest.mark.requirement("X-13")
@pytest.mark.parametrize("name", PINNED)
def test_a_release_pinned_result_may_be_cached(tools, pinned, name):
    result = _call(tools, pinned, name)

    assert not result.is_error
    assert result.meta.get("ttlMs", 0) > 0
    # Governed content; the results computed from caller-supplied values are caDSR's (M2.3).
    assert result.meta.get("cacheScope") == "public"
