"""The concept rules: EVS concept requests answered from one recording per concept.

EVS's concept endpoints return any projection (`include`) of a concept, alone
(`/api/v1/concept/{terminology}/{code}`) or in a batch (`/api/v1/concept/{terminology}
?list=`). Rather than one recording per request form, the fixture set records each
concept once, and two rules compose the answer EVS gives; `record.py` checks the
composition against real projections and batches:

- project by include: an answer holds the base keys and the keys each include value
  adds (the manifest's table); a request without include gets the default. EVS
  leaves out an empty list, and so does a recording.
- select by list: a batch answer holds each requested code once and leaves out a
  code EVS does not know. EVS keeps no order: the same request answered in two orders
  a minute apart on 2 October 2026, mostly but not always lexicographic. The rules
  answer in the request's order rotated by one, which differs from it whenever two
  codes are found, so that pairing answers with requests by position fails here
  rather than only sometimes against EVS.

A recording covers the keys its own include names. The rules answer only what the
recordings cover, so a missing recording is never taken for an empty answer: a key a
recording does not cover, an include value outside the table, a code without a
recording, or any other parameter leaves the request unanswered. A recording of a 404
says EVS does not know the code: a request for that code alone gets the 404, and a
batch leaves the code out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Callable

CONCEPT_PATH = re.compile(r"/api/v1/concept/(?P<terminology>[^/]+)(?:/(?P<code>[^/]+))?")


@dataclass(frozen=True, slots=True)
class Recording:
    """One concept as recorded: its fixture, the answer EVS gave, and the keys it covers."""

    name: str
    status: int
    body: Any
    covers: frozenset[str]


type Find = Callable[[str, str], Recording | None]


@dataclass(frozen=True, slots=True)
class Answer:
    names: list[str]
    status: int
    body: Any


@dataclass(frozen=True, slots=True)
class ConceptRules:
    """The include table: the keys every answer holds, and those each include value adds."""

    base: frozenset[str]
    include: dict[str, frozenset[str]]
    default: str

    @classmethod
    def from_manifest(cls, section: Any) -> Self:
        """The rules from the manifest's `evs.concepts` section."""

        try:
            rules = cls(
                frozenset(section["base"]),
                {value: frozenset(keys) for value, keys in section["include"].items()},
                section["default"],
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("manifest.yaml: evs.concepts has base, include and default") from error
        if rules.default not in rules.include:
            raise ValueError("manifest.yaml: evs.concepts.default is one of its include values")
        return rules

    def keys(self, include: str) -> frozenset[str] | None:
        """The keys an include value names; None for a value outside the table."""

        values = include.split(",")
        if not all(value in self.include for value in values):
            return None
        return self.base.union(*(self.include[value] for value in values))

    def recording_problem(self, request: dict[str, Any], status: int, body: Any) -> str | None:
        """What makes a fixture unusable as a concept recording, if anything."""

        match = CONCEPT_PATH.fullmatch(request["path"])
        if (request["surface"], request["method"]) != ("evs", "GET") or not (
            match and match["code"]
        ):
            return "a concept recording is a GET of /api/v1/concept/{terminology}/{code} from evs"
        return self._include_problem(request.get("params", {})) or _answer_problem(
            match["code"], status, body
        )

    def _include_problem(self, params: dict[str, list[str]]) -> str | None:
        if set(params) != {"include"} or len(params["include"]) != 1:
            return "a concept recording names exactly the include it was recorded with"
        if self.keys(params["include"][0]) is None:
            return f"include {params['include'][0]} is outside the manifest's table"
        return None

    def answer(self, path: str, params: dict[str, list[str]], find: Find) -> Answer | None:
        """EVS's answer to a concept request, composed from the recordings; None if they cannot."""

        match = CONCEPT_PATH.fullmatch(path)
        if match is None or not _composable(params, single=match["code"] is not None):
            return None
        keys = self.keys(params.get("include", [self.default])[0])
        if keys is None:
            return None
        terminology, code = match["terminology"], match["code"]
        if code:
            return _single(find(terminology, code), keys)
        return _batch([find(terminology, each) for each in _codes(params["list"][0])], keys)


def recording_key(path: str) -> tuple[str, str]:
    """The terminology and code a concept recording's request path names."""

    match = CONCEPT_PATH.fullmatch(path)
    if match is None or match["code"] is None:
        raise ValueError(f"{path} is not the path of one concept")
    return match["terminology"], match["code"]


def project(concept: dict[str, Any], keys: frozenset[str]) -> dict[str, Any]:
    return {key: value for key, value in concept.items() if key in keys}


def _answer_problem(code: str, status: int, body: Any) -> str | None:
    if status not in (HTTPStatus.OK, HTTPStatus.NOT_FOUND):
        return "a concept recording answers 200 or 404"
    if status == HTTPStatus.OK and (not isinstance(body, dict) or body.get("code") != code):
        return f"a concept recording's body is the concept {code}"
    return None


def _composable(params: dict[str, list[str]], *, single: bool) -> bool:
    """Whether the rules know the request's parameters: include, and list for a batch."""

    if any(len(values) != 1 for values in params.values()):
        return False
    if single:
        return set(params) <= {"include"}
    return "list" in params and set(params) <= {"include", "list"}


def _codes(listed: str) -> list[str]:
    """The codes of a batch in the order the rules answer them: once each, in the
    request's order rotated by one."""

    codes = list(dict.fromkeys(listed.split(",")))
    return codes[1:] + codes[:1]


def _usable(recording: Recording | None, keys: frozenset[str]) -> bool:
    if recording is None:
        return False
    return recording.status == HTTPStatus.NOT_FOUND or keys <= recording.covers


def _single(recording: Recording | None, keys: frozenset[str]) -> Answer | None:
    if recording is None or not _usable(recording, keys):
        return None
    if recording.status == HTTPStatus.NOT_FOUND:
        return Answer([recording.name], recording.status, recording.body)
    return Answer([recording.name], HTTPStatus.OK, project(recording.body, keys))


def _batch(recordings: list[Recording | None], keys: frozenset[str]) -> Answer | None:
    if not all(_usable(recording, keys) for recording in recordings):
        return None
    found = [recording for recording in recordings if recording is not None]
    return Answer([each.name for each in found], HTTPStatus.OK, _known(found, keys))


def _known(recordings: list[Recording], keys: frozenset[str]) -> list[dict[str, Any]]:
    """The projected concepts of a batch; the codes EVS does not know are left out."""

    return [project(each.body, keys) for each in recordings if each.status == HTTPStatus.OK]
