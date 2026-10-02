"""Craft the scenario fixtures the live services do not produce on demand.

    pdm run acceptance-craft [--fixtures DIR]

Each scenario is built here, from the recorded set where it changes a real answer, so
that it follows a re-recording, or from scratch where it is synthetic. Every crafted
fixture names the requirement it stands in for. The self-tests check that the files
this writes are the ones in the fixture set. The scenarios recorded from live
(`release/unknown`, `batch/silent-drop`, `retired/with-replacement`, the refusal of
`license/restricted`) come from `record.py`.

Synthetic concepts use codes from C99000000 up, which NCIt does not use.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nci_si_acceptance.fixture_server import CONCEPTS, EVERY_PATH
from nci_si_acceptance.record import (
    DISCOVERY,
    FIXTURES,
    RECORDED,
    RELEASE_FIELDS,
    reported_releases,
    write,
)
from nci_si_acceptance.spec import RECORDS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

type Documents = dict[str, dict[str, Any]]

RELEASE, OTHER_RELEASE = "26.09d", "26.08e"
# A second release tagged monthly beside the pinned one (release/duplicate-tag).
DUPLICATE_RELEASE = "26.09e"
# The release the release/unknown scenario serves nothing of.
UNKNOWN = "99.99z"
TERMINOLOGY = f"ncit_{RELEASE}"
EXCLUSION_ROLES = frozenset(RECORDS["traversal"]["fields"]["polarity"]["exclusions"]["ncit"])
# Positive roles of C4817 given a name that reads as an exclusion in traversal/exclusions.
MISLEADING_POSITIVE_ROLES = frozenset({"R108", "R116"})
# More nodes at depth 1 than the 1,000-node maximum, and a chain deeper than depth 4.
FANOUT, CHAIN = 1001, 5
STARVED_ROLES, STARVED_ASSOCIATIONS = 300, 2
LICENCE_KEY = "acceptance-licence-key"
# The positions, in the recorded expansion, of the members valueset/inactive-members marks
# inactive: early, so that a short page shows them.
INACTIVE_MEMBERS = (1, 3)


class Recorded:
    """The recorded fixture set, read by file."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def __call__(self, file: str) -> dict[str, Any]:
        return json.loads((self.root / file).read_text(encoding="utf-8"))

    def files(self) -> list[str]:
        """Every recorded and derived fixture, by file."""

        return sorted(
            path.relative_to(self.root).as_posix()
            for directory in (RECORDED, "crafted")
            for path in (self.root / directory).rglob("*.json")
        )


def crafted(requirement: str, request: dict[str, Any], **answer: Any) -> dict[str, Any]:
    """A crafted fixture; `answer` is its `response` or its `responses`."""

    return {"kind": "crafted", "requirement": requirement, "request": request} | answer


def concept_request(code: str) -> dict[str, Any]:
    return {
        "surface": "evs",
        "method": "GET",
        "path": f"/api/v1/concept/{TERMINOLOGY}/{code}",
        "params": {"include": ["full"]},
    }


def concept(code: str, name: str, **relations: list[dict[str, Any]]) -> dict[str, Any]:
    """A synthetic concept in EVS's shape, its empty relation lists left out as EVS does."""

    body = {"code": code, "name": name, "terminology": "ncit", "version": RELEASE}
    body |= {"conceptStatus": "DEFAULT", "leaf": not relations.get("children"), "active": True}
    return body | {key: value for key, value in relations.items() if value}


def link(code: str, name: str) -> dict[str, Any]:
    return {"code": code, "name": name}


def related(kind: str, kind_code: str, code: str, name: str) -> dict[str, Any]:
    return {"code": kind_code, "type": kind, "relatedCode": code, "relatedName": name}


def recording(scenario: str, requirement: str, body: dict[str, Any]) -> Documents:
    file = f"scenarios/{scenario}/{CONCEPTS}/{body['code']}.json"
    response = {"status": 200, "body": body}
    return {file: crafted(requirement, concept_request(body["code"]), response=response)}


def _with_release(payload: Any, release: str) -> Any:
    """The payload with every field that names the pinned release naming `release`."""

    if isinstance(payload, list):
        return [_with_release(item, release) for item in payload]
    if not isinstance(payload, dict):
        return payload
    return {
        key: release
        if key in RELEASE_FIELDS and value == RELEASE
        else _with_release(value, release)
        for key, value in payload.items()
    }


def mismatched(recorded: Recorded) -> list[str]:
    """The fixtures release/mismatch serves from another release: every recorded or
    derived one whose payload reports the pinned release, except release discovery."""

    return [
        file
        for file in recorded.files()
        if file not in DISCOVERY
        and RELEASE in reported_releases(recorded(file)["response"]["body"])
    ]


def release_mismatch(recorded: Recorded) -> Documents:
    """Every payload that reports the pinned release reports another one instead."""

    documents = {}
    for file in mismatched(recorded):
        source = recorded(file)
        path = Path(file)
        name = (
            f"{CONCEPTS}/{path.name}"
            if path.parent.name == CONCEPTS
            else f"{path.parent.name}-{path.name}"
        )
        body = _with_release(source["response"]["body"], OTHER_RELEASE)
        documents[f"scenarios/release/mismatch/{name}"] = crafted(
            "A3.4: the content served names another release than the one requested",
            source["request"],
            response=source["response"] | {"body": body},
        )
    return documents


WEEKLY_RELEASE = "26.10a"


def release_two_latest(recorded: Recorded) -> Documents:
    """A monthly and a weekly release both `latest`, the weekly row first in every list:
    a server taking the first `latest` row resolves the wrong monthly release. Every
    form of the release query gives each channel the same release."""

    monthly_source = recorded("recorded/evs/release-monthly.json")
    weekly_source = recorded("recorded/evs/release-weekly.json")
    monthly = monthly_source["response"]["body"][0] | {"tags": {"monthly": "true"}}
    weekly = monthly | {
        "version": WEEKLY_RELEASE,
        "terminologyVersion": f"ncit_{WEEKLY_RELEASE}",
        "date": "2026-10-05",
        "name": f"NCI Thesaurus {WEEKLY_RELEASE}",
        "tags": {"weekly": "true"},
    }
    listing = recorded("recorded/evs/terminologies.json")
    rows = [
        replaced
        for row in listing["response"]["body"]
        for replaced in (
            [weekly, monthly] if row == monthly_source["response"]["body"][0] else [row]
        )
    ]
    requirement = "A3.6.1-A3.6.3: two releases carry latest at once, one per channel"
    no_channel = monthly_source["request"] | {
        "params": {"terminology": ["ncit"], "latest": ["true"]}
    }
    answers = {
        "latest": (no_channel, [weekly, monthly]),
        "monthly": (monthly_source["request"], [monthly]),
        "weekly": (weekly_source["request"], [weekly]),
        "terminologies": (listing["request"], rows),
    }
    return {
        f"scenarios/release/two-latest/{name}.json": crafted(
            requirement, request, response={"status": 200, "body": body}
        )
        for name, (request, body) in answers.items()
    }


def release_duplicate_tag(recorded: Recorded) -> Documents:
    """Two releases both latest with the monthly tag: the monthly query, and the listing, name
    both, so the monthly channel's current release cannot be named (A3.6.3)."""

    monthly_source = recorded("recorded/evs/release-monthly.json")
    monthly = monthly_source["response"]["body"][0] | {"tags": {"monthly": "true"}}
    duplicate = monthly | {
        "version": DUPLICATE_RELEASE,
        "terminologyVersion": f"ncit_{DUPLICATE_RELEASE}",
        "name": f"NCI Thesaurus {DUPLICATE_RELEASE}",
    }
    listing = recorded("recorded/evs/terminologies.json")
    rows = [
        replaced
        for row in listing["response"]["body"]
        for replaced in (
            [monthly, duplicate] if row == monthly_source["response"]["body"][0] else [row]
        )
    ]
    requirement = "A3.6.3: a release with duplicate tags fails closed"
    answers = {
        "monthly": (monthly_source["request"], [monthly, duplicate]),
        "terminologies": (listing["request"], rows),
    }
    return {
        f"scenarios/release/duplicate-tag/{name}.json": crafted(
            requirement, request, response={"status": 200, "body": body}
        )
        for name, (request, body) in answers.items()
    }


def traversal_deep_fanout(_: Recorded) -> Documents:
    """A root with more children than the node maximum, one of them heading a chain
    deeper than the depth maximum."""

    requirement = "A5.1-A5.4: descendants beyond the depth and node bounds"
    root = link("C99000000", "Synthetic Fanout Root")
    children = [
        link(f"C{99000001 + index}", f"Synthetic Fanout Child {index + 1}")
        for index in range(FANOUT)
    ]
    chain = [
        link(f"C{99100001 + index}", f"Synthetic Chain Level {index + 2}")
        for index in range(CHAIN + 1)
    ]
    documents = recording("traversal/deep-fanout", requirement, concept(**root, children=children))
    for index, child in enumerate(children):
        below = [chain[0]] if index == 0 else []
        documents |= recording(
            "traversal/deep-fanout", requirement, concept(**child, parents=[root], children=below)
        )
    # Each chain node is recorded with the next below it; the last is never reached.
    parents = [children[0], *chain[:-2]]
    for node, parent, deeper in zip(chain[:-1], parents, chain[1:], strict=True):
        documents |= recording(
            "traversal/deep-fanout",
            requirement,
            concept(**node, parents=[parent], children=[deeper]),
        )
    return documents


def traversal_exclusions(recorded: Recorded) -> Documents:
    """The exclusion roles named as positive ones, and two positive roles named as
    exclusions, in C4817 and the catalogue alike: only polarity by code is right. C4817 gains a
    role of each exclusion code it lacks, to its first role's target, so that every code of the
    set is shown."""

    requirement = "A5.6, A5.7, E-4: polarity by relationship code, not by name"
    roles = recorded("recorded/evs/roles.json")
    named = {role["code"]: role["name"] for role in roles["response"]["body"]}
    source = recorded(f"recorded/evs/{CONCEPTS}/C4817.json")["response"]["body"]
    first = source["roles"][0]
    lacking = sorted(EXCLUSION_ROLES - {role["code"] for role in source["roles"]})
    added = [first | {"code": code, "type": named[code]} for code in lacking]
    body = source | {
        "roles": [
            role | {"type": _misleading(role["code"], role["type"])}
            for role in [*source["roles"], *added]
        ]
    }
    catalogue = [
        role | {"name": _misleading(role["code"], role["name"])}
        for role in roles["response"]["body"]
    ]
    return recording("traversal/exclusions", requirement, body) | {
        "scenarios/traversal/exclusions/roles.json": crafted(
            requirement, roles["request"], response={"status": 200, "body": catalogue}
        )
    }


def _misleading(code: str, name: str) -> str:
    if code in EXCLUSION_ROLES:
        return name.replace("_Excludes_", "_Without_")
    if code in MISLEADING_POSITIVE_ROLES:
        return f"{name}_Excludes_None"
    return name


def traversal_starvation(_: Recorded) -> Documents:
    """Two hubs over the same targets: one with many roles and few associations, the other the
    other way round, so that a budget spent in a fixed order of kinds starves the small kind of
    one of them."""

    requirement = "A5.5: a budget per relationship kind; no kind starved"
    many = [
        link(f"C{99200001 + index}", f"Synthetic Role Target {index + 1}")
        for index in range(STARVED_ROLES)
    ]
    few = [
        link(f"C{99200001 + STARVED_ROLES + index}", f"Synthetic Associated Concept {index + 1}")
        for index in range(STARVED_ASSOCIATIONS)
    ]
    documents = recording(
        "traversal/starvation",
        requirement,
        _hub("C99200000", "Synthetic Starvation Hub", many, few),
    )
    documents |= recording(
        "traversal/starvation",
        requirement,
        _hub("C99200400", "Synthetic Association Hub", few, many),
    )
    for target in [*many, *few]:
        documents |= recording("traversal/starvation", requirement, concept(**target))
    return documents


def _hub(code: str, name: str, role_targets: list, associated: list) -> dict[str, Any]:
    return concept(
        code,
        name,
        roles=[related("Disease_Has_Finding", "R108", **target) for target in role_targets],
        associations=[related("Concept_In_Subset", "A8", **target) for target in associated],
    )


def upstream_unavailable(_: Recorded) -> Documents:
    """Every request to either EVS surface, whatever its path: a refused connection (as near
    as a fixture can: closed), then 503, then no answer at all, the connection held past the
    server's timeout and closed, the last repeating."""

    requirement = "A2.5, A5.3: bounded retries, counted, then a structured error"
    responses = [
        {"fault": "close"},
        {"status": 503, "body": {"message": "Service Unavailable"}},
        {"fault": "close", "delay_seconds": 3},
    ]
    documents: Documents = {
        "scenarios/upstream/unavailable/settings.json": {"NCI_SI_TIMEOUT_SECONDS": "1"}
    }
    for surface in ("evs", "evs-fhir"):
        request = {
            "surface": surface,
            "method": "GET",
            "path": EVERY_PATH,
            "ignored": {"*": "crafted: an unavailable service answers no request at all"},
        }
        documents[f"scenarios/upstream/unavailable/{surface}.json"] = crafted(
            requirement, request, responses=responses
        )
    return documents


def release_unknown_expand(recorded: Recorded) -> Documents:
    """$expand pinned by system-version to the release release/unknown names: as every other
    pinned form of that release, it answers 404. An ordinary fixture, since no other request
    names that release; crafted, since EVS refuses system-version altogether today (400)."""

    pinned = recorded("crafted/OP-F05/expand-c85492.json")["request"]
    (version,) = pinned["params"]["system-version"]
    request = pinned | {
        "params": pinned["params"] | {"system-version": [version.replace(RELEASE, UNKNOWN)]}
    }
    outcome = {
        "resourceType": "OperationOutcome",
        "issue": [
            {"severity": "error", "code": "not-found", "diagnostics": "Terminology not found"}
        ],
    }
    return {
        "crafted/OP-F05/expand-c85492-unknown-release.json": crafted(
            "OP-F05: a pinned $expand of an unknown release fails as every pinned path does",
            request,
            response={"status": 404, "body": outcome},
        )
    }


def valueset_inactive_members(recorded: Recorded) -> Documents:
    """The recorded expansion, pinned and unpinned, with two members marked inactive as FHIR
    R4 marks them (contains.inactive). EVS was not seen to mark any: C85492 holds none."""

    documents = {}
    for name, file in (
        ("expand", "recorded/evs-fhir/expand-c85492.json"),
        ("expand-pinned", "crafted/OP-F05/expand-c85492.json"),
    ):
        source = recorded(file)
        body = source["response"]["body"]
        members = [
            member | ({"inactive": True} if position in INACTIVE_MEMBERS else {})
            for position, member in enumerate(body["expansion"]["contains"])
        ]
        expansion = body["expansion"] | {"contains": members}
        documents[f"scenarios/valueset/inactive-members/{name}.json"] = crafted(
            "A8.3: activeOnly leaves out the members an expansion marks inactive. EVS was never"
            " seen to mark one, so against live EVS activeOnly cannot be shown to do anything"
            " (an upstream question, #42)",
            source["request"],
            response=source["response"] | {"body": body | {"expansion": expansion}},
        )
    return documents


def upstream_rate_limited(recorded: Recorded) -> Documents:
    """429 with Retry-After on the release query, then the recorded answer."""

    source = recorded("recorded/evs/release-monthly.json")
    limited = {
        "status": 429,
        "headers": {"Retry-After": "1"},
        "body": {"message": "Too Many Requests"},
    }
    return {
        "scenarios/upstream/rate-limited/release.json": crafted(
            "E-7, P-1: back-off honoured and counted",
            source["request"],
            responses=[limited, source["response"]],
        )
    }


# Invented content: no licence key is available to record EVS's answer with one.
LICENSED_CONCEPT = {
    "code": "10000000",
    "name": "Placeholder licensed term",
    "terminology": "mdr",
    "version": "29_0",
    "conceptStatus": "DEFAULT",
    "leaf": True,
    "active": True,
}


def license_restricted(_: Recorded) -> Documents:
    """With the licence key from configuration, a licensed concept is served; without
    it, EVS's refusal (recorded by record.py) answers."""

    requirement = "E-7, A7.5: the licence key sent from configuration"
    request = {
        "surface": "evs",
        "method": "GET",
        "path": "/api/v1/concept/mdr_29_0/10000000",
        "headers": {"X-EVSRESTAPI-License-Key": LICENCE_KEY},
        "ignored": {"*": "placeholder content answers every projection alike"},
    }
    document = crafted(requirement, request, response={"status": 200, "body": LICENSED_CONCEPT})
    return {
        # At debug level, so that a key logged as a detail shows (A7.5).
        "scenarios/license/restricted/settings.json": {
            "NCI_SI_EVS_LICENSE_KEY": LICENCE_KEY,
            "NCI_SI_LOG_LEVEL": "DEBUG",
        },
        "scenarios/license/restricted/granted.json": document,
    }


SCENARIOS: tuple[Callable[[Recorded], Documents], ...] = (
    release_mismatch,
    release_two_latest,
    release_duplicate_tag,
    traversal_deep_fanout,
    traversal_exclusions,
    traversal_starvation,
    release_unknown_expand,
    valueset_inactive_members,
    upstream_unavailable,
    upstream_rate_limited,
    license_restricted,
)


def craft(
    root: Path, scenarios: Iterable[Callable[[Recorded], Documents]] = SCENARIOS
) -> Documents:
    """Every crafted scenario fixture, by file."""

    recorded = Recorded(root)
    documents: Documents = {}
    for scenario in scenarios:
        documents |= scenario(recorded)
    return documents


def main(arguments: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Craft the scenario fixtures.")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES, help="the fixture directory")
    options = parser.parse_args(arguments)
    documents = craft(options.fixtures)
    write(options.fixtures, documents)
    sys.stdout.write(f"Crafted {len(documents)} fixtures.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
