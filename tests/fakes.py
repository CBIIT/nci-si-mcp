"""Shared test doubles. Nothing here touches the network."""

from nci_si_mcp.evs import INDEX_INCLUDE, LOOKUP_INCLUDE, EVSNotFoundError, verify_content
from nci_si_mcp.release import ReleaseContext

# Fields EVS returns only when the `include` parameter asks for them.
OPTIONAL_FIELDS = frozenset(
    {
        "definitions",
        "synonyms",
        "properties",
        "parents",
        "children",
        "roles",
        "inverseRoles",
        "associations",
        "inverseAssociations",
    }
)


def release(version="26.06e", date="2026-06-29", channel="monthly", terminology="ncit"):
    return ReleaseContext(
        terminology=terminology,
        channel=channel,
        version=version,
        date=date,
        pinned_terminology=f"{terminology}_{version}",
    )


def terminology_row(version="26.06e", date="2026-06-29", latest=True, **tags):
    """A row of EVS's terminology listing; the tags default to the monthly channel."""

    return {
        "terminology": "ncit",
        "version": version,
        "date": date,
        "name": f"NCI Thesaurus {version}",
        "terminologyVersion": f"ncit_{version}",
        "latest": latest,
        "tags": tags or {"monthly": "true"},
    }


def concept(code, name=None, version="26.06e", **fields):
    """An EVS concept payload; pass optional fields such as `children=[...]` by name."""

    payload = {
        "code": code,
        "name": name or f"Concept {code}",
        "terminology": "ncit",
        "version": version,
    }
    payload.update(fields)
    return payload


def terminology_row_matches(row, terminology, latest, tag):
    """Whether a listing row passes the filters a listing request names."""

    return (
        terminology in (None, row["terminology"])
        and (row["latest"] or not latest)
        and (tag is None or row["tags"].get(tag) == "true")
    )


class FakeEVS:
    """In-memory stand-in for EVSClient that records the calls it receives.

    `calls` holds (method, terminology, argument) tuples and `includes` the
    include string of each concept request. Like EVS, a concept request returns
    only the optional fields its include string names, and a batch request
    returns only the known concepts. EVS keeps no order in a batch, so the fake
    answers the known concepts in request order rotated by one: code that pairs
    answers with requests by position fails here. Set `errors[method]` to an exception to make
    that method fail. The terminology listing is `rows` when set, otherwise the one latest
    monthly row of `release`; like EVS it honours the `terminology`, `latest` and `tag` filters,
    and it is read afresh on every call.
    """

    def __init__(self, concepts=(), version="26.06e", descendants=None):
        self.release = release(version)
        self.rows = None
        self.concepts = {item["code"]: item for item in concepts}
        self.descendants = descendants or {}
        self.errors = {}
        self.calls = []
        self.includes = []
        self.max_response_bytes = 1_000_000

    def uri(self, path):
        return f"https://evs.test{path}"

    def _record(self, method, terminology=None, argument=None):
        self.calls.append((method, terminology, argument))
        if method in self.errors:
            raise self.errors[method]

    def _concept(self, code, include):
        wanted = set(include.split(","))
        if "summary" in wanted:
            wanted |= {"definitions", "synonyms", "properties"}
        dropped = OPTIONAL_FIELDS - wanted
        return {key: value for key, value in self.concepts[code].items() if key not in dropped}

    def get_api_version(self):
        self._record("get_api_version")
        return {"version": "test"}

    def get_terminologies(self, terminology=None, *, latest=False, tag=None):
        self._record("get_terminologies", terminology, (latest, tag))
        rows = self.rows
        if rows is None:
            rows = [
                terminology_row(
                    self.release.version, self.release.date, **{self.release.channel: "true"}
                )
            ]
        matching = (terminology_row_matches(row, terminology, latest, tag) for row in rows)
        return [row for row, kept in zip(rows, matching, strict=True) if kept]

    def get_concept(self, code, release, include=LOOKUP_INCLUDE):
        self._record("get_concept", release.pinned_terminology, code)
        self.includes.append(include)
        if code not in self.concepts:
            raise EVSNotFoundError(f"{code} not found")
        raw = self._concept(code, include)
        verify_content([raw], release)
        return raw

    def get_concepts_by_codes(self, codes, release, include=INDEX_INCLUDE):
        codes = list(codes)
        self._record("get_concepts_by_codes", release.pinned_terminology, codes)
        self.includes.append(include)
        known = [
            self._concept(code, include) for code in dict.fromkeys(codes) if code in self.concepts
        ]
        verify_content(known, release)
        return known[1:] + known[:1]

    def get_descendants(self, code, max_level, release):
        self._record("get_descendants", release.pinned_terminology, (code, max_level))
        return [item for item in self.descendants.get(code, []) if item["level"] <= max_level]
