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
PREFIXED = re.compile(r"[:/#\s]")


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

    schema = tools.available[result.tool].output_schema
    assert schema is not None
    errors = [error.message for error in Draft202012Validator(schema).iter_errors(result.content)]
    assert errors == []


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
    coded = [item for item in _items(tools, pinned, name) if "code" in item]

    # A prefix (NCIT:C4817), a URI or a fragment shows in punctuation no bare code carries.
    assert [item["code"] for item in coded if PREFIXED.search(str(item["code"]))] == []
    assert [item["code"] for item in coded if not item.get("terminology")] == []


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
    assert result.meta.get("cacheScope") == TOOLS[name].get("cacheScope", "public")
