"""Independent API/MCP comparison rejects corrupted citations on its first attempt."""

import importlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from nci_si_acceptance.fixture_server import FixtureServer, load_fixtures

FIXTURES = Path(__file__).parents[1] / "fixtures"


def verifier():
    package = importlib.import_module("nci_si_acceptance")
    module = importlib.util.find_spec("nci_si_acceptance.citations")
    assert module is not None, "acceptance must expose its independent citation comparison"
    return importlib.import_module(f"{package.__name__}.citations").verify_citations


def identity(identifier, version):
    return {"publicId": identifier, "version": version}


def answer():
    return identity("2200604", "4") | {
        "dateModified": "mcp-date",
        "dataElementConcept": identity("2226947", "1")
        | {
            "objectClass": identity("2236731", "1"),
            "property": identity("2403945", "1"),
        },
        "valueDomain": identity("2222445", "3")
        | {
            "RepresentationTerm": identity("2234813", "1"),
        },
        "conceptAssociations": [
            {
                "role": role,
                "conceptCode": code,
                "terminology": "NCI_CONCEPT_CODE",
                "primary": True,
                "displayOrder": 0,
            }
            for role, code in [
                ("objectClass", "C25190"),
                ("property", "C28421"),
                ("representationTerm", "C25284"),
            ]
        ],
    }


class Tools:
    def __init__(self, content):
        self.content, self.calls = content, []

    def call(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        return SimpleNamespace(is_error=False, content=self.content)


@pytest.mark.parametrize("corruption", ["identity", "code", "source", "primary", "order"])
def test_citation_mismatch_keeps_both_dates_and_never_retries(corruption):
    check = verifier()
    content = answer()
    if corruption == "identity":
        del content["dataElementConcept"]["objectClass"]["publicId"]
    else:
        key, value = {
            "code": ("conceptCode", "C999"),
            "source": ("terminology", "other"),
            "primary": ("primary", False),
            "order": ("displayOrder", "0"),
        }[corruption]
        content["conceptAssociations"][0][key] = value
    tools = Tools(content)
    with FixtureServer(load_fixtures(FIXTURES)) as upstream:
        with pytest.raises(
            AssertionError, match=r"API dateModified=2026-08-25; MCP dateModified=mcp-date"
        ):
            check(tools, upstream.base_url("cadsr"))
        requests = upstream.log()
    assert [(row["path"], row["params"]) for row in requests] == [
        ("/NCIAPI/1.0/api/DataElement/2200604", {"version": ["4"]})
    ]
    assert tools.calls == [
        (
            "get_data_element",
            {
                "publicId": "2200604",
                "version": "4",
                "include": ["dataElementConcept", "conceptAssociations", "valueDomain"],
            },
        )
    ]


def test_citation_comparison_excludes_names_and_dates():
    check = verifier()
    tools = Tools(answer() | {"longName": "unrelated name"})
    with FixtureServer(load_fixtures(FIXTURES)) as upstream:
        check(tools, upstream.base_url("cadsr"))
        assert len(upstream.log()) == 1
    assert len(tools.calls) == 1
