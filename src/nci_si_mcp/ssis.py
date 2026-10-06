"""Shared SI contract client; content release comparison belongs to #38's tools.

Only validated identifiers enter fixed SPARQL templates. The extra row is retained so
the caller can distinguish a complete answer from a cut; it does not establish a total.
Graph dates and versions are returned as supplied, never replaced by another surface.
"""

from __future__ import annotations

from datetime import date, datetime
from functools import partial
from http import HTTPStatus
from typing import Any, NotRequired, TypedDict

from .config import Settings
from .errors import InputValidationError, PlatformError
from .http_client import HttpClient, UpstreamRejectedError, UpstreamUnavailableError
from .validation import bounded, validate_identifier

NCIT_GRAPH = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.rdf"
CADSR_GRAPH = "http://cbiit.nci.nih.gov/caDSR"
_GRAPHS = (NCIT_GRAPH, CADSR_GRAPH)
MAXIMUM = 1000
_PREFIXES = """PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX mdr: <http://www.iso.org/11179/MDR#>
PREFIX cadsr: <http://cbiit.nci.nih.gov/caDSR#>
PREFIX ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
"""
_IDENTITIES = f"""SELECT ?graph ?version ?date
WHERE {{
  VALUES ?graph {{ <{NCIT_GRAPH}> <{CADSR_GRAPH}> }}
  GRAPH ?graph {{
    ?ontology dc:date ?date .
    OPTIONAL {{ ?ontology owl:versionInfo ?version }}
  }}
}}
LIMIT {len(_GRAPHS) + 1}"""
_ELEMENTS = """SELECT ?id ?version (MIN(?label) AS ?name)
WHERE {
  CONCEPT_CLAUSE
  GRAPH <CADSR_GRAPH> {
    VALUES ?role { cadsr:main_concept cadsr:minor_concept }
    ?node ?role ?concept .
    { VALUES ?part { mdr:Object_Class mdr:Property } ?element ?part ?node . }
    UNION
    { ?value cadsr:has_concept ?node . ?element mdr:permitted_value ?value . }
    ?element cadsr:publicId ?id ;
      mdr:version ?version ;
      rdfs:label ?label .
  }
}
GROUP BY ?id ?version
ORDER BY ?id ?version
""".replace("CADSR_GRAPH", CADSR_GRAPH)
_VALUES = """SELECT DISTINCT ?id ?version ?value ?concept
WHERE {
  CONCEPT_CLAUSE
  GRAPH <CADSR_GRAPH> {
    VALUES ?role { cadsr:main_concept cadsr:minor_concept }
    ?node ?role ?concept .
    ?pv cadsr:has_concept ?node ;
      mdr:value ?value .
    ?element mdr:permitted_value ?pv ;
      cadsr:publicId ?id ;
      mdr:version ?version .
  }
}
ORDER BY ?id ?version ?value ?concept
""".replace("CADSR_GRAPH", CADSR_GRAPH)


class GraphIdentity(TypedDict):
    """Actual graph metadata, including its original date spelling and optional version."""

    graph: str
    date: str
    version: NotRequired[str]


def _malformed() -> PlatformError:
    return PlatformError(
        "upstream_unavailable",
        "Shared SI returned incomplete or malformed results. Retry later.",
        surface="ssis",
    )


def _list(payload: Any, key: str, item_type: type) -> list[Any]:
    value = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(value, list) or any(not isinstance(row, item_type) for row in value):
        raise _malformed()
    return value


def _term(term: Any) -> str:
    if not isinstance(term, dict) or term.get("type") not in ("uri", "literal", "typed-literal"):
        raise _malformed()
    value = term.get("value")
    if not isinstance(value, str):
        raise _malformed()
    return value


def _binding(row: dict[str, Any], required: tuple[str, ...]) -> dict[str, str]:
    if not all(key in row for key in required):
        raise _malformed()
    return {key: _term(value) for key, value in row.items()}


def _concept_clause(code: str, expand: bool) -> str:
    validate_identifier(code, r"C[0-9]+", "conceptCode")
    if type(expand) is not bool:
        raise InputValidationError("expandDescendants must be boolean", "expandDescendants")
    if expand:
        return f"GRAPH <{NCIT_GRAPH}> {{ ?concept rdfs:subClassOf* ncit:{code} . }}"
    return f"VALUES ?concept {{ ncit:{code} }}"


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        try:
            datetime.strptime(value, "%B %d, %Y")
        except ValueError:
            return False
    return True


def _identity(row: dict[str, str]) -> GraphIdentity:
    if row["graph"] not in _GRAPHS or not _valid_date(row["date"]):
        raise _malformed()
    identity: GraphIdentity = {"graph": row["graph"], "date": row["date"]}
    if "version" in row:
        if not row["version"].strip():
            raise _malformed()
        identity["version"] = row["version"]
    return identity


class SSISClient:
    """Minimal façade and bounded SPARQL operations used by the cross-domain tools.

    The graph version is input to #38's comparison with the effective release. This
    client neither compares it with EVS nor fabricates a registry release from a date.
    """

    def __init__(self, settings: Settings) -> None:
        transport = partial(
            HttpClient,
            timeout_seconds=settings.timeout_seconds,
            max_attempts=3,
            retry_backoff_seconds=0.25,
            max_response_bytes=10 * 1024 * 1024,
            size_bound="ssis_response_bytes",
        )
        self.http = transport(settings.ssis_facade_url, label="SSIS")
        self.sparql_http = transport(settings.ssis_sparql_url, label="SSIS_SPARQL")

    def get_graph_names(self, limit: int = 100) -> list[str]:
        """Swagger requires limit even though the endpoint otherwise returns HTTP 200."""
        limit = bounded(limit, MAXIMUM, "limit")
        payload = self.http.get_json("/si-api/v1/database/graph_names", {"limit": limit})
        return _list(payload, "graph", str)

    def get_data_elements_for_dec(self, dec_public_id: str) -> list[dict[str, Any]]:
        """with_concept_id takes a DEC public id; its recorded upstream cap is 100."""
        validate_identifier(dec_public_id, r"[1-9][0-9]*", "dec_pub_id")
        payload = self.http.get_json(
            "/si-api/v1/data_elements/with_concept_id",
            {"graph_name": CADSR_GRAPH, "resource_name": "caDSR", "dec_pub_id": dec_public_id},
        )
        rows = _list(payload, "results", dict)
        for row in rows:
            if any(
                not isinstance(row.get(key), str)
                for key in ("entity", "identifier", "preferred_term")
            ):
                raise _malformed()
        return rows

    def _query(self, query: str, required: tuple[str, ...], row_limit: int) -> list[dict[str, str]]:
        try:
            payload = self.sparql_http.post_form(
                "/sparql", {"query": _PREFIXES + query}, accept="application/sparql-results+json"
            )
        except UpstreamRejectedError as exc:
            if exc.details.get("status") != HTTPStatus.FORBIDDEN:
                raise
            raise UpstreamUnavailableError(
                "Shared SI rejected the query. Check the supported query forms or retry later.",
                reason="query rejected by inspection layer",
                **exc.details,
            ) from None
        results = payload.get("results") if isinstance(payload, dict) else None
        rows = _list(results, "bindings", dict)
        if len(rows) > row_limit:
            raise _malformed()
        return [_binding(row, required) for row in rows]

    def get_graph_identities(self) -> list[GraphIdentity]:
        """Return unchanged metadata; #38 normalizes dates and verifies the NCIt release."""
        rows = self._query(_IDENTITIES, ("graph", "date"), len(_GRAPHS) + 1)
        identities = [_identity(row) for row in rows]
        if identities and sorted(row["graph"] for row in identities) != sorted(_GRAPHS):
            raise _malformed()
        return identities

    def find_data_elements(
        self, concept_code: str, *, expand_descendants: bool = False, maximum: int = MAXIMUM
    ) -> list[dict[str, str]]:
        maximum = bounded(maximum, MAXIMUM, "maximum")
        query = _ELEMENTS.replace(
            "CONCEPT_CLAUSE", _concept_clause(concept_code, expand_descendants)
        )
        return self._query(query + f"LIMIT {maximum + 1}", ("id", "version", "name"), maximum + 1)

    def find_permissible_values(
        self, concept_code: str, *, expand_descendants: bool = False, maximum: int = MAXIMUM
    ) -> list[dict[str, str]]:
        maximum = bounded(maximum, MAXIMUM, "maximum")
        query = _VALUES.replace("CONCEPT_CLAUSE", _concept_clause(concept_code, expand_descendants))
        return self._query(
            query + f"LIMIT {maximum + 1}", ("id", "version", "value", "concept"), maximum + 1
        )

    def get_permissible_values(
        self, public_id: str, *, maximum: int = MAXIMUM
    ) -> list[dict[str, str]]:
        """Read all versions for the identifier; the tool selects version/value locally.

        A SPARQL 1.1 plain quoted string is an xsd:string literal. No caller value or
        version text is interpolated; the recorded query retrieves both as variables.
        """
        validate_identifier(public_id, r"[1-9][0-9]*", "publicId")
        maximum = bounded(maximum, MAXIMUM, "maximum")
        query = f"""SELECT ?version ?value ?concept ?role
WHERE {{
  GRAPH <{CADSR_GRAPH}> {{
    VALUES ?role {{ cadsr:main_concept cadsr:minor_concept }}
    ?element cadsr:publicId "{public_id}" ;
      mdr:version ?version ;
      mdr:permitted_value ?pv .
    ?pv mdr:value ?value ;
      cadsr:has_concept ?node .
    ?node ?role ?concept .
  }}
}}
ORDER BY ?version ?value ?role ?concept
LIMIT {maximum + 1}"""
        return self._query(query, ("version", "value", "concept", "role"), maximum + 1)
