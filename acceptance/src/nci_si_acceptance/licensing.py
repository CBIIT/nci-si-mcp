"""What the fixture set may not redistribute: licensed terminology content.

EVS SOW v2.1 item 2: the mapping capability "shall not retrieve or redistribute
licensed target-terminology content, including SNOMED CT or MedDRA content, unless
NCI provides the applicable authorization, credentials, and license basis". A fixture
set furnished to contractors is redistribution. The manifest's deny list names the
licensed terminologies, by every name EVS gives them, and the mapsets that carry them:

- a request that names a denied terminology or mapset, in its path or a parameter, is
  not recorded, unless the service refused it (403): the refusal carries no content;
- in a payload, an item that comes from a denied terminology or maps to one (a map, a
  synonym) is licensed content. `record.py` removes it and the fixture lists what was
  removed; a self-test fails on any that remains.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Self

# The fields by which an item of a payload names the terminology it comes from or maps to.
SOURCE_FIELDS = ("source", "target", "targetTerminology")


def _names(path: str, params: dict[str, list[str]]) -> list[str]:
    """What a request names: its path segments and its parameter values, comma lists split."""

    values = [value for listed in params.values() for value in listed]
    return [*path.split("/"), *(part for value in values for part in value.split(","))]


@dataclass(frozen=True, slots=True)
class DenyList:
    terminologies: frozenset[str]
    mapsets: frozenset[str]

    @classmethod
    def from_manifest(cls, section: Any) -> Self:
        """The deny list from the manifest's `deny` section; terminology names in any case."""

        try:
            return cls(
                frozenset(name.casefold() for name in section["terminologies"]),
                frozenset(section["mapsets"]),
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("manifest.yaml: deny has terminologies and mapsets") from error

    def _denied(self, name: str) -> bool:
        """Whether a path segment or parameter value names a denied terminology or mapset.

        A pinned terminology is `{terminology}_{release}`, so a prefix counts.
        """

        folded = name.casefold()
        return name in self.mapsets or any(
            folded == term or folded.startswith(f"{term}_") for term in self.terminologies
        )

    def request_problem(self, path: str, params: dict[str, list[str]], status: int) -> str | None:
        """Why a request may not be recorded, if it may not."""

        denied = sorted({name for name in _names(path, params) if self._denied(name)})
        if denied and status != HTTPStatus.FORBIDDEN:
            return f"names licensed content ({', '.join(denied)}); only a refusal (403) is recorded"
        return None

    def licensed(self, item: Any) -> str | None:
        """The denied terminology a payload item comes from or maps to, if any."""

        if not isinstance(item, dict):
            return None
        return next(
            (
                item[field]
                for field in SOURCE_FIELDS
                if isinstance(item.get(field), str) and item[field].casefold() in self.terminologies
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
            if source := self.licensed(item):
                removed.append(f"{pointer}/{index} ({source})")
                continue
            value, lines = self.redact(item, f"{pointer}/{index}")
            kept.append(value)
            removed += lines
        return kept, removed
