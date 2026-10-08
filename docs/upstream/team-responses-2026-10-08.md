# EVS and caDSR team responses

Recorded 8 October 2026 from the project owner's relay of the EVS and caDSR teams'
answers. Credit for the explanations and upstream remediation belongs to those teams.
These are team-reported facts and plans, not new live measurements or independently
verified closures. Historical recordings remain unchanged. The
[requirements packages](README.md) retain the outstanding capability requests.

## Decisions and implications

| Topic | Team response | Implication for this MCP |
|---|---|---|
| CDE Match 2.0 contract host | The declared development host was a bug, logged as Jira 9609 and reported fixed. | Treat the host defect as **reported fixed, awaiting contract recheck**. Do not derive a production URL by replacing `-dev`; verify the published contract and intended environment before changing client configuration. |
| `/DataElements/Concept` latency | The warm improvement reflects Oracle caching. C17357 (Gender) is widely used; the tested response contained about 126 CDEs across all statuses and about 885 KB of full metadata. CDE 3111444 alone contained 1,215 elements. Performance investigation is tracked as Jira 9611. | The observed 19–20 s cold / 9.7 s warm times are a workload observation, not a general service baseline or limit. Benchmark representative concepts and payloads before setting expectations. |
| Shared SI missing parameter | Omitting required `dec_pub_id` returned HTTP 200, `apiResponse.type: I`, and “No data found”. The team classifies this as informational, not a masked error. A possible HTTP 400 response is a backlog design improvement, not a confirmed defect. | Correct the earlier characterization. Continue validating required input before requests; do not classify all informational envelopes or empty results as failures. |
| CDE AI Engine | Its vector database is private and will not be exposed publicly. The supported access is the published CDE AI Engine API, currently on Stage and expected in production at the end of October 2026. It is a purpose-built CDE retrieval/reranking pipeline with a customized SapBERT model, designed and evaluated by the caDSR team. | Assess that API for future caDSR AI matching. No direct vector-database integration or duplicate local caDSR index follows from this response. The expected date does not establish production availability. |
| caDSR II text search | The IBM webMethods application uses Elasticsearch, built on Apache Lucene. | Record the technology, but do not infer a public Elasticsearch endpoint or supported MCP search contract from the web application's internals. |
| EVS search | The `/search` `type` field controls lexical handling of `term` across model names. EVS also supports structured retrieval and ontology/relationship-aware retrieval, including SPARQL. Roles/associations **searches** were temporarily removed for rework; SPARQL was added. | Distinguish lexical search, graph semantics and embedding similarity. This does not establish an embedding-based ranked API equivalent to our local semantic/hybrid search, or removal of all relationship retrieval. |
| EVS FHIR R5 contract | The team reports the known `/fhir/r5/api-docs` fault fixed in release 2.5.0 and the endpoint working. The team tentatively attributes the break to 2.4. | Treat it as **reported fixed, awaiting live recheck** at the [published R5 contract](https://api-evsrest.nci.nih.gov/fhir/r5/api-docs). Preserve the old recording as historical evidence. |

## Correct Shared SI interpretation

`/data_elements/with_concept_id` takes a **Data Element Concept (DEC) public ID**,
not an arbitrary NCIt concept code. The team suggests
`/data_elements/with_specific_object_class` as a possible alternative for an object-class
use case. It is not a general replacement for finding concepts used as properties or
permissible values, or for descendant expansion. Our client already documents and validates
the DEC identity; the cross-domain tools use bounded SPARQL for their broader contract.

The team suggests existing Shared SI graph queries may respond in under a second, depending
on the use case. That is not a measured equivalent of the full REST metadata request.
Shared SI is in maintenance mode, with no funding for new API development; the response also
notes unfunded maintenance. Plan around existing, verified capabilities, not an assumed
new upstream endpoint.

## Focused follow-up

1. **Recheck reported fixes:** retrieve the current CDE Match contract and EVS FHIR R5
   contract; record the date, host/version and result. Confirm an actual supported operation
   before changing adapters. No endpoint or fixture is changed solely from this note.
2. **Give the performance team a concrete workload:** describe whether the caller needs CDE
   identities, headers, full metadata or concept usage. Compare a common and a rare concept,
   status filters where supported, payload bytes, result counts, cold/warm latency and
   release/registry identities. Our recorded OP-C09 request includes `headerOnly=true`, while
   the response explanation describes full metadata: reconcile the exact request and payload
   before treating them as the same benchmark. Share that evidence against Jira 9611.
3. **Evaluate the CDE AI Engine contract when available:** confirm Stage/production URLs,
   access requirements, input and output schemas, supported model/threshold controls,
   pagination/caps, failure semantics, registry provenance, latency and usage constraints.
   Compare retrieval and reranking with the project's representative CDE matching cases.
   Access remains through the team's API; no data is invented when access is unavailable.
4. **Keep EVS capabilities distinct:** evaluate published SPARQL and structured retrieval
   against graph questions; evaluate any future embedding retrieval against the existing
   full-corpus quality, release-pinning and latency criteria before retiring the local index.
   Earlier probes of rejected `type=semantic` and undocumented `type=exact` remain historical
   observations, not supported API contracts.

These follow-ups inform future upstream integration work. They do not expand Phase 7's
portal scope, waive acceptance requirements or change `spec/` behavior.
