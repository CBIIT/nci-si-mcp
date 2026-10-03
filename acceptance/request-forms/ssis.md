# Upstream request forms, for the Shared SI team

Generated from `acceptance/fixtures/manifest.yaml` by `pdm run acceptance-register`; do not edit by hand.

The upstream requests the acceptance suite's fixtures answer, each with the platform operation it
serves (its `OP-` id in the programme's operation inventory, or the requirement where the
operation is missing) and why
it has this form. They are initial versions, from the published API documentation and live checks
where the operation exists and a draft where it does not, furnished for the EVS, caDSR and Shared SI
teams to refine as needed, each change with the approval of the branch chief or a delegate.

Two kinds of request are answered whatever their form: EVS concept requests, by rules over one
recording per concept (below), and requests whose parameters the service is shown to ignore.

## Shared SI: graph identities, and the query text

The Shared SI Service names no release (S-1, S-2): the NCIt and caDSR graphs each carry an
untyped `dc:date`, in two formats, and only NCIt an `owl:versionInfo`; the identity query below
reads them (A3.7.1). For the SPARQL endpoint the suite prescribes the query text: each query
below is matched with runs of whitespace collapsed, sent as a form-encoded POST (a direct POST
of the query is refused) asking for `application/sparql-results+json`. Its `LIMIT` is the tool's
maximum + 1, so that the answer shows whether more exist. A team may propose another form here,
as for every form. The façade answers HTML unless `Accept: application/json` is sent, and a
missing argument with HTTP 200 (X-15).

## Requests

| Operation | Request | Expected | Made | Rationale | Fixture |
|---|---|---|---|---|---|
| OP-S02 | `GET ssis /si-api/v1/database/graph_names?limit=100` with `Accept: application/json` | 200 | recorded | The graphs the façade names. Neither Thesaurus.owl nor Thesaurus.rdf is among them, though the SPARQL endpoint serves both. | `recorded/ssis/graph-names.json` |
| OP-S02 | `GET ssis /si-api/v1/database/graph_names?limit=100` with no header | 200 | recorded | The same request without Accept: HTTP 200 with an HTML table, though the swagger declares JSON. The fixture naming Accept answers a request that carries it; this one any other (M3.2, X-15). | `recorded/ssis/graph-names-html.json` |
| OP-S02 | `GET ssis /si-api/v1/database/graph_names` with `Accept: application/json` | 200 | recorded | Without its required limit: HTTP 200 with apiResponse type E, "Error executing SPARQL query" (X-15). upstream/masked-error serves this answer to well-formed requests. | `recorded/ssis/graph-names-without-limit.json` |
| OP-S02 | `GET ssis /si-api/v1/data_elements/with_concept_id?graph_name=http://cbiit.nci.nih.gov/caDSR&resource_name=caDSR&dec_pub_id=2226947` with `Accept: application/json` | 200 | recorded | The data elements of one data element concept, Person Sex (2226947, the concept of 2200604). The façade's nearest operation to a concept's data elements is keyed by the DEC's public id, not by a concept code. | `recorded/ssis/data-elements-of-dec-2226947.json` |
| OP-S02 | `GET ssis /si-api/v1/data_elements/with_concept_id?graph_name=http://cbiit.nci.nih.gov/caDSR&resource_name=caDSR` with `Accept: application/json` | 200 | recorded | Without its required dec_pub_id: HTTP 200 with apiResponse type I, "No data found", a missing argument answered as an empty result (X-15). | `recorded/ssis/data-elements-without-dec.json` |
| OP-S02 | `GET ssis /si-api/v1/data_elements/with_specific_object_class?graph_name=http://cbiit.nci.nih.gov/caDSR&resource_name=caDSR&concept_id=C25190` with `Accept: application/json` | 200 | recorded | The data elements whose object class is Person (C25190): exactly 1,000 rows with apiResponse type S, where the caDSR graph holds 2,088. The cap is silent (A5.4). | `recorded/ssis/data-elements-of-object-class-c25190.json` |
| OP-S01 | `POST ssis-sparql /sparql` with `Accept: application/sparql-results+json`, form `query` (below) | 200 | recorded | The release identity of the NCIt and caDSR graphs: owl:versionInfo and dc:date, two untyped dates in two formats ("September 28, 2026" and "2026-07-01"), the stand-in for OP-S03 (A3.7.1). | `recorded/ssis-sparql/graph-identities.json` |
| OP-S01 | `POST ssis-sparql /sparql` with `Accept: application/sparql-results+json`, form `query` (below) | 200 | recorded | The data elements that use Gender (C17357) as an object class, property or permissible value concept, main or minor (find_data_elements_for_concept). | `recorded/ssis-sparql/data-elements-c17357.json` |
| OP-S01 | `POST ssis-sparql /sparql` with `Accept: application/sparql-results+json`, form `query` (below) | 200 | recorded | The same, over C17357 and its descendants in NCIt's hierarchy (A5.4). | `recorded/ssis-sparql/data-elements-c17357-descendants.json` |
| OP-S01 | `POST ssis-sparql /sparql` with `Accept: application/sparql-results+json`, form `query` (below) | 200 | recorded | The same over Disease or Disorder (C2991) and its descendants: 1,001 rows, so more exist than the tool's maximum (A5.4). | `recorded/ssis-sparql/data-elements-c2991-descendants.json` |
| OP-S01 | `POST ssis-sparql /sparql` with `Accept: application/sparql-results+json`, form `query` (below) | 200 | recorded | The concept the permissible value "Male" of 2200604 stands for: C20197 (get_concept_for_permissible_value). | `recorded/ssis-sparql/concept-of-2200604-male.json` |
| OP-S04 | `POST ssis-sparql /sparql` with `Accept: application/sparql-results+json`, form `query` (below) | 200 | recorded | The permissible values that stand for Male (C20197), with their data elements: the reverse lookup no façade operation offers. | `recorded/ssis-sparql/values-of-c20197.json` |
| OP-S01 | `POST ssis-sparql /sparql` with `Accept: application/sparql-results+json`, form `query` (below) | 403 | recorded | The inspection layer's refusal of a query it does not pass: HTTP 403 with an HTML body. ssis/query-rejected serves it to every query. | `recorded/ssis-sparql/query-refused.json` |

## Query texts

### `recorded/ssis-sparql/graph-identities.json`: `query`

```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX mdr: <http://www.iso.org/11179/MDR#>
PREFIX cadsr: <http://cbiit.nci.nih.gov/caDSR#>
PREFIX ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
SELECT ?graph ?version ?date
WHERE {
  VALUES ?graph { <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.rdf> <http://cbiit.nci.nih.gov/caDSR> }
  GRAPH ?graph {
    ?ontology dc:date ?date .
    OPTIONAL { ?ontology owl:versionInfo ?version }
  }
}
```

### `recorded/ssis-sparql/data-elements-c17357.json`: `query`

```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX mdr: <http://www.iso.org/11179/MDR#>
PREFIX cadsr: <http://cbiit.nci.nih.gov/caDSR#>
PREFIX ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
SELECT DISTINCT ?id ?version ?name
WHERE {
  VALUES ?concept { ncit:C17357 }
  GRAPH <http://cbiit.nci.nih.gov/caDSR> {
    VALUES ?role { cadsr:main_concept cadsr:minor_concept }
    ?node ?role ?concept .
    { VALUES ?part { mdr:Object_Class mdr:Property } ?element ?part ?node . }
    UNION
    { ?value cadsr:has_concept ?node . ?element mdr:permitted_value ?value . }
    ?element cadsr:publicId ?id ;
      mdr:version ?version ;
      rdfs:label ?name .
  }
}
ORDER BY ?id ?version
LIMIT 1001
```

### `recorded/ssis-sparql/data-elements-c17357-descendants.json`: `query`

```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX mdr: <http://www.iso.org/11179/MDR#>
PREFIX cadsr: <http://cbiit.nci.nih.gov/caDSR#>
PREFIX ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
SELECT DISTINCT ?id ?version ?name
WHERE {
  GRAPH <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.rdf> {
    ?concept rdfs:subClassOf* ncit:C17357 .
  }
  GRAPH <http://cbiit.nci.nih.gov/caDSR> {
    VALUES ?role { cadsr:main_concept cadsr:minor_concept }
    ?node ?role ?concept .
    { VALUES ?part { mdr:Object_Class mdr:Property } ?element ?part ?node . }
    UNION
    { ?value cadsr:has_concept ?node . ?element mdr:permitted_value ?value . }
    ?element cadsr:publicId ?id ;
      mdr:version ?version ;
      rdfs:label ?name .
  }
}
ORDER BY ?id ?version
LIMIT 1001
```

### `recorded/ssis-sparql/data-elements-c2991-descendants.json`: `query`

```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX mdr: <http://www.iso.org/11179/MDR#>
PREFIX cadsr: <http://cbiit.nci.nih.gov/caDSR#>
PREFIX ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
SELECT DISTINCT ?id ?version ?name
WHERE {
  GRAPH <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.rdf> {
    ?concept rdfs:subClassOf* ncit:C2991 .
  }
  GRAPH <http://cbiit.nci.nih.gov/caDSR> {
    VALUES ?role { cadsr:main_concept cadsr:minor_concept }
    ?node ?role ?concept .
    { VALUES ?part { mdr:Object_Class mdr:Property } ?element ?part ?node . }
    UNION
    { ?value cadsr:has_concept ?node . ?element mdr:permitted_value ?value . }
    ?element cadsr:publicId ?id ;
      mdr:version ?version ;
      rdfs:label ?name .
  }
}
ORDER BY ?id ?version
LIMIT 1001
```

### `recorded/ssis-sparql/concept-of-2200604-male.json`: `query`

```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX mdr: <http://www.iso.org/11179/MDR#>
PREFIX cadsr: <http://cbiit.nci.nih.gov/caDSR#>
PREFIX ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
SELECT ?concept ?role
WHERE {
  GRAPH <http://cbiit.nci.nih.gov/caDSR> {
    VALUES ?role { cadsr:main_concept cadsr:minor_concept }
    ?element cadsr:publicId "2200604" ;
      mdr:permitted_value ?pv .
    ?pv mdr:value "Male" ;
      cadsr:has_concept ?node .
    ?node ?role ?concept .
  }
}
```

### `recorded/ssis-sparql/values-of-c20197.json`: `query`

```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX mdr: <http://www.iso.org/11179/MDR#>
PREFIX cadsr: <http://cbiit.nci.nih.gov/caDSR#>
PREFIX ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
SELECT ?id ?version ?value
WHERE {
  GRAPH <http://cbiit.nci.nih.gov/caDSR> {
    VALUES ?role { cadsr:main_concept cadsr:minor_concept }
    ?node ?role ncit:C20197 .
    ?pv cadsr:has_concept ?node ;
      mdr:value ?value .
    ?element mdr:permitted_value ?pv ;
      cadsr:publicId ?id ;
      mdr:version ?version .
  }
}
ORDER BY ?id ?version ?value
LIMIT 1001
```

### `recorded/ssis-sparql/query-refused.json`: `query`

```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX mdr: <http://www.iso.org/11179/MDR#>
PREFIX cadsr: <http://cbiit.nci.nih.gov/caDSR#>
PREFIX ncit: <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#>
SELECT ?concept
WHERE {
  GRAPH <http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.rdf> {
    ?concept rdfs:subClassOf ?parent
      OPTION (TRANSITIVE, t_distinct, t_in(?concept), t_out(?parent), t_max(3)) .
    FILTER (?parent = ncit:C17357)
  }
}
```

## Scenarios

Each scenario provokes one case; its fixtures answer before the ordinary
ones while a test selects it. A recorded fixture is what the service answers today. A crafted one
stands in for a case the service does not produce on demand, under the requirement it names, and
answers the ordinary forms above.

### `upstream/unavailable`

Every upstream request, whatever its surface and path, gets a closed connection, then 503, then no answer within the timeout. Crafted, 7 fixtures, for A2.5, A5.3: bounded retries, counted, then a structured error.

### `upstream/masked-error`

Every request to the Shared SI façade and to the caDSR API is answered with HTTP 200 and an apiResponse of type E, the failure each sends inside a success. Crafted, 2 fixtures, for X-15: an error envelope in an HTTP 200 is an upstream error.

### `ssis/query-rejected`

Every SPARQL query is refused by the Shared SI Service's inspection layer: HTTP 403 with an HTML body. Crafted, 1 fixture, for X-15: HTML where JSON was asked for is an upstream error.
