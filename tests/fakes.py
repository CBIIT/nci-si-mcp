"""Shared test doubles. Nothing here touches the network."""

from nci_si_mcp.evs import EVSNotFoundError
from nci_si_mcp.models import ReleaseInfo

RELATION_KEYS = frozenset(
    {"parents", "children", "roles", "inverseRoles", "associations", "inverseAssociations"}
)


def release(version="26.06e", date="2026-06-29"):
    return ReleaseInfo(
        terminology="ncit",
        version=version,
        date=date,
        name=f"NCI Thesaurus {version}",
        terminology_version=f"ncit_{version}",
        latest=True,
        monthly=True,
        weekly=False,
    )


def concept(code, name=None, version="26.06e", **fields):
    """An EVS concept payload; pass relation lists such as `children=[...]` as fields."""

    payload = {
        "code": code,
        "name": name or f"Concept {code}",
        "terminology": "ncit",
        "version": version,
        "definitions": [],
        "synonyms": [],
        "properties": [],
    }
    payload.update(fields)
    return payload


class FakeEVS:
    """In-memory stand-in for EVSClient that records the calls it receives.

    `calls` holds (method, terminology, argument) tuples and `includes` the
    include string of each batched concept request, which returns only the
    relation lists that string names. Set `errors[method]` to an exception to
    make that method fail.
    """

    def __init__(self, concepts=(), version="26.06e", descendants=None):
        self.release = release(version)
        self.concepts = {item["code"]: item for item in concepts}
        self.descendants = descendants or {}
        self.errors = {}
        self.calls = []
        self.includes = []

    def _record(self, method, terminology=None, argument=None):
        self.calls.append((method, terminology, argument))
        if method in self.errors:
            raise self.errors[method]

    def get_api_version(self):
        self._record("get_api_version")
        return {"version": "test"}

    def resolve_monthly_ncit_release(self):
        self._record("resolve_monthly_ncit_release")
        return self.release

    def get_concept(self, code, terminology="ncit", include=""):
        self._record("get_concept", terminology, code)
        if code not in self.concepts:
            raise EVSNotFoundError(f"{code} not found")
        return self.concepts[code]

    def get_concepts_by_codes(self, codes, terminology="ncit", include=""):
        codes = list(codes)
        self._record("get_concepts_by_codes", terminology, codes)
        self.includes.append(include)
        dropped = RELATION_KEYS.difference(include.split(","))
        return [
            {key: value for key, value in self.concepts[code].items() if key not in dropped}
            for code in codes
            if code in self.concepts
        ]

    def get_descendants(self, code, max_level, terminology="ncit"):
        self._record("get_descendants", terminology, (code, max_level))
        return [item for item in self.descendants.get(code, []) if item["level"] <= max_level]
