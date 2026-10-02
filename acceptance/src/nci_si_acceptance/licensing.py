"""What the fixture set may not redistribute: licensed terminology content.

EVS SOW v2.1 item 2: the mapping capability "shall not retrieve or redistribute
licensed target-terminology content, including SNOMED CT or MedDRA content, unless
NCI provides the applicable authorization, credentials, and license basis". A fixture
set furnished to contractors is redistribution. The manifest's `licensing` section
names the licensed terminologies, by every name EVS gives them, the mapsets that carry
them, and the terminologies allowed:

- a request that names a licensed terminology or mapset, in its path or a parameter, is
  not recorded, unless the service refused it (403): the refusal carries no content;
- in a payload, an item that comes from a licensed terminology or maps to one (a map, a
  synonym) is licensed content. `record.py` removes it and the fixture lists what was
  removed; a self-test fails on any that remains;
- the check fails closed: a terminology a payload names that is on neither list stops
  the recording and fails the self-test, so a new name, or a new spelling of a licensed
  one, is decided once by a person rather than passed by default;
- after redaction no licensed name may be left anywhere in a payload: an item the
  redaction cannot remove (one that is not a list element) stops the recording too.

Names are compared in any case. The FHIR surface names a terminology by its system URI
and a mapset by a lowercase ConceptMap id with a release suffix, so the lists hold
those forms too, and a request's parameter values are split on `,?=&|` to find them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Self

# The fields by which an item of a payload names the terminology it comes from or maps to:
# EVS REST's sources and map targets, and FHIR's code systems.
SOURCE_FIELDS = ("source", "target", "targetTerminology", "system")
PARAMETER_PARTS = re.compile(r"[,?=&|]")


def _names(path: str, params: dict[str, list[str]]) -> list[str]:
    """What a request names: its path segments and its parameter values, comma lists split."""

    values = [value for listed in params.values() for value in listed]
    return [*path.split("/"), *(part for value in values for part in PARAMETER_PARTS.split(value))]


def _folded(names: Any) -> frozenset[str]:
    return frozenset(name.casefold() for name in names)


def _base(name: str) -> str:
    """A name as it is compared: case-folded, without a query (a FHIR value set URL)."""

    return PARAMETER_PARTS.split(name)[0].casefold()


def named_terminologies(payload: Any) -> set[str]:
    """Every terminology a payload names in a source field, at any depth."""

    if isinstance(payload, list):
        return set().union(*map(named_terminologies, payload))
    if not isinstance(payload, dict):
        return set()
    named = {payload[field] for field in SOURCE_FIELDS if isinstance(payload.get(field), str)}
    return named.union(*map(named_terminologies, payload.values()))


@dataclass(frozen=True, slots=True)
class Licensing:
    """Terminology and mapset names, case-folded."""

    licensed: frozenset[str]
    allowed: frozenset[str]
    mapsets: frozenset[str]

    @classmethod
    def from_manifest(cls, section: Any) -> Self:
        """The rules of the manifest's `licensing` section."""

        try:
            return cls(
                _folded(section["licensed"]),
                _folded(section["allowed"]),
                _folded(section["mapsets"]),
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(
                "manifest.yaml: licensing has licensed, allowed and mapsets"
            ) from error

    def licensed_names(self, payload: Any) -> list[str]:
        """The licensed terminologies a payload still names."""

        return sorted(name for name in named_terminologies(payload) if _base(name) in self.licensed)

    def undecided(self, payload: Any) -> list[str]:
        """The terminologies a payload names that are neither licensed nor allowed."""

        known = self.licensed | self.allowed
        return sorted(name for name in named_terminologies(payload) if _base(name) not in known)

    def _denied(self, name: str) -> bool:
        """Whether a path segment or parameter value names a licensed terminology or mapset.

        A pinned terminology is `{terminology}_{release}`, so a prefix counts.
        """

        folded = name.casefold()
        return any(
            folded == named or folded.startswith(f"{named}_")
            for named in self.licensed | self.mapsets
        )

    def request_problem(self, path: str, params: dict[str, list[str]], status: int) -> str | None:
        """Why a request may not be recorded, if it may not."""

        denied = sorted({name for name in _names(path, params) if self._denied(name)})
        if denied and status != HTTPStatus.FORBIDDEN:
            return f"names licensed content ({', '.join(denied)}); only a refusal (403) is recorded"
        return None

    def licensed_source(self, item: Any) -> str | None:
        """The licensed terminology a payload item comes from or maps to, if any."""

        if not isinstance(item, dict):
            return None
        return next(
            (
                item[field]
                for field in SOURCE_FIELDS
                if isinstance(item.get(field), str) and _base(item[field]) in self.licensed
            ),
            None,
        )

    def redact(self, payload: Any, pointer: str = "") -> tuple[Any, list[str]]:
        """The payload without its licensed items, and a line for each item removed."""

        if isinstance(payload, dict):
            return self._redact_object(payload, pointer)
        if isinstance(payload, list):
            return self._redact_list(payload, pointer)
        return payload, []

    def _redact_object(self, payload: dict[str, Any], pointer: str) -> tuple[Any, list[str]]:
        kept, removed = {}, []
        for key, value in payload.items():
            kept[key], lines = self.redact(value, f"{pointer}/{key}")
            removed += lines
        return kept, removed

    def _redact_list(self, payload: list[Any], pointer: str) -> tuple[Any, list[str]]:
        kept, removed = [], []
        for index, item in enumerate(payload):
            if source := self.licensed_source(item):
                removed.append(f"{pointer}/{index} ({source})")
                continue
            value, lines = self.redact(item, f"{pointer}/{index}")
            kept.append(value)
            removed += lines
        return kept, removed
