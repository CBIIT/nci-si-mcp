# Specification of the required MCP tools and their behavior

Generated from `spec/` by `pdm run spec-render`; do not edit by hand.

This is the specification of the required MCP tools and their behavior furnished with the
Prototype Baseline Package. The Statements of Work frame and bound its scope; within that scope
it is the source of record for what the tools do. Its source is data: the conventions
(`spec/conventions.yaml`), the tools (`spec/tools.yaml`) and the requirements
(`spec/requirements.yaml`). The server implements the tools, and the acceptance suite tests the
requirements, each test citing the requirements it enforces by id.

## 1. Conventions

Binding on every tool.

### A1 · One platform, two modules

| Id | Convention |
|---|---|
| A1.1 | A caller uses both modules in one workflow without translating identifiers, reconciling error formats or reading provenance differently. |
| A1.2 | A concept code is exchanged as the bare code the terminology publishes (C4817), with the terminology in a separate field; never with an embedded prefix, URI expansion or terminology punctuation as its primary form. A URI may be given as an additional field. |
| A1.3 | A code of the other module's domain is returned in the A1.2 form, ready to pass to the other module unchanged. |
| A1.4 | Neither module needs the other to be present to work. |
| A1.5 | Content obtained from the Shared Semantic Infrastructure Service says so in its provenance, with the release identity of both source graphs. |

*Why A1.5.* The Shared SI Service's NCIt and caDSR graphs are refreshed on independent schedules and were two months apart when last checked, so a result joined across them describes two content states.

### A2 · Tool schema conventions

| Id | Convention |
|---|---|
| A2.1 | Tool names are verb-led, lowercase and underscore-separated, and name the object retrieved. |
| A2.2 | Equivalent operations of the two modules share the tool name pattern and the parameter names. |
| A2.3 | A tool description describes only behaviour the platform implements; it shows no unsupported operator, wildcard, filter syntax or parameter value. |
| A2.4 | No tool description contains placeholder, debug or development text, checked by an automated test of the rendered schema. |
| A2.5 | Errors are structured, from one model that distinguishes at least: invalid request; content not found; release not available; upstream unavailable; result bound exceeded; internal error; and never resemble an empty result. |
| A2.6 | An empty result is distinguishable from a suppressed, truncated or failed one. |
| A2.7 | Where a platform serves an operation through a standard terminology interface (FHIR), it is preferred over a proprietary equivalent, and the choice is documented. |

*Why A2.3.* A model generates calls from a description; an operator the platform does not support produces a rejected call, after which the model improvises, as has been observed.

*Why A2.7.* EVS serves FHIR R4 and R5 ($lookup, $expand, $validate-code, $subsumes, $translate); a schema on standard operations is better specified and survives a platform change. Where they fall short, as for bounded traversal over roles, the native surface is used.

### A3 · Release identity

| Id | Convention |
|---|---|
| A3.1 | Every platform request names the release explicitly; resolving it once at start-up does not count. |
| A3.2 | Unversioned platform access is used only to discover which releases exist, and, where the platform offers no release-pinned form of an operation, to call its unpinned form, verify the release the payload reports and fail closed on a difference (the verified fallback). |
| A3.3 | The release in effect is recorded in the provenance of every result. |
| A3.4 | A cache or index derived from platform content is bound to its release; any mismatch between the release requested, the release the content reports and the release of derived content fails closed with a structured error. |
| A3.5 | Acceptance tests show fail-closed behaviour for each mismatch of A3.4. |
| A3.6.1 | The NCIt release is resolved by tag (monthly), never by the latest flag. |
| A3.6.2 | Monthly and weekly builds are told apart by tag, and the choice is explicit in configuration. |
| A3.6.3 | A release with conflicting or duplicate tags fails closed and is reported in a form fit to send upstream. |
| A3.7.1 | A query of the Shared SI Service reports the release identity of each graph it touched (owl:versionInfo and dc:date of the NCIt graph, dc:date of the caDSR graph) and the interval between them, and warns rather than fails above a configured threshold. |
| A3.8.1 | caDSR publishes no registry release identifier, and none is implied; a data element's version is never presented as the registry's state. |
| A3.8.2 | Until a registry release identifier exists, the most specific provenance the platform gives (a generation timestamp) is surfaced, and registry-level reproducibility is documented as not achievable. |
| A3.8.3 | Every data element is returned with its own version and registration status. |

*Why A3.6.1.* On 10 September 2026 the listing flagged two releases latest at once, the newest weekly and the newest monthly, so a client taking the first such row could request one release and receive another without an error.

### A4 · Provenance and citation

| Id | Convention |
|---|---|
| A4.1 | Every item carries its canonical identifier and, where published, its status as fields of the item, and a camelCase provenance record sufficient to reproduce, audit or discard it: at least release (terminology or registry, identifier and date), source and retrievedAt; the full record is the provenance record below. |
| A4.2 | An item reached by traversal adds depth, relationship (code, name, kind), direction, qualifiers, polarity and evidence. |
| A4.3 | Every provenance field the platform supplies is passed through, including those not listed here. |
| A4.4 | Provenance is attached per item, not per response. |

### A5 · Result bounding, truncation and traversal

| Id | Convention |
|---|---|
| A5.1 | A tool that can return a large result takes caller limits and applies a documented default. |
| A5.2 | Result size, traversal depth and outbound requests per tool call are bounded. |
| A5.3 | The outbound request bound counts retries. |
| A5.4 | A bound reached is reported with which bound and how much was omitted, exactly or as a stated estimate; a flag alone is not enough. |
| A5.5 | Traversal over several relationship kinds does not spend its budget on one kind at the expense of another, and reports truncation per kind; a response missing a whole category of relationship says so. |
| A5.6 | Negative assertions are kept, marked with negative polarity and full provenance, and left out of positive expansion by default, which the caller can override. |
| A5.7 | Negative assertions are identified by relationship code against the release's relationship catalogue, never by the relationship's name. |
| A5.8 | The same information is not retrieved twice within one operation. |

*Why A5.7.* NCIt's exclusion relationships are exactly eight roles, R135 to R142. Matching their names fails open: a renamed label turns an exclusion into the assertion that the disease has the very finding it rules out.

### A6 · Audit context, correlation and telemetry

| Id | Convention |
|---|---|
| A6.0 | The authoritative audit record, rate limits, quotas and authorisation are the platform's; the module supplies what only it knows. |
| A6.1 | Every tool call emits telemetry of timestamp, tool, parameters, release, outbound requests with retries, result size, truncation, response code and elapsed time. |
| A6.2 | A caller's correlation identifier is accepted, recorded on every telemetry entry, passed upstream on every platform request and returned; one is generated where none is supplied. |
| A6.2a | The platform is given the audit context only the module knows (tool, consumer, workflow correlation) through the parameters or headers it defines. |
| A6.3 | Log records share one structured format and field vocabulary across both modules. |
| A6.4 | Telemetry records no more content than needed, and no caller free text where a hash suffices. |
| A6.5 | The module does not rate-limit in place of the platform; it honours 429 and Retry-After with jittered back-off and counts retries against its budget. |

*Why A6.2a.* One question may cross both modules; without a shared correlation identifier the two audit trails cannot be joined.

### A7 · Identifiers, terminologies and scoping

| Id | Convention |
|---|---|
| A7.1 | Terminology and context identifiers come from the platform's metadata at build or start-up, never hard-coded or guessed. |
| A7.2 | A tool lists the terminologies, contexts or classification schemes available, with identifiers and current releases. |
| A7.3 | Results from a terminology with licence conditions carry its attribution. |
| A7.4 | A credentialed operation degrades explicitly when the credential is absent and is never presented as public. |
| A7.5 | A credential or licence key reaches only the platform request that needs it, and appears in no log, error message or result. |

*Why A7.1.* A public EVS MCP server sends the terminology key loinc where EVS expects lnc, so every LOINC call answers 404, and its own tests assert the wrong value.

### A8 · Content status and retired identifiers

| Id | Convention |
|---|---|
| A8.1 | An item's published status (retired, obsolete, provisional, pending approval) is always surfaced. |
| A8.2 | Replacement and history of a retired identifier are exposed, and a retired identifier never resolves silently as current. |
| A8.3 | How search and expansion treat retired content is documented and caller-overridable. |

### A9 · The lean module

| Id | Convention |
|---|---|
| A9.1 | The module is a thin interface over its platform; it returns results unmodified in substance. |
| A9.2 | Without a written amendment naming the capability and its owner, the module keeps no embeddings, vector or search index or persistent copy of platform content, computes no similarity, ranking or score the platform did not return, reconciles, maps or enriches nothing between the two modules' content, and derives no content (the interim NCIt index is that amendment, M4.1). |
| A9.3 | A capability that needs more is a platform dependency, recorded as such. |
| A9.4 | Request-scoped caching and caching bound to a release under A3.4 are allowed. |
| A9.5 | An absent platform capability is shown to the caller, never compensated for silently. |
| A9.6 | Reading from a governed SI service that joins or scores is allowed; the module does not own such capability, and documents the dependency's refresh cadence, service level and what it does when the service is unavailable. |

*Why A9.6.* Capability in the platform reaches every consumer, the repository backends and batch pipelines that never call a module included; inside a module it reaches only that module's callers and makes a second copy of governed content.

### A10 · Joint conformance and acceptance

| Id | Convention |
|---|---|
| A10.1 | A joint conformance test spans both modules and checks A1.2, A3, A4, A5.4 and correlation propagation. |
| A10.2 | Where the two modules are contracted to different parties, responsibility for the joint test is assigned explicitly, and neither party's acceptance depends on the other's delivery schedule. |
| A10.3 | No tool description contains placeholder text or an unsupported operator, parameter or syntax example. |
| A10.4 | Response-time criteria are relative to the platform's capability at delivery: a module that passes a slow platform call through faithfully conforms. |

### A11 · Performance measurement

| Id | Convention |
|---|---|
| A11.1 | A benchmark reports, per representative scenario, release, cache state, result size, outbound calls, error rate and p50/p95 response time. |
| A11.2 | The benchmark reports what the platform supports and what it does not. |
| A11.3 | The scenarios include a workflow across both modules, measured end to end. |

*Why A11.1.* The only caDSR response-time figures in circulation come from one session in November 2025 and cannot be confirmed either way.

### M1 · A static, self-describing tool surface

| Id | Convention |
|---|---|
| M1.1 | Each tool description stands alone, naming its domain, its object and how it pins a release, without reference to another tool. |
| M1.2 | The tool surface does not vary with the terminology, the release pinned or what the platform offers today; a capability not yet available is a tool returning a structured unavailable error, never an absent tool. |
| M1.3 | Each tool's group (evs, cadsr, cross-domain, workflow) is carried in its metadata. |
| M1.4 | Every tool declares the read-only annotations readOnlyHint true, destructiveHint false, idempotentHint true and openWorldHint true. |
| M1.5 | A server serves one profile, which decides which tools tools/list names and nothing else. The profile evs serves the EVS tools, cadsr the caDSR tools, and unified all four groups; the cross-domain and workflow tools need both modules, so only unified has them (A1.4). |

### M2 · Caching hints

| Id | Convention |
|---|---|
| M2.1 | tools/list, resources/list, resources/read and server/discover carry ttlMs and cacheScope. |
| M2.2 | A result's ttlMs follows what it holds: release-pinned content 86,400,000 (long); governed content that no release pins (caDSR content while caDSR publishes no registry release) a short positive ttlMs, at most 3,600,000; a result computed from caller-supplied values (the tools marked computed in tools.yaml) 0; resolve_release, resolve_registry_release and get_release_alignment 0. tools/list is long and public. |
| M2.3 | cacheScope is public for governed content, the resolve tools included, and private for results computed from caller-supplied values. |
| M2.4 | A cursor pins the release it was issued against; presented after that release is superseded, it is a structured error. |
| M2.5 | A tool result carries ttlMs and cacheScope in its _meta. |

*Why M2.5.* The protocol carries ttlMs and cacheScope as fields of the results of the list methods, resources/read and server/discover only (revision 2026-07-28), so a tool result needs a stated place for them.

### M3 · Typed results

| Id | Convention |
|---|---|
| M3.1 | Every tool declares an outputSchema covering its success and its error shape. |
| M3.2 | A platform failure, including one wrapped in an HTTP 200, is a result with isError; a genuine empty result has its provenance and no isError. |
| M3.3 | A result is a JSON object; a list of items is a named field of it, beside the cursor, the truncation and what was left out. |

*Why M3.3.* Protocol revisions before 2026-07-28 define structuredContent as an object, and a bare list leaves no place for nextCursor (M6.1), a bound reached (A5.4) or the identifiers the platform left out.

### M4 · The interim NCIt index

| Id | Convention |
|---|---|
| M4.1 | NCIt semantic and hybrid search are served from a release-bound local index (the amendment A9.2 asks for) until EVS serves them. The index is re-embedded for each approved monthly release, activated and rolled back atomically, and its release is in provenance and in the index-manifest resource, from which resolve_release learns it. |

### M5 · Prompts and resources

| Id | Convention |
|---|---|
| M5.1 | The furnished prompt templates and resources are listed by prompts/list and resources/list with their arguments, and a prompt names only tools present in the profile. |

### M6 · Pagination

| Id | Convention |
|---|---|
| M6.1 | Pagination is cursor-based, cursor in and nextCursor out, with totalKnown where it can be determined. A cursor continues the call it was issued for, presented with the same arguments, compared as applied (an optional argument left out equals its default given) and limit among them; with any argument that differs it is an invalid request. |

### M7 · Correlation on the wire

| Id | Convention |
|---|---|
| M7.1 | A caller passes its correlation identifier as correlationId in the _meta of tools/call. The module sends it upstream on every platform request the call makes, in the X-Correlation-ID header until the platform defines a parameter or header of its own (A6.2a), and returns it in the provenance of each item and in the error record. |

### The provenance record

Every returned item carries one, beside its identifier and status, which are fields of the item (A4.1, A4.4). A result with no items carries one as its own provenance field (M3.2); a result with items does not, the items carrying theirs. Field names are camelCase.

| Field | Content | Rule |
|---|---|---|
| `release` | The release in effect, in one of two forms; the terminology form `{ terminology, identifier, date }`: the terminology and the release the call pinned; the registry form `{ registry, identifier?, date? }`: registry is cadsr; identifier and date are the registry release's, present only where caDSR publishes one (A3.8.1). Content the API serves without one names neither: it is newer than the export (2200604 modified 2026-08-25, the export of 2026-07-01), so the export's date would mislabel it; the item's own version and dateModified identify it, with retrievedAt beside them (A3.8.2) | A3.3 |
| `source` | The surface that supplied the item: one of `evs_rest`, `evs_fhir`, `evs_index`, `cadsr_rest`, `ssis_facade`, `ssis_sparql` | A4.1 |
| `servedBy` | Where the answer came from: one of `live`, `cache`, `index`, `fixture` | A4.1 |
| `retrievedAt` | When it was retrieved, ISO-8601 | A4.1 |
| `sourceUri` | The upstream URL that produced the item | A4.1 |
| `correlationId` | The call's correlation identifier | M7.1 |
| `graphs` | For items served by the Shared SI Service: the identifier and date of each graph touched | A1.5 |
| `attribution` | The licence text of the item's terminology, where the platform's listing of that terminology carries one (EVS: metadata.licenseText) (optional) | A7.3 |
| `upstream` | The fields the platform supplied about the item's origin, under its names and with its values; at least: from EVS REST the item's terminology and version; from EVS FHIR the value set's url and version; from caDSR the item's public id and version, as each API names them (publicId, the Form API's publicID, the CRDC list's CDE Public ID and Version, vmMatch's itemId); from the Shared SI Service the identity and date of each graph | A4.3 |

### The provenance record of an item reached by traversal

The provenance record, with these fields added (A4.2).

| Field | Content | Rule |
|---|---|---|
| `depth` | Steps from the concept the caller asked about; an edge has the depth of the item it reaches | A4.2 |
| `relationship` | The relationship that brought the item in: { code, name, kind }; the code decides polarity | A5.7 |
| `direction` | Whether the assertion points outward from the origin or inward to it | A4.2 |
| `polarity` | Negative exactly when the relationship's code is in its terminology's exclusion set below, positive otherwise, whatever the relationship is named (A5.7); the set is checked against the release's relationship catalogue (the relationship record): one of `positive`, `negative`; exclusion set of ncit: R135, R136, R137, R138, R139, R140, R141, R142 | A5.6 |
| `qualifiers` | Any qualifying detail the platform attaches | A4.2 |
| `evidence` | Supporting evidence, where the platform supplies it | A4.2 |

### The concept record

A concept as a terminology tool returns it: these fields, status where the platform publishes one, and the sections the caller's include selects. Status is the platform's own, unchanged: a status set of the module's would lose what the platform said and differ between terminologies (A9.1).

| Field | Content | Rule |
|---|---|---|
| `code` | The bare code the terminology publishes | A1.2 |
| `terminology` | The terminology the code belongs to, as the platform names it | A1.2 |
| `name` | The preferred name | A4.1 |
| `active` | Whether the platform publishes the concept as active, its boolean unchanged | A8.1 |
| `status` | The platform's own status value, unchanged, where it publishes one (from EVS: DEFAULT, Header_Concept, Retired_Concept, ...) (optional) | A8.1 |
| `provenance` | The provenance record | A4.4 |
| `synonyms` | The platform's synonym entries, unchanged, when include selects them (optional) | get_concept |
| `definitions` | The platform's definition entries, unchanged, when include selects them (optional) | get_concept |
| `properties` | The platform's property entries, unchanged, when include selects them; the semantic-type property among them (optional) | get_concept |
| `semanticType` | The values of the terminology's semantic-type property, when include selects them; the property is named by its code, not its label (NCIt: P106), as A5.7 names roles (optional) | get_concept |

### The node record

A concept reached by traversal, as get_concept_hierarchy and get_concept_neighborhood return it, each concept once, with its status as the concept record carries it (A8.1). A relation list names a neighbour by code and name only, so the tool reads the nodes of the last depth, which it follows no further, for their status: in batches at the minimal include, bounded by the node bound.

| Field | Content | Rule |
|---|---|---|
| `code` | The bare code the terminology publishes | A1.2 |
| `terminology` | The terminology the code belongs to, as the platform names it | A1.2 |
| `name` | The preferred name | A4.1 |
| `active` | Whether the platform publishes the concept as active, its boolean unchanged | A8.1 |
| `status` | The platform's own status value, unchanged, where it publishes one (optional) | A8.1 |
| `provenance` | The traversal record: depth 0 and no relationship for the concept asked about, and for any other the depth and the relationship of an edge that reached it there | A4.2 |

### The edge record

One assertion between two concepts, as get_concept_neighborhood returns it, in the direction the platform states it: a role or association from the concept holding it to its target, a hierarchy link from child to parent.

| Field | Content | Rule |
|---|---|---|
| `sourceCode` | The bare code of the assertion's subject | A1.2 |
| `sourceTerminology` | The terminology of the subject | A1.2 |
| `targetCode` | The bare code of the assertion's object | A1.2 |
| `targetTerminology` | The terminology of the object | A1.2 |
| `provenance` | The traversal record: the depth of the node it reaches, its relationship { code, name, kind }, direction and polarity | A4.2 |

### The subset record

A subset a concept belongs to, as get_concept_subsets returns it: the platform's Concept_In_Subset associations of the concept, read with it, in the platform's order.

| Field | Content | Rule |
|---|---|---|
| `code` | The bare code of the subset concept | A1.2 |
| `terminology` | The terminology the subset belongs to, as the platform names it | A1.2 |
| `name` | The subset's name, as the association names it | A9.1 |
| `provenance` | The provenance record | A4.4 |

### The mapping record

A map the platform carries on a concept, from it to another terminology, as get_concept_mappings returns it: the platform's map unchanged, in the platform's order. Its provenance names the release of the concept it was read from; the target's own version is the map's. A licensed target's attribution is an open question: the platform names the target by a label that matches no terminology of its listing (#42).

| Field | Content | Rule |
|---|---|---|
| `targetCode` | The target's code, as the platform gives it | A9.1 |
| `targetTerminology` | The target terminology, as the platform names it on the map | A9.1 |
| `targetName` | The target's name | A9.1 |
| `targetTermType` | The target's term type, where the platform gives one; absent, never null, where it does not (optional) | A9.1 |
| `targetTerminologyVersion` | The target terminology's version, where the platform gives one; absent, never null, where it does not (optional) | A9.1 |
| `type` | The map's relation (EVS's type, such as Related To or Has Synonym), unchanged | A9.1 |
| `provenance` | The provenance record | A4.4 |

### The replacement record

A concept the platform names as replacing a retired one, as resolve_retired_code returns it.

| Field | Content | Rule |
|---|---|---|
| `code` | The bare code of the replacement | A1.2 |
| `terminology` | The terminology it belongs to, as the platform names it | A1.2 |
| `name` | The replacement's name, as the platform gives it | A9.1 |
| `provenance` | The provenance record | A4.4 |

### The relationship record

A role or association of a release's relationship catalogue, as list_relationships returns it. The tool, and get_concept_neighborhood, fail closed with internal_error for a release whose catalogue lacks a code of the terminology's exclusion set, naming the codes in details. The check catches a set code the release no longer has, not a new exclusion relationship the set does not know: that needs the catalogue to mark polarity, which is the platform's to add.

| Field | Content | Rule |
|---|---|---|
| `code` | The relationship's code | A5.7 |
| `terminology` | The terminology whose catalogue it is, as the platform names it | A1.2 |
| `name` | Its name in the catalogue, unchanged | A9.1 |
| `kind` | Whether it is a role or an association: one of `role`, `association` | A4.2 |
| `polarity` | As in the traversal record, by code: one of `positive`, `negative` | A5.7 |
| `provenance` | The provenance record | A4.4 |

### The search result record

One result of search_concepts, in the order the source ranked it: the module ranks and scores nothing itself (A9.2), except through the interim index (M4.1).

| Field | Content | Rule |
|---|---|---|
| `concept` | The concept record, with no section | concept |
| `score` | The score the source returned, unchanged: the interim index's (M4.1); absent where the source returns none (EVS) (optional) | A9.2 |
| `matchedOn` | Where the match was found, as the source says it: from EVS its highlight, unchanged (the matched text, the query's terms marked); from the interim index the field matched, name, synonym or definition. Its form follows the mode the caller asked for. Absent where the source says nothing (EVS typeahead), never null (optional) | A9.2 |

### The value set member record

A member of a value set, as expand_value_set returns it. count and offset select a page of the members in the platform's order, and total counts every member activeOnly keeps, so a page is no truncation: truncation reports a bound of the tool's own (A5.2).

| Field | Content | Rule |
|---|---|---|
| `code` | The bare code the terminology publishes | A1.2 |
| `terminology` | The terminology the code belongs to, as the platform names it | A1.2 |
| `name` | The platform's display text for the member, unchanged | A9.1 |
| `inactive` | true where the expansion marks the member inactive (FHIR contains.inactive: no longer active, retired members among them), and absent, never false or null, where it does not; activeOnly leaves such members out (A8.3) (optional) | A8.1 |
| `provenance` | The provenance record | A4.4 |

### The terminology record

A terminology the platform serves, as list_terminologies returns it.

| Field | Content | Rule |
|---|---|---|
| `terminology` | The terminology's identifier, as the platform's metadata names it | A7.1 |
| `release` | Its current release identifier | A7.2 |
| `provenance` | The provenance record | A4.4 |

### The registry release record

The registry's content state, as resolve_registry_release returns it. caDSR publishes no registry release today, so the export's date stands for it and no identifier is invented (A3.8).

| Field | Content | Rule |
|---|---|---|
| `published` | Whether caDSR publishes a registry release; false today | A3.8.1 |
| `identifier` | The registry release, present only where caDSR publishes one (optional) | A3.8.1 |
| `generatedAt` | When the content was generated, ISO-8601: the release's own date where one is published, otherwise the export's date as the export folder gives it (A3.8.2) | A3.8.2 |
| `sourceDistribution` | The distribution generatedAt is read from (today releasedCDEsXML-OD.zip) | A3.8.2 |

### The data element record

A data element as a caDSR tool returns it: these fields, the platform's own, and the sections the caller's include selects; no include returns these fields alone. Names follow the caDSR API's (DataElement), statuses unchanged (A9.1).

| Field | Content | Rule |
|---|---|---|
| `publicId` | The data element's public id | A1.2 |
| `version` | The data element's own version, never the registry's state | A3.8.3 |
| `longName` | Its long name | A4.1 |
| `context` | The context that owns it | A3.8.3 |
| `workflowStatus` | The platform's workflow status, unchanged (RELEASED, RETIRED ARCHIVED, ...) | A3.8.3 |
| `registrationStatus` | The platform's registration status, unchanged (Standard, Application, ...) | A3.8.3 |
| `dateCreated` | When the platform created it, its own value unchanged | A3.8.2 |
| `dateModified` | When the platform last modified it, its own value unchanged | A3.8.2 |
| `provenance` | The provenance record, its release in the registry form | A4.4 |
| `permissibleValues` | The permissible value records of its value domain, when include selects them (optional) | get_data_element |
| `valueDomain` | The platform's value domain fields, unchanged but for its permissible values, which permissibleValues holds, when include selects it (optional) | get_data_element |
| `conceptAssociations` | The concepts of its data element concept, each { conceptCode, longName, role }, role objectClass or property, as the platform's ObjectClass and Property give them, when include selects them (optional) | get_data_element |
| `alternateNames` | The platform's alternate name entries, unchanged, when include selects them (optional) | get_data_element |
| `classificationSchemes` | The classification scheme records it is classified in, when include selects them (optional) | get_data_element |

### The permissible value record

A permissible value of a value domain, with the value meaning it stands for.

| Field | Content | Rule |
|---|---|---|
| `publicId` | The identifier caDSR REST publishes for it; no platform operation retrieves a value by it yet (OP-C10) | A1.2 |
| `value` | The value as stored | A9.1 |
| `valueMeaning` | Its value meaning: { publicId, version, longName, concepts[] }, each concept { conceptCode, longName, primary } as the platform gives it | A9.1 |
| `provenance` | The provenance record, its release in the registry form | A4.4 |

### The classification scheme record

A classification scheme as an object with its nested items (A7.2). The platform lists none on their own today (OP-C13): a data element carries the schemes it is classified in.

| Field | Content | Rule |
|---|---|---|
| `publicId` | The scheme's public id | A1.2 |
| `version` | The scheme's version | A3.8.3 |
| `longName` | Its long name | A4.1 |
| `context` | The context that owns it | A7.2 |
| `items` | Its classification scheme items, each { publicId, version, longName } | A7.2 |
| `provenance` | The provenance record, its release in the registry form | A4.4 |

### The context record

A context of the registry, as list_contexts returns it. The platform publishes a context by its name alone, so the name is its identifier (A7.2).

| Field | Content | Rule |
|---|---|---|
| `name` | The context's name, its identifier | A7.2 |
| `provenance` | The provenance record, its release in the registry form | A4.4 |

### The data element match record

One match of match_data_elements, in the platform's order.

| Field | Content | Rule |
|---|---|---|
| `dataElement` | The data element record, with no section | data_element |
| `score` | The platform's score, unchanged | A9.2 |
| `rule` | The rule the platform says matched (CDE Match's ruleDescription), unchanged | A9.2 |
| `matchedText` | The text the platform says matched, unchanged | A9.2 |

### The value meaning match record

One match of match_value_meanings, in the platform's order.

| Field | Content | Rule |
|---|---|---|
| `item` | The item matched, as vmMatch gives it: { itemType, publicId, version, name, concept?, evsSource?, context, workflowStatus, registrationStatus, provenance }. itemType is Concept or ValueMeaning, publicId that item's caDSR public id (itemId), name its matchedName; concept is the code it stands for, in the code system evsSource names (NCI_CONCEPT_CODE, SNOMED-CT_CODE, ...), each absent where the platform gives none | A9.1 |
| `rule` | The rule the platform says matched (vmMatch's ruleDescription), unchanged | A9.2 |
| `score` | The platform's score, where it gives one; absent, never null, where it does not (vmMatch gives none) (optional) | A9.2 |
| `crosswalk` | The crosswalk the platform gives, { code, description } from crosswalkCode and crosswalkDescription; absent where it gives none, the platform's NA meaning no crosswalk (optional) | match_value_meanings |

### The form record

A form as get_form returns it, its status the platform's own (A8.1).

| Field | Content | Rule |
|---|---|---|
| `publicId` | The form's public id (the Form API's publicID) | A1.2 |
| `version` | The form's version | A3.8.3 |
| `longName` | Its long name | A4.1 |
| `context` | The context that owns it | A3.8.3 |
| `workflowStatus` | The platform's workflow status, unchanged (RETIRED ARCHIVED is surfaced, not hidden) | A8.1 |
| `registrationStatus` | The platform's registration status, unchanged | A3.8.3 |
| `dateCreated` | When the platform created it, its own value unchanged | A3.8.2 |
| `dateModified` | When the platform last modified it, its own value unchanged | A3.8.2 |
| `provenance` | The provenance record, its release in the registry form | A4.4 |
| `modules` | Its modules in the platform's order, each with its questions, the platform's entries unchanged; absent when includeModules is false (optional) | get_form |

### The code map record

The values one data element of the CRDC crosswalk binds to concepts, with the contexts and commons that use it, as get_code_map returns it. The values do not vary by user.

| Field | Content | Rule |
|---|---|---|
| `dataElement` | { publicId, version } of the data element | A1.2 |
| `crdcName` | Its CRDC name, as the platform gives it | A9.1 |
| `usedBy` | The contexts and commons that use it, the platform's Used By names split at their commas (CRDC, GDC, Pediatric Cancer, CTEP, ...); empty where it names none | get_code_map |
| `valueLevelBinding` | Whether the data element binds values to concepts, which it does where the platform gives its permissible values; false says so rather than returning no values | get_code_map |
| `coverage` | The number of its values bound to a concept code | get_code_map |
| `values` | Its values, each { value, conceptCode? }, as the platform gives them: conceptCode colon-joined where it names several (C15388:C20821), absent where it names none | A9.1 |
| `provenance` | The provenance record, its release in the registry form | A4.4 |

### The truncation record

The truncation field of a result whose tool bounds it. When nothing was truncated it holds occurred false and no other field. When a bound was reached it holds occurred, bound, limit, reached, omitted and exact, and perKind as well where a traversal spans several relationship kinds. A page that a cursor or an offset continues is not a truncation (M6.1).

| Field | Content | Rule |
|---|---|---|
| `occurred` | Whether a bound was reached | A5.4 |
| `bound` | Which bound was reached: one of `results`, `depth`, `nodes`, `edges`, `kind_budget`, `requests`, `upstream_cap` (optional) | A5.4 |
| `limit` | The value of that bound (optional) | A5.1 |
| `reached` | The amount counted against the bound when it stopped (optional) | A5.4 |
| `omitted` | How much was left out, always a number (optional) | A5.4 |
| `exact` | False when omitted is a lower bound or an estimate (optional) | A5.4 |
| `perKind` | For a traversal over several relationship kinds: a map from each kind to its own truncation record; occurred is true when any kind's budget was reached (optional) | A5.5 |

### The error record

A failed call returns { error } as its structuredContent, with isError set (M3.2); it never resembles an empty result (A2.5).

| Field | Content | Rule |
|---|---|---|
| `code` | The class of the failure: those A2.5 names, a release mismatch (A3.4), a timeout, a capability not yet available (M1.2) and a cursor whose release is superseded (M2.4). release_not_available also covers a channel whose current release cannot be named, as when one channel's query names two releases (A3.6.3): one of `invalid_request`, `not_found`, `release_not_available`, `release_mismatch`, `upstream_unavailable`, `timeout`, `bound_exceeded`, `capability_unavailable`, `cursor_expired`, `internal_error` | A2.5 |
| `message` | What failed, in words | A2.5 |
| `details` | An object holding what the caller needs for its next step, such as the release requested and the release served, the bound, its limit and the amount reached, or the surface, status and attempts of a failed upstream request (optional) | A2.5 |
| `correlationId` | The call's correlation identifier | M7.1 |

## 2. Tools

### EVS tools

| Tool | Inputs → result | What it does |
|---|---|---|
| `resolve_release` | `(terminology, channel?) → { terminology, channel, version, date, alternatives[] }` | Which release of a terminology is current, by channel (without one, the channel configured, A3.6.2); called explicitly, and its answer is passed to every later call. Items: `.`. |
| `get_concept` | `(terminology, release, code, include[]?) → concept` | One concept with the detail selected. `include`: synonyms, definitions, properties, semanticType. Not offered, `include`: descendants. Items: `.`. |
| `get_concepts` | `(terminology, release, codes[], include[]?) → { concepts[], missing[] }` | Many concepts in one platform call, with the detail selected. `include`: synonyms, definitions, properties, semanticType. Items: `concepts[]`. |
| `search_concepts` | `(terminology, release, query, mode?, limit?, cursor?, retired?) → { results[{ concept, score?, matchedOn? }], nextCursor?, totalKnown? }` | Ranked search of a terminology; semantic and hybrid from the interim NCIt index (M4.1). Retired concepts are returned with the others (retired include, the default, as the platform returns them), or alone (retired only), where retirement is a concept status the search can select: the listing's metadata.retiredStatusValue is among its metadata.conceptStatuses, and that status is what is sent. On 2 October 2026 this holds for NCIt alone. Every mode takes the same two values. Search cannot leave retired concepts out: the platform offers no such selection, and each result's active and status let a caller drop them itself. `mode`: lexical, typeahead, semantic, hybrid. `retired`: include, only. `limit`: default 10, at most 1000. `mode`: default lexical. `retired`: default include. Not offered, `retired`: exclude. Not offered, `evs_type`: match, phrase, AND, OR, fuzzy. Items: `results[].concept`. |
| `get_concept_hierarchy` | `(terminology, release, code, direction, depth?, limit?, cursor?) → { nodes[], paths[]?, truncation, nextCursor? }` | A concept's parents, children or paths to the root, bounded. nodes holds the concepts reached, not the one asked about, and limit is a page that the cursor continues. pathsToRoot returns each path the platform gives in paths, the codes from the concept to the root in the platform's order, with each concept on them once in nodes; depth, limit and cursor do not apply to it. `direction`: parent, child, pathsToRoot. `depth`: default 1, at most 4. `limit`: default 200, at most 1000. At most 200 upstream requests a call. Items: `nodes[]`. |
| `expand_value_set` | `(terminology, release, valueSet \| code, count?, offset?, activeOnly?) → { members[], total, truncation }` | The members of a value set, paged and bounded by the tool itself; $lookup, $validate-code, $subsumes and $translate where a caller asks. It pages by count and offset, as FHIR $expand does, in place of a cursor (the exception to M6.1); activeOnly is false unless given. Items: `members[]`. |
| `get_concept_neighborhood` | `(terminology, release, code, depth?, kinds[]?, maxNodes?, maxEdges?, budgetPerKind?, includeNegative?) → { nodes[], edges[], truncation }` | Bounded traversal across roles and associations with a budget per kind; nodes holds the concept asked about at depth 0 and those reached, and maxNodes counts them all. Given, budgetPerKind bounds the nodes each kind adds; not given, the tool shares maxNodes among the kinds asked so that none present is starved, in a way of its own. Negative assertions are returned marked, with the nodes they reach, which are not followed further unless includeNegative. `kinds`: parent, child, role, association, inverseRole, inverseAssociation. `depth`: default 2, at most 4. `maxNodes`: default 200, at most 1000. `maxEdges`: default 1000, at most 5000. `budgetPerKind`: at most 1000. At most 200 upstream requests a call. Items: `nodes[]`, `edges[]`. |
| `get_concept_subsets` | `(terminology, release, code) → { subsets[] }` | The subsets a concept belongs to. Items: `subsets[]`. |
| `get_concept_mappings` | `(terminology, release, code, targetTerminology?) → { mappings[] }` | The maps the platform carries on a concept, from it to other terminologies, unchanged and each with its target's version where the platform gives one; maps into the terminology from others are not this tool's. targetTerminology keeps the maps whose target the platform names exactly so, case included. Items: `mappings[]`. |
| `resolve_retired_code` | `(terminology, release, code) → { code, terminology, active, status?, replacements[] }` | Whether a code is retired (active false, as the platform publishes it), its status, and what the platform names as replacing it: replacements is an empty list, present, where the platform names none, retired or not. Items: `.`, `replacements[]`. |
| `list_relationships` | `(terminology, release) → { relationships[] }` | The relationship catalogue of a release, polarity marked by code. Items: `relationships[]`. |
| `list_terminologies` | `() → { terminologies[] }` | The terminologies available, with their current releases. Items: `terminologies[]`. |

### caDSR tools

| Tool | Inputs → result | What it does |
|---|---|---|
| `resolve_registry_release` | `() → registry_release` | The registry's content state: published false and the export's date while caDSR publishes no registry release, never an invented identifier; the release itself where one is. |
| `get_data_element` | `(publicId \| longName \| questionText, version?, include[]?, registryRelease?) → data_element` | One data element, at its latest version or the version given, with the sections include selects; without include, the record's own fields. questionText finds it by its preferred question text, an invalid request naming the candidates where several have it and not_found where none has; longName is capability_unavailable until the platform serves that lookup (OP-C02). `include`: permissibleValues, valueDomain, conceptAssociations, alternateNames, classificationSchemes. Items: `.`. |
| `search_data_elements` | `(query, mode?, filters?, limit?, cursor?, registryRelease?) → { results[{ dataElement, score?, matchedOn? }], nextCursor?, totalKnown?, truncation }` | Search of data elements, filtered by context, status and value-domain type, a page at a time up to the platform's cap of 1,000 results a query, which truncation reports (upstream_cap). semantic and hybrid are capability_unavailable until the platform serves them (OP-C04). `mode`: lexical, semantic, hybrid. `filters`: context, workflowStatus, registrationStatus, valueDomainType. `limit`: default 10, at most 100. `mode`: default lexical. Items: `results[].dataElement`. |
| `match_data_elements` | `(entities[{ name, userTip?, permissibleValues[]? }], matchLimit?, modelVariant?, similarityThreshold?, filters?, registryRelease?) → { matches[data_element_match] }` | Data elements matched to described entities, scored and rule-attributed, at most matchLimit for each entity; the contract's default is 10, and the maximum of 100 is this specification's, the contract stating none. modelVariant and similarityThreshold, which the platform does not take, are an invalid request when given. At most 10 entities a call: the contract states no maximum, and one entity took 28.9 s (10 September 2026) against a match timeout of 45 s. `filters`: context, workflowStatus, registrationStatus, classificationScheme, valueDomainType. `matchLimit`: default 10, at most 100. Computed from caller-supplied values: ttlMs 0, private. `entities`: at most 10 a call. Items: `matches[].dataElement`. |
| `match_value_meanings` | `(values[], strictness?, terminologyScope?, registryRelease?) → { matches[value_meaning_match] }` | Value meanings and concepts matched to values, each rule-attributed with its crosswalk; strictness is vmMatch's matchType, terminologyScope its evsTerminologyCodes. At most 10 values a call: the contract states no maximum, and one value took 15.5 s (10 September 2026) against a match timeout of 45 s. `strictness`: restricted, unrestricted. `strictness`: default restricted. Computed from caller-supplied values: ttlMs 0, private. `values`: at most 10 a call. Items: `matches[].item`. |
| `get_form` | `(publicId? \| keyword?, version?, includeModules?, registryRelease?) → form` | A form or case report form by public id, with its modules and questions; a keyword is an invalid request saying the platform needs an identifier (Form/query takes a public or protocol id only). `includeModules`: default True. Items: `.`. |
| `get_permissible_value` | `(permissibleValueId, registryRelease?) → permissible_value` | A permissible value by the identifier caDSR REST publishes for it; capability_unavailable until the platform retrieves a value by it (OP-C10). Items: `.`. |
| `get_code_map` | `(sourceSystem?, targetContext?, dataElementId?, limit?, cursor?, registryRelease?) → { codeMaps[code_map], nextCursor? }` | Code maps between source code systems and registered value sets, one per data element of the CRDC crosswalk with the contexts and commons that use it; targetContext selects those a context or commons uses, dataElementId one data element. `sourceSystem`: CRDC. `limit`: default 100, at most 1000. `sourceSystem`: default CRDC. Items: `codeMaps[]`. |
| `list_contexts` | `(limit?, cursor?, registryRelease?) → { contexts[context], nextCursor? }` | The registry's contexts, by name. `limit`: default 100, at most 1000. Items: `contexts[]`. |
| `list_classification_schemes` | `(context?, limit?, cursor?, registryRelease?) → { classificationSchemes[classification_scheme], nextCursor? }` | Classification schemes as objects with their nested items; capability_unavailable until the platform lists them (OP-C13). A data element's schemes come with get_data_element. `limit`: default 100, at most 1000. Items: `classificationSchemes[]`. |

### Cross-domain tools

| Tool | Inputs → result | What it does |
|---|---|---|
| `find_data_elements_for_concept` | `(conceptCode, terminology?, release?, expandDescendants?, includePermissibleValues?, limit?, cursor?) → { dataElements[], truncation, nextCursor? }` | The data elements and permissible values that use a concept, optionally across its descendants. |
| `get_concept_for_permissible_value` | `(permissibleValueId \| { dataElementId, value }) → concept` | The concept a permissible value stands for. |
| `resolve_stored_value` | `(conceptCode, commons, dataElementId?) → { storedValues[], confidence, evidence }` | The literal a data commons stores for a concept. |
| `get_release_alignment` | `() → { datasets[{ name, version, date }], maxIntervalDays, warning? }` | The release of every dataset a cross-domain answer touches, and the interval between them. |

### Workflow tools

| Tool | Inputs → result | What it does |
|---|---|---|
| `ground_value` | `(conceptCode \| text, commons?, release, registryRelease) → { concept, dataElements[], permissibleValues[], storedValues[], provenance, truncation }` | Concept to data element to permissible value to stored value, under one provenance envelope naming both content states. |
| `expand_cohort` | `(conceptCode, release, maxDepth?, includeNegative?, maxNodes?) → { codes[], excluded[], edges[], truncation, provenance }` | The codes a cohort query should use, exclusion assertions withheld and listed. |
| `harmonize_data_dictionary` | `(columns[{ name, description?, sampleValues[]? }], registryRelease, filters?) → { columns[{ matches[{ dataElement, score, rule }], permissibleValueAlignment }], unmatched[], provenance }` | A data dictionary's columns matched to data elements, with permissible-value alignment. Computed from caller-supplied values: ttlMs 0, private. |

## 3. Requirements

### Protocol gates: once per server, before any tool test

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| P-1 | tools/list names every tool of the profile under test, and no other. | M1.2, M1.5 | `tests/test_protocol.py::test_tools_list_names_the_tools_of_the_profile_and_no_other` | tested |
| P-2 | Every tool declares an outputSchema that is valid JSON Schema and covers the error record, admitting one and refusing one with a code outside its closed set or with no code. | M3.1, error | `tests/test_protocol.py::test_every_output_schema_admits_the_error_record_and_refuses_a_malformed_one` | tested |
| P-3 | No tool description, nor any description in a tool's schemas, contains placeholder, debug or development text. | A2.4, A10.3 | `tests/test_protocol.py::test_no_description_holds_placeholder_or_debug_text` | tested |
| P-4 | Tool names are verb-led, lowercase and underscore-separated. | A2.1 | `tests/test_protocol.py::test_tool_names_are_verb_led_lowercase_and_underscore_separated` | tested |
| P-5 | tools/list carries a positive ttlMs and cacheScope public. | M2.1, M2.2 | `tests/test_protocol.py::test_tools_list_may_be_cached_and_shared` | tested |
| P-6 | tools/list is the same after content calls, a release-pinned EVS call among them, and while the platform is unavailable. | M1.2 | `tests/test_protocol.py::test_tools_list_is_the_same_after_a_content_call`, `tests/test_protocol.py::test_tools_list_is_the_same_while_the_platform_is_unavailable` | tested |
| P-7 | A correlation identifier passed on a call is sent upstream on every platform request the call makes, and returned in the provenance of each item. | A6.2, M7.1 | `tests/test_protocol.py::test_a_correlation_identifier_goes_upstream_and_comes_back` | tested |
| P-8 | prompts/list and resources/list list the furnished prompts and resources with their arguments, and a prompt names only tools present in the profile. | M5.1 | — | planned #60 |
| P-9 | resources/read results carry ttlMs, cacheScope and a provenance record. | M2.1, A4.1, M5.1 | — | planned #60 |
| P-10 | Every tool declares readOnlyHint true, destructiveHint false, idempotentHint true and openWorldHint true. | M1.4 | `tests/test_protocol.py::test_every_tool_is_annotated_read_only_idempotent_and_open_world` | tested |
| P-11 | No tool description shows an operator, wildcard, filter syntax or parameter value that a tool test shows unsupported. | A2.3, A10.3 | `tests/test_protocol.py::test_no_description_or_schema_shows_what_the_tool_does_not_offer` | tested |
| P-12 | Each tool takes the parameters the specification names for it, under those names, and requires exactly those not marked optional, so that equivalent operations of the two modules share parameter names. | A2.2 | `tests/test_protocol.py::test_each_tool_takes_the_parameters_the_specification_names` | tested |

### Cross-cutting: against every content-returning tool

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| X-1 | The release in every returned item's provenance equals the release requested. | A3.1, A3.3 | `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concept]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concepts]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[search_concepts]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[expand_value_set]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concept_subsets]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concept_mappings]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[resolve_retired_code]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[list_relationships]` | planned #55 |
| X-2 | A request for a release the platform does not serve fails closed, with the requested-release-not-available error, or, where the platform offers no release-pinned form of the operation (A3.2), the mismatch error; and no content. | A3.4, A3.5 | `tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed[get_concept]`, `tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed[get_concepts]`, `tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed[search_concepts]`, `tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed[expand_value_set]`, `tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed[get_concept_subsets]`, `tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed[get_concept_mappings]`, `tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed[resolve_retired_code]`, `tests/test_crosscutting.py::test_a_release_the_platform_does_not_serve_fails_closed[list_relationships]` | planned #55 |
| X-3 | Content the upstream serves from another release than the one requested fails closed, with the mismatch error. | A3.4 | `tests/test_crosscutting.py::test_content_of_another_release_fails_closed[get_concept]`, `tests/test_crosscutting.py::test_content_of_another_release_fails_closed[get_concepts]`, `tests/test_crosscutting.py::test_content_of_another_release_fails_closed[search_concepts]`, `tests/test_crosscutting.py::test_content_of_another_release_fails_closed[expand_value_set]`, `tests/test_crosscutting.py::test_content_of_another_release_fails_closed[get_concept_subsets]`, `tests/test_crosscutting.py::test_content_of_another_release_fails_closed[get_concept_mappings]`, `tests/test_crosscutting.py::test_content_of_another_release_fails_closed[resolve_retired_code]`, `tests/test_crosscutting.py::test_content_of_another_release_fails_closed[list_relationships]` | planned #55 |
| X-4 | A query that legitimately matches nothing returns an empty result with provenance and no nextCursor, not an error. | A2.5, A2.6, M3.2, provenance | `tests/test_crosscutting.py::test_a_query_that_matches_nothing_is_an_empty_result_with_provenance[search_concepts]`, `tests/test_crosscutting.py::test_a_query_that_matches_nothing_is_an_empty_result_with_provenance[get_concept_mappings]`, `tests/test_crosscutting.py::test_a_query_that_matches_nothing_is_an_empty_result_with_provenance[get_code_map]` | planned #55 |
| X-5 | An upstream failure (a refused or closed connection, a server error, no answer in time) is an upstream error or a timeout, never an empty success. | A2.5, M3.2 | `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[resolve_release]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[get_concept]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[get_concepts]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[search_concepts]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[expand_value_set]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[get_concept_subsets]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[get_concept_mappings]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[resolve_retired_code]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[list_relationships]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[list_terminologies]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[get_data_element]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[get_form]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[match_value_meanings]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[get_code_map]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[search_data_elements]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[match_data_elements]`, `tests/test_crosscutting.py::test_an_unavailable_platform_is_an_upstream_error[list_contexts]` | planned #55 |
| X-6 | structuredContent validates against the tool's declared outputSchema, for successes and errors alike. | M3.1 | `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[resolve_release]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concept]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concepts]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[search_concepts]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[expand_value_set]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concept_subsets]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concept_mappings]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[resolve_retired_code]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[list_relationships]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[list_terminologies]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_data_element]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_form]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[match_value_meanings]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_code_map]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[search_data_elements]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[match_data_elements]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[list_contexts]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[resolve_release]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[get_concept]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[get_concepts]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[search_concepts]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[expand_value_set]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[get_concept_subsets]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[get_concept_mappings]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[resolve_retired_code]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[list_relationships]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[list_terminologies]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[get_data_element]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[get_form]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[match_value_meanings]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[get_code_map]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[search_data_elements]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[match_data_elements]`, `tests/test_crosscutting.py::test_an_error_validates_against_the_declared_output_schema[list_contexts]` | planned #55 |
| X-7 | Every item carries release, source surface, retrieval time and servedBy, and, where reached by traversal, depth, relationship, direction and polarity. | A4.1, A4.2, A4.4, provenance, traversal | `tests/test_crosscutting.py::test_every_item_carries_its_provenance[resolve_release]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concept]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concepts]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[search_concepts]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[expand_value_set]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concept_subsets]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concept_mappings]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[resolve_retired_code]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[list_relationships]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[list_terminologies]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_data_element]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_form]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[match_value_meanings]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_code_map]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[search_data_elements]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[match_data_elements]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[list_contexts]`, `tests/test_crosscutting.py::test_an_item_reached_by_traversal_says_how[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_an_item_reached_by_traversal_says_how[get_concept_neighborhood]` | planned #55 |
| X-8 | Fields the upstream supplied appear unchanged in provenance; none is dropped or renamed. | A4.3, provenance | `tests/test_crosscutting.py::test_what_the_platform_says_of_an_item_s_origin_is_passed_through[get_concept]`, `tests/test_crosscutting.py::test_what_the_platform_says_of_an_item_s_origin_is_passed_through[get_concepts]`, `tests/test_crosscutting.py::test_what_the_platform_says_of_an_item_s_origin_is_passed_through[search_concepts]`, `tests/test_crosscutting.py::test_what_the_platform_says_of_an_item_s_origin_is_passed_through[expand_value_set]`, `tests/test_crosscutting.py::test_what_the_platform_says_of_an_item_s_origin_is_passed_through[get_data_element]`, `tests/test_crosscutting.py::test_what_the_platform_says_of_an_item_s_origin_is_passed_through[get_form]` | planned #55 |
| X-9 | A code is returned bare, with its terminology in a separate field; never with an embedded prefix or as a URI. | A1.2, A1.3 | `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[resolve_release]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concept]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concepts]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[search_concepts]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[expand_value_set]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concept_subsets]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concept_mappings]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[resolve_retired_code]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[list_relationships]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[list_terminologies]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_data_element]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_form]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[match_value_meanings]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_code_map]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[search_data_elements]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[match_data_elements]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[list_contexts]` | planned #55 |
| X-10 | With a caller limit smaller than the result, truncation is reported with the bound reached and the magnitude omitted. | A5.1, A5.4, truncation | `tests/test_crosscutting.py::test_a_bound_reached_is_reported_with_how_much_was_left_out[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_a_bound_reached_is_reported_with_how_much_was_left_out[search_data_elements]` | planned #55 |
| X-11 | Within one tool call, no upstream request is made again with identical parameters, except to retry one that failed. | A5.8 | `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[resolve_release]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concept]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concepts]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[search_concepts]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[expand_value_set]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concept_subsets]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concept_mappings]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[resolve_retired_code]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[list_relationships]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[list_terminologies]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_data_element]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_form]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[match_value_meanings]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_code_map]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[search_data_elements]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[match_data_elements]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[list_contexts]` | planned #55 |
| X-12 | The licence key reaches the upstream request and appears in no log, error message or result. | A7.5 | `tests/test_crosscutting.py::test_the_licence_key_reaches_the_platform_and_nothing_the_server_returns_or_logs`, `tests/test_crosscutting.py::test_an_error_carries_no_licence_key` | tested |
| X-13 | Every result carries in its _meta the ttlMs and cacheScope M2.2 and M2.3 give what it holds. | M2.2, M2.3, M2.5 | `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concept]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concepts]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[search_concepts]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[expand_value_set]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concept_subsets]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concept_mappings]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[resolve_retired_code]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[list_relationships]`, `tests/test_crosscutting.py::test_a_cadsr_result_is_cached_as_what_it_holds_says[get_data_element]`, `tests/test_crosscutting.py::test_a_cadsr_result_is_cached_as_what_it_holds_says[get_form]`, `tests/test_crosscutting.py::test_a_cadsr_result_is_cached_as_what_it_holds_says[match_value_meanings]`, `tests/test_crosscutting.py::test_a_cadsr_result_is_cached_as_what_it_holds_says[get_code_map]`, `tests/test_crosscutting.py::test_a_cadsr_result_is_cached_as_what_it_holds_says[search_data_elements]`, `tests/test_crosscutting.py::test_a_cadsr_result_is_cached_as_what_it_holds_says[match_data_elements]`, `tests/test_crosscutting.py::test_a_cadsr_result_is_cached_as_what_it_holds_says[list_contexts]` | planned #55 |
| X-14 | Every result is a JSON object, its lists of items named fields of it. | M3.3 | `tests/test_crosscutting.py::test_a_result_is_an_object[resolve_release]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concept]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concepts]`, `tests/test_crosscutting.py::test_a_result_is_an_object[search_concepts]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_a_result_is_an_object[expand_value_set]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concept_subsets]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concept_mappings]`, `tests/test_crosscutting.py::test_a_result_is_an_object[resolve_retired_code]`, `tests/test_crosscutting.py::test_a_result_is_an_object[list_relationships]`, `tests/test_crosscutting.py::test_a_result_is_an_object[list_terminologies]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_data_element]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_form]`, `tests/test_crosscutting.py::test_a_result_is_an_object[match_value_meanings]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_code_map]`, `tests/test_crosscutting.py::test_a_result_is_an_object[search_data_elements]`, `tests/test_crosscutting.py::test_a_result_is_an_object[match_data_elements]`, `tests/test_crosscutting.py::test_a_result_is_an_object[list_contexts]` | planned #55 |
| X-15 | An upstream failure masked as a successful response (an error envelope in an HTTP 200, HTML where JSON was asked for) is an upstream error, never parsed as content. | A2.5, M3.2 | `tests/test_cadsr.py::test_a_server_that_leaves_out_accept_gets_html_and_never_parses_it`, `tests/test_cadsr.py::test_a_failure_inside_an_http_200_is_an_error_never_an_empty_success`, `tests/test_cadsr.py::test_html_where_json_was_asked_for_is_an_upstream_error`, `tests/test_cadsr.py::test_a_refusal_inside_an_http_200_is_an_invalid_request`, `tests/test_cadsr.py::test_an_unknown_form_answered_inside_an_http_200_is_not_found` | planned #52 |
| X-16 | After a 429 with Retry-After, the module waits at least that long before it asks again, and makes no request to that endpoint in between. | A6.5, A5.3 | `tests/test_crosscutting.py::test_a_rate_limited_request_is_asked_once_more_after_the_wait[resolve_release]`, `tests/test_crosscutting.py::test_a_rate_limited_request_is_asked_once_more_after_the_wait[get_data_element]` | tested |
| X-17 | A page smaller than the result carries nextCursor; following it returns the next items, repeats none of the page before, and keeps the release the first page was pinned to. Presented with any argument that differs from the first call's as applied, release and limit among them, the cursor is an invalid request; an optional argument the first call left out may be given as its default. | M6.1, M2.4 | `tests/test_cadsr.py::test_a_cursor_keeps_the_registry_release_it_was_issued_with[pinned-then-not]`, `tests/test_cadsr.py::test_a_cursor_keeps_the_registry_release_it_was_issued_with[unpinned-then-pinned]`, `tests/test_crosscutting.py::test_a_cursor_continues_with_the_next_items_of_the_same_release[search_concepts-0]`, `tests/test_crosscutting.py::test_a_cursor_continues_with_the_next_items_of_the_same_release[search_concepts-1]`, `tests/test_crosscutting.py::test_a_cursor_continues_with_the_next_items_of_the_same_release[get_concept_hierarchy-0]`, `tests/test_crosscutting.py::test_a_cursor_continues_with_the_next_items_of_the_same_release[get_code_map-0]`, `tests/test_crosscutting.py::test_a_cursor_continues_with_the_next_items_of_the_same_release[search_data_elements-0]`, `tests/test_crosscutting.py::test_a_cursor_continues_with_the_next_items_of_the_same_release[list_contexts-0]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[search_concepts-0-retired]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[search_concepts-0-limit]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[search_concepts-0-query]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[search_concepts-0-mode]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[search_concepts-1-mode]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[search_concepts-1-retired]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[search_concepts-1-query]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[search_concepts-1-limit]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[get_concept_hierarchy-0-depth]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[get_concept_hierarchy-0-limit]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[get_concept_hierarchy-0-direction]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[get_concept_hierarchy-0-code]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[get_code_map-0-limit]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[get_code_map-0-targetContext]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[get_code_map-0-dataElementId]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[search_data_elements-0-limit]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[search_data_elements-0-query]`, `tests/test_crosscutting.py::test_a_cursor_with_another_argument_is_an_invalid_request[list_contexts-0-limit]`, `tests/test_crosscutting.py::test_a_cursor_with_a_left_out_argument_given_as_its_default_continues[search_concepts-0]`, `tests/test_crosscutting.py::test_a_cursor_with_a_left_out_argument_given_as_its_default_continues[search_concepts-1]`, `tests/test_crosscutting.py::test_a_cursor_with_a_left_out_argument_given_as_its_default_continues[get_concept_hierarchy-0]`, `tests/test_crosscutting.py::test_a_cursor_with_a_left_out_argument_given_as_its_default_continues[get_code_map-0]`, `tests/test_crosscutting.py::test_a_cursor_with_a_left_out_argument_given_as_its_default_continues[search_data_elements-0]`, `tests/test_crosscutting.py::test_a_cursor_with_a_given_default_left_out_continues[search_concepts-0]`, `tests/test_crosscutting.py::test_a_cursor_with_a_given_default_left_out_continues[search_concepts-1]`, `tests/test_crosscutting.py::test_a_cursor_with_a_given_default_left_out_continues[get_concept_hierarchy-0]`, `tests/test_crosscutting.py::test_a_cursor_with_a_given_default_left_out_continues[get_code_map-0]`, `tests/test_crosscutting.py::test_a_cursor_with_a_given_default_left_out_continues[search_data_elements-0]`, `tests/test_crosscutting.py::test_a_cursor_without_an_argument_the_first_call_gave_is_an_invalid_request[search_concepts-1-mode]`, `tests/test_crosscutting.py::test_a_cursor_without_an_argument_the_first_call_gave_is_an_invalid_request[get_concept_hierarchy-0-limit]`, `tests/test_crosscutting.py::test_a_cursor_without_an_argument_the_first_call_gave_is_an_invalid_request[get_code_map-0-limit]`, `tests/test_crosscutting.py::test_a_cursor_without_an_argument_the_first_call_gave_is_an_invalid_request[list_contexts-0-limit]`, `tests/test_evs.py::test_an_index_search_s_cursor_continues_with_its_next_ranked_items[semantic]`, `tests/test_evs.py::test_an_index_search_s_cursor_continues_with_its_next_ranked_items[hybrid]` | tested |
| X-18 | A value below 1 for an argument with bounds is an invalid request, never raised to 1. | A5.1, A2.5 | `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-search_concepts-limit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-get_concept_hierarchy-depth]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-get_concept_hierarchy-limit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-get_concept_neighborhood-depth]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-get_concept_neighborhood-maxNodes]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-get_concept_neighborhood-maxEdges]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-get_concept_neighborhood-budgetPerKind]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-get_code_map-limit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-search_data_elements-limit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-match_data_elements-matchLimit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[0-list_contexts-limit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-search_concepts-limit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-get_concept_hierarchy-depth]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-get_concept_hierarchy-limit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-get_concept_neighborhood-depth]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-get_concept_neighborhood-maxNodes]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-get_concept_neighborhood-maxEdges]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-get_concept_neighborhood-budgetPerKind]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-get_code_map-limit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-search_data_elements-limit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-match_data_elements-matchLimit]`, `tests/test_crosscutting.py::test_a_bounded_argument_below_one_is_an_invalid_request[-1-list_contexts-limit]` | tested |
| X-19 | An item of a terminology whose listing row carries licence text carries that text as its attribution. | A7.3, provenance | `tests/test_crosscutting.py::test_an_item_of_a_licensed_terminology_carries_its_licence_text` | planned #52 |
| X-20 | A call that leaves out an optional argument with a stated default returns what the call that gives the default returns. | A5.1, M6.1 | `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[search_concepts-0]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[get_concept_hierarchy-0]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[get_concept_hierarchy-1]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[get_concept_neighborhood-0]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[get_concept_neighborhood-1]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[get_concept_neighborhood-2]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[get_form-0]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[match_value_meanings-0]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[get_code_map-0]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[search_data_elements-0]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[match_data_elements-0]`, `tests/test_crosscutting.py::test_a_left_out_argument_is_its_stated_default[list_contexts-0]` | tested |
| X-21 | A caDSR content call without registryRelease names the registry form of the release with neither identifier nor date; with a registry release caDSR does not publish, it is release_not_available, never answered unpinned; with one caDSR publishes, the release is asked for and named in provenance with its date. | A3.8, A3.4, provenance | `tests/test_cadsr.py::test_a_published_registry_release_is_asked_for_and_named_with_its_date`, `tests/test_crosscutting.py::test_a_cadsr_item_without_a_registry_release_names_the_registry_alone[get_data_element]`, `tests/test_crosscutting.py::test_a_cadsr_item_without_a_registry_release_names_the_registry_alone[get_form]`, `tests/test_crosscutting.py::test_a_cadsr_item_without_a_registry_release_names_the_registry_alone[match_value_meanings]`, `tests/test_crosscutting.py::test_a_cadsr_item_without_a_registry_release_names_the_registry_alone[get_code_map]`, `tests/test_crosscutting.py::test_a_cadsr_item_without_a_registry_release_names_the_registry_alone[search_data_elements]`, `tests/test_crosscutting.py::test_a_cadsr_item_without_a_registry_release_names_the_registry_alone[match_data_elements]`, `tests/test_crosscutting.py::test_a_cadsr_item_without_a_registry_release_names_the_registry_alone[list_contexts]`, `tests/test_crosscutting.py::test_a_registry_release_cadsr_does_not_publish_fails_closed[get_data_element]`, `tests/test_crosscutting.py::test_a_registry_release_cadsr_does_not_publish_fails_closed[get_form]`, `tests/test_crosscutting.py::test_a_registry_release_cadsr_does_not_publish_fails_closed[match_value_meanings]`, `tests/test_crosscutting.py::test_a_registry_release_cadsr_does_not_publish_fails_closed[get_code_map]`, `tests/test_crosscutting.py::test_a_registry_release_cadsr_does_not_publish_fails_closed[search_data_elements]`, `tests/test_crosscutting.py::test_a_registry_release_cadsr_does_not_publish_fails_closed[match_data_elements]`, `tests/test_crosscutting.py::test_a_registry_release_cadsr_does_not_publish_fails_closed[list_contexts]` | tested |

### EVS tools

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| resolve_release-1 | A channel's release is resolved by its tag, never by the first row flagged latest; with a weekly and a monthly row both latest, each channel gets its own. | resolve_release, A3.6.1, A3.6.2 | `tests/test_evs.py::test_a_channel_s_release_is_the_row_its_tag_names[monthly]`, `tests/test_evs.py::test_a_channel_s_release_is_the_row_its_tag_names[weekly]` | tested |
| resolve_release-2 | Where one channel's query names two releases, the tool fails closed with release_not_available, naming both in details. | resolve_release, A3.6.3, error | `tests/test_evs.py::test_a_channel_whose_query_names_two_releases_fails_closed` | tested |
| resolve_release-3 | The result carries ttlMs 0. | resolve_release, M2.2 | `tests/test_evs.py::test_a_resolved_release_is_never_cached` | tested |
| get_concept-1 | Each include value returns its section and no other section. | get_concept, concept | `tests/test_evs.py::test_an_include_value_returns_its_section_and_no_other[synonyms]`, `tests/test_evs.py::test_an_include_value_returns_its_section_and_no_other[definitions]`, `tests/test_evs.py::test_an_include_value_returns_its_section_and_no_other[properties]`, `tests/test_evs.py::test_an_include_value_returns_its_section_and_no_other[semanticType]` | tested |
| get_concept-2 | descendants as an include value is an invalid request. | get_concept, error | `tests/test_evs.py::test_descendants_is_no_include_value` | tested |
| get_concept-3 | The concept carries code, terminology, name and active always, and status where the platform publishes one; active and status as the platform publishes them. | get_concept, concept, A8.1, A9.1 | `tests/test_evs.py::test_a_concept_carries_its_identity_and_the_status_the_platform_publishes[current]`, `tests/test_evs.py::test_a_concept_carries_its_identity_and_the_status_the_platform_publishes[retired]` | tested |
| get_concepts-1 | A batch of codes is one upstream request. | get_concepts, A5.8 | `tests/test_evs.py::test_a_batch_is_one_upstream_request` | tested |
| get_concepts-2 | A code the upstream omits is named in missing. | get_concepts | `tests/test_evs.py::test_a_code_the_platform_leaves_out_of_a_batch_is_named_missing` | tested |
| get_concepts-3 | The concepts come back in request order, never in the upstream's order. | get_concepts | `tests/test_evs.py::test_a_batch_comes_back_in_request_order[as-listed]`, `tests/test_evs.py::test_a_batch_comes_back_in_request_order[reversed]` | tested |
| search_concepts-1 | lexical search returns the platform's matches in the platform's order, a page at a time, each with the platform's highlight as matchedOn and no score, and the platform's total as totalKnown. | search_concepts, search_result, A9.2 | `tests/test_evs.py::test_lexical_search_returns_the_platform_s_matches_in_its_order_a_page_at_a_time` | tested |
| search_concepts-2 | typeahead returns the platform's prefix matches in the platform's order, with neither matchedOn nor score, since the platform gives neither. | search_concepts, search_result, A9.2 | `tests/test_evs.py::test_typeahead_returns_the_platform_s_prefix_matches_in_its_order` | tested |
| search_concepts-3 | semantic and hybrid return concepts of the interim index, each with a score, in descending order of score, matchedOn naming the field matched (name, synonym or definition), and provenance naming the index's release, with source evs_index and servedBy index; a query equal to a concept's preferred name returns that concept on the first page. They rank every indexed concept of the terminology, with no score threshold, so totalKnown is the size of the index set; the page and the cursor bound what is returned. The suite does not judge score values, the order beyond that, or which other concepts semantic search returns. | search_concepts, search_result, provenance, M4.1 | `tests/test_evs.py::test_index_search_returns_scored_indexed_concepts_and_the_named_one_first_page[semantic]`, `tests/test_evs.py::test_index_search_returns_scored_indexed_concepts_and_the_named_one_first_page[hybrid]`, `tests/test_evs.py::test_an_index_search_s_cursor_continues_with_its_next_ranked_items[semantic]`, `tests/test_evs.py::test_an_index_search_s_cursor_continues_with_its_next_ranked_items[hybrid]` | tested |
| search_concepts-4 | A mode the server cannot serve for the terminology asked is an invalid request, never an empty result. | search_concepts, A2.5 | `tests/test_evs.py::test_an_index_mode_for_a_terminology_without_an_index_is_invalid[as-prepared-semantic]`, `tests/test_evs.py::test_an_index_mode_for_a_terminology_without_an_index_is_invalid[as-prepared-hybrid]`, `tests/test_evs.py::test_an_index_mode_for_a_terminology_without_an_index_is_invalid[none-semantic]`, `tests/test_evs.py::test_an_index_mode_for_a_terminology_without_an_index_is_invalid[none-hybrid]` | tested |
| search_concepts-5 | semantic and hybrid for a release other than the index's fail closed with release_mismatch, never with results of the index's release. | search_concepts, M4.1, A3.4 | `tests/test_evs.py::test_an_index_search_for_another_release_fails_closed[semantic]`, `tests/test_evs.py::test_an_index_search_for_another_release_fails_closed[hybrid]` | tested |
| search_concepts-6 | Retired concepts are returned unless the caller asks for them alone (retired only), as the platform returns them; with only, every result is retired, by the retired status the terminology's listing names among its concept statuses; any other retired value, exclude included, is an invalid request. A8.3 is met in part, since a caller cannot ask for retired concepts to be left out, a platform dependency (A9.3) whose question is in #42. | search_concepts, A8.3, A9.1, A9.3, A2.5 | `tests/test_evs.py::test_retired_concepts_are_returned_with_the_others_by_default[default]`, `tests/test_evs.py::test_retired_concepts_are_returned_with_the_others_by_default[include]`, `tests/test_evs.py::test_retired_only_returns_the_retired_concepts_alone`, `tests/test_evs.py::test_typeahead_takes_retired_only_as_lexical_search_does`, `tests/test_evs.py::test_a_retired_value_outside_the_two_is_invalid[exclude]`, `tests/test_evs.py::test_a_retired_value_outside_the_two_is_invalid[ONLY]`, `tests/test_evs.py::test_a_retired_value_outside_the_two_is_invalid[]`, `tests/test_evs.py::test_index_search_returns_retired_concepts_with_the_others_or_alone[semantic]`, `tests/test_evs.py::test_index_search_returns_retired_concepts_with_the_others_or_alone[hybrid]`, `tests/test_evs.py::test_a_retired_value_outside_the_two_is_invalid_in_index_modes[exclude-semantic]`, `tests/test_evs.py::test_a_retired_value_outside_the_two_is_invalid_in_index_modes[exclude-hybrid]`, `tests/test_evs.py::test_a_retired_value_outside_the_two_is_invalid_in_index_modes[ONLY-semantic]`, `tests/test_evs.py::test_a_retired_value_outside_the_two_is_invalid_in_index_modes[ONLY-hybrid]`, `tests/test_evs.py::test_a_retired_value_outside_the_two_is_invalid_in_index_modes[-semantic]`, `tests/test_evs.py::test_a_retired_value_outside_the_two_is_invalid_in_index_modes[-hybrid]` | tested |
| search_concepts-7 | retired only is an invalid request for a terminology whose listing names no retired status, or names one that is not among its concept statuses, as a mode the terminology cannot be searched in is (search_concepts-4); on 2 October 2026 that is every listed terminology but NCIt. | search_concepts, A2.5, A9.5 | `tests/test_evs.py::test_retired_only_where_the_status_is_none_the_search_selects_is_invalid`, `tests/test_evs.py::test_retired_only_where_the_listing_names_no_retired_status_is_invalid` | tested |
| search_concepts-8 | Without an index, semantic and hybrid are capability_unavailable, never an empty result. The terminology is served but its index is not there yet, unlike a mode the terminology is never searched in (search_concepts-4). | search_concepts, M1.2, A2.5 | `tests/test_evs.py::test_an_index_mode_without_an_index_is_unavailable[semantic]`, `tests/test_evs.py::test_an_index_mode_without_an_index_is_unavailable[hybrid]` | tested |
| get_concept_hierarchy-1 | A depth above the maximum is applied as the maximum; no node is deeper, and a hierarchy that continues below it is reported as truncation by depth, with the depth applied as the limit. | get_concept_hierarchy, node, A5.2, A5.4 | `tests/test_evs.py::test_a_depth_above_the_maximum_is_applied_as_the_maximum_and_reported` | tested |
| get_concept_hierarchy-2 | pathsToRoot returns every path the platform gives, each the codes from the concept to the root in the platform's order. | get_concept_hierarchy, A9.1 | `tests/test_evs.py::test_paths_to_root_are_the_platform_s_paths_in_its_order` | tested |
| get_concept_hierarchy-3 | limit is a page that the cursor continues; the pages together hold the concepts the platform lists, in its order, none twice. | get_concept_hierarchy, node, M6.1 | `tests/test_evs.py::test_limit_is_a_page_the_cursor_continues_to_the_end` | tested |
| expand_value_set-1 | count and offset, which the platform ignores, select the members the tool returns, in the platform's order, and total counts them all. | expand_value_set, member, A5.1 | `tests/test_evs.py::test_count_and_offset_select_the_members_and_total_counts_them_all[first]`, `tests/test_evs.py::test_count_and_offset_select_the_members_and_total_counts_them_all[middle]`, `tests/test_evs.py::test_count_and_offset_select_the_members_and_total_counts_them_all[last]` | tested |
| expand_value_set-2 | activeOnly, which the platform ignores, leaves out the members the expansion marks inactive, and total counts those kept; when it is false or not given they are listed, marked inactive. | expand_value_set, member, A8.3 | `tests/test_evs.py::test_active_only_leaves_out_the_members_marked_inactive[true]`, `tests/test_evs.py::test_active_only_leaves_out_the_members_marked_inactive[false]`, `tests/test_evs.py::test_active_only_leaves_out_the_members_marked_inactive[default]` | tested |
| get_concept_neighborhood-1 | A node budget reached by one kind starves no other kind asked for that is present, and truncation.perKind names the kind that reached it. | get_concept_neighborhood, truncation, A5.5 | `tests/test_evs.py::test_a_kind_that_reaches_its_budget_starves_no_other[in-order-roles]`, `tests/test_evs.py::test_a_kind_that_reaches_its_budget_starves_no_other[in-order-associations]`, `tests/test_evs.py::test_a_kind_that_reaches_its_budget_starves_no_other[reversed-roles]`, `tests/test_evs.py::test_a_kind_that_reaches_its_budget_starves_no_other[reversed-associations]` | tested |
| get_concept_neighborhood-2 | An edge is negative exactly when its relationship's code is in the terminology's exclusion set, whatever the relationship is named. | get_concept_neighborhood, edge, traversal, A5.7 | `tests/test_evs.py::test_polarity_follows_the_relationship_code_not_its_name` | tested |
| get_concept_neighborhood-3 | Negative edges are returned, marked, and not followed further unless includeNegative is true. | get_concept_neighborhood, edge, A5.6 | `tests/test_evs.py::test_negative_edges_are_returned_marked_and_followed_only_when_included[default]`, `tests/test_evs.py::test_negative_edges_are_returned_marked_and_followed_only_when_included[included]` | tested |
| get_concept_neighborhood-4 | A node limit above the maximum is applied as the maximum, and the truncation record's limit shows the value applied. | get_concept_neighborhood, truncation, A5.2 | `tests/test_evs.py::test_a_node_limit_above_the_maximum_is_applied_as_the_maximum` | tested |
| get_concept_neighborhood-5 | The outbound request bound counts retries, as the attempts of a failed request report them, and no call makes more requests than the bound. Reaching the bound is shown by no fixture, since batch sizes are each server's own; a server's own tests must show it. | get_concept_neighborhood, get_concept_hierarchy, A5.2, A5.3 | `tests/test_evs.py::test_the_attempts_a_failed_call_reports_are_the_requests_it_made[get_concept_neighborhood]`, `tests/test_evs.py::test_the_attempts_a_failed_call_reports_are_the_requests_it_made[get_concept_hierarchy]` | tested |
| get_concept_neighborhood-6 | budgetPerKind, when given, bounds the nodes each kind adds, and a kind that reaches it is named in truncation.perKind. | get_concept_neighborhood, truncation, A5.5 | `tests/test_evs.py::test_budget_per_kind_bounds_the_nodes_each_kind_adds[roles]`, `tests/test_evs.py::test_budget_per_kind_bounds_the_nodes_each_kind_adds[associations]` | tested |
| get_concept_subsets-1 | The subsets are the platform's Concept_In_Subset associations of the concept, each by code and name, in the platform's order. | get_concept_subsets, subset, A9.1 | `tests/test_evs.py::test_the_subsets_are_the_concept_s_subset_associations_in_order` | tested |
| get_concept_mappings-1 | The mappings are the maps the platform carries on the concept, unchanged, in the platform's order. | get_concept_mappings, mapping, A9.1 | `tests/test_evs.py::test_the_mappings_are_the_concept_s_maps_unchanged_in_order` | tested |
| get_concept_mappings-2 | targetTerminology keeps the maps whose target the platform names exactly so, case included, and no other. | get_concept_mappings, mapping | `tests/test_evs.py::test_target_terminology_keeps_the_maps_with_that_target_and_no_other` | tested |
| resolve_retired_code-1 | A retired code returns active false, the platform's status unchanged, and the replacements the platform names, by code and name, an empty list where it names none. | resolve_retired_code, replacement, A8.1, A8.2 | `tests/test_evs.py::test_a_retired_code_is_inactive_with_its_status_and_replacements[replaced]`, `tests/test_evs.py::test_a_retired_code_is_inactive_with_its_status_and_replacements[unreplaced]` | tested |
| resolve_retired_code-2 | An active code returns active true, its status, and no replacement. | resolve_retired_code, A8.1 | `tests/test_evs.py::test_an_active_code_is_active_with_its_status_and_no_replacement` | tested |
| list_relationships-1 | Every role and association of the release's catalogue is listed, by code, name and kind. | list_relationships, relationship, A9.1 | `tests/test_evs.py::test_every_relationship_of_the_catalogue_is_listed_by_code_name_and_kind` | tested |
| list_relationships-2 | A relationship is negative exactly when its code is in the terminology's exclusion set, whatever it is named. | list_relationships, relationship, traversal, A5.7 | `tests/test_evs.py::test_a_relationship_s_polarity_follows_its_code_not_its_name` | tested |
| list_relationships-3 | For a release whose catalogue lacks a code of the exclusion set, list_relationships and get_concept_neighborhood fail closed with internal_error naming the absent codes. | list_relationships, get_concept_neighborhood, relationship, A5.7 | `tests/test_evs.py::test_a_catalogue_without_a_code_of_the_exclusion_set_fails_closed[list_relationships]`, `tests/test_evs.py::test_a_catalogue_without_a_code_of_the_exclusion_set_fails_closed[get_concept_neighborhood]` | tested |
| list_terminologies-1 | The available terminologies are listed with their current releases, none the platform offers left out. | list_terminologies, A7.2, terminology | `tests/test_evs.py::test_every_terminology_the_platform_serves_is_listed_with_its_current_release` | tested |

### caDSR tools

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| resolve_registry_release-1 | Without a registry release upstream, published is false, generatedAt is the export's date as the export folder gives it and sourceDistribution the file it dates, with no identifier; with one, published is true and the release is returned; ttlMs 0. | resolve_registry_release, registry_release, A3.8, M2.2 | `tests/test_cadsr.py::test_without_a_registry_release_the_export_date_stands_for_it`, `tests/test_cadsr.py::test_a_published_registry_release_is_returned` | tested |
| get_data_element-1 | Each include returns its section, and without include the record's own fields alone; the data element's own version and statuses are surfaced; a version given returns that version. | get_data_element, data_element, A3.8.3 | `tests/test_cadsr.py::test_a_data_element_is_its_own_fields_alone_with_its_version_and_statuses[latest]`, `tests/test_cadsr.py::test_a_data_element_is_its_own_fields_alone_with_its_version_and_statuses[version-1]`, `tests/test_cadsr.py::test_each_include_returns_its_section_as_the_platform_gives_it[permissibleValues]`, `tests/test_cadsr.py::test_each_include_returns_its_section_as_the_platform_gives_it[valueDomain]`, `tests/test_cadsr.py::test_each_include_returns_its_section_as_the_platform_gives_it[conceptAssociations]`, `tests/test_cadsr.py::test_each_include_returns_its_section_as_the_platform_gives_it[alternateNames]`, `tests/test_cadsr.py::test_each_include_returns_its_section_as_the_platform_gives_it[classificationSchemes]` | tested |
| get_data_element-2 | questionText finds the data element whose preferred question text it is; where several have it, the call is an invalid request naming their public ids, and where none has it, not_found; longName is capability_unavailable until the platform serves that lookup (OP-C02), never an empty result. | get_data_element, M1.2, A9.3 | `tests/test_cadsr.py::test_a_question_text_one_data_element_has_finds_it`, `tests/test_cadsr.py::test_a_question_text_several_have_is_an_invalid_request_naming_them`, `tests/test_cadsr.py::test_a_long_name_lookup_is_unavailable_never_empty`, `tests/test_cadsr.py::test_a_question_text_no_data_element_has_is_not_found` | tested |
| search_data_elements-1 | A lexical search the platform answers with its cap of 1,000 results reports truncation with bound upstream_cap, limit 1000, omitted at least 1 and exact false; totalKnown only where the platform counts; semantic and hybrid are capability_unavailable until the platform serves them (OP-C04). | search_data_elements, truncation, A5.4, M1.2, M6.1 | `tests/test_cadsr.py::test_a_capability_the_platform_lacks_is_unavailable_never_empty[search-semantic]`, `tests/test_cadsr.py::test_a_capability_the_platform_lacks_is_unavailable_never_empty[search-hybrid]`, `tests/test_cadsr.py::test_a_search_the_platform_caps_reports_the_cap_and_no_total` | tested |
| search_data_elements-2 | Filters by context, workflow status, registration status and value-domain type apply. The platform's search form names no parameter for them yet (OP-C03), a platform dependency (A9.3). | search_data_elements, A9.3 | — | planned #42 |
| match_data_elements-1 | Matches are scored and rule-attributed, at most matchLimit for an entity; modelVariant and similarityThreshold are refused as an invalid request, never ignored; more entities than the tool's maximum is an invalid request; a platform slower than the match timeout is a timeout error, never an empty result. | match_data_elements, data_element_match, A5.2, A2.5 | `tests/test_cadsr.py::test_data_element_matches_are_scored_and_rule_attributed_as_the_platform_says`, `tests/test_cadsr.py::test_what_the_platform_does_not_take_is_an_invalid_request_never_ignored[model-variant]`, `tests/test_cadsr.py::test_what_the_platform_does_not_take_is_an_invalid_request_never_ignored[similarity-threshold]`, `tests/test_cadsr.py::test_what_the_platform_does_not_take_is_an_invalid_request_never_ignored[too-many-entities]`, `tests/test_cadsr.py::test_at_most_match_limit_matches_come_for_an_entity`, `tests/test_cadsr.py::test_matching_slower_than_the_match_timeout_is_a_timeout[match_data_elements]` | tested |
| match_value_meanings-1 | As match_data_elements, for value meanings and concepts: each match rule-attributed, scored where the platform scores (vmMatch does not), naming the item's type and the code system of its concept, with its crosswalk, none where the platform says NA. | match_value_meanings, value_meaning_match, A5.2, A2.5 | `tests/test_cadsr.py::test_value_meaning_matches_are_the_platform_s_in_its_order_with_their_rule`, `tests/test_cadsr.py::test_more_values_than_the_tool_takes_is_an_invalid_request`, `tests/test_cadsr.py::test_matching_slower_than_the_match_timeout_is_a_timeout[match_value_meanings]`, `tests/test_cadsr.py::test_a_match_with_no_concept_has_none_never_null` | tested |
| get_form-1 | A form retrieved by public id returns its modules and questions, and its status unchanged, a retired one included; a keyword is an invalid request stating that the platform needs an identifier. | get_form, form, A8.1 | `tests/test_cadsr.py::test_a_form_returns_its_modules_and_its_status_unchanged_a_retired_one_too`, `tests/test_cadsr.py::test_a_form_without_its_modules_has_none`, `tests/test_cadsr.py::test_a_form_keyword_is_an_invalid_request_saying_an_identifier_is_needed` | tested |
| get_permissible_value-1 | Retrieval by permissible value id is capability_unavailable until the platform serves it (OP-C10, A9.3), never not_found or empty; the id the tool takes is the one caDSR REST publishes. | get_permissible_value, permissible_value, M1.2, A9.3 | `tests/test_cadsr.py::test_a_capability_the_platform_lacks_is_unavailable_never_empty[permissible-value]` | tested |
| get_code_map-1 | The crosswalk is returned per data element, with the contexts and commons that use it and its coverage; a data element without value-level binding says so rather than returning no values; a source system other than CRDC is an invalid request. | get_code_map, code_map | `tests/test_cadsr.py::test_a_code_map_is_a_data_element_s_values_users_and_coverage`, `tests/test_cadsr.py::test_a_data_element_without_value_level_binding_says_so`, `tests/test_cadsr.py::test_a_context_selects_the_code_maps_it_uses[GDC]`, `tests/test_cadsr.py::test_a_context_selects_the_code_maps_it_uses[CIP]`, `tests/test_cadsr.py::test_a_source_system_other_than_crdc_is_an_invalid_request` | tested |
| list_contexts-1 | Contexts come from the registry's context names, the name its identifier; list_classification_schemes is capability_unavailable until the platform lists schemes (OP-C13), and a data element's classification schemes come as objects with their nested items. | list_contexts, list_classification_schemes, context, classification_scheme, A7.2 | `tests/test_cadsr.py::test_a_capability_the_platform_lacks_is_unavailable_never_empty[classification-schemes]`, `tests/test_cadsr.py::test_the_contexts_are_the_registry_s_context_names` | tested |

### Cross-domain tools

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| find_data_elements_for_concept-1 | The batch form is used where available; descendant expansion is bounded and reported; where served from the Shared SI Service, both graphs' release identities are recorded; a masked upstream failure is an error. | find_data_elements_for_concept, A3.7, A5.4, M3.2 | — | planned #55 |
| get_concept_for_permissible_value-1 | Resolves to a concept with both release identities. | get_concept_for_permissible_value | — | planned #55 |
| resolve_stored_value-1 | A GDC value resolves through the mapset named in provenance; a node without a mapping reports no mapping with its coverage, never the preferred term as if stored. | resolve_stored_value | — | planned #55 |
| get_release_alignment-1 | Every dataset's release and date are returned with the interval, and a warning above the configured threshold; ttlMs 0. | get_release_alignment, A3.7, M2.2 | — | planned #55 |

### Workflow tools

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| ground_value-1 | One provenance envelope names both content states and fails closed if either cannot be named; each hop's truncation is carried through. | ground_value | — | planned #55 |
| expand_cohort-1 | Exclusion codes are withheld from codes and listed in excluded by default; the result equals composing the fine-grained tools. | expand_cohort, A5.6 | — | planned #55 |
| harmonize_data_dictionary-1 | One upstream match call per column, batched where the upstream allows; unmatched columns listed; one registry release across all matches. | harmonize_data_dictionary | — | planned #55 |

## 4. Acceptance

The acceptance suite (`acceptance/`, [README](../acceptance/README.md)) tests the requirements
against a server: in fixture mode against the recorded and crafted upstream answers of
`acceptance/fixtures/`, and in live mode for the tests marked live-capable. A run gives each
required tool one outcome:

| Outcome | Meaning |
|---|---|
| PASS | Every gate, every cross-cutting test and every test of the tool passes, in fixture mode and, for the live-capable tests, in live mode |
| PASS (fixture only) | Passes in fixture mode; each live failure is a test with a documented upstream limitation, named per test |
| FAIL | A test fails in fixture mode, or a live-capable test fails live without a documented upstream limitation |
| INCOMPLETE | The tests that ran passed, but others could not run for want of a capability: a hardening candidate, never PASS |
| NO FIXTURE | An upstream request found no fixture; the report names it |
| NOT RUN | No test of the tool ran, for example in a live run or without the operator's prepare step |
| NO TESTS | The suite has no test for the tool: a defect of the suite |
| NOT IMPLEMENTED | The server exposes the tool neither by name nor through the baseline tool map (the Prototype Baseline Assessment) |

An upstream limitation excuses a failing live test only test by test, each with its
requirement named; one known limitation does not excuse another live failure of the same tool,
and each such limitation is an entry in the upstream requirements package. A
gate that fails live fails every tool, as a failing live test does. A module is accepted when
every one of its tools is PASS or PASS (fixture only) and the gates pass; INCOMPLETE, NOT RUN, NO FIXTURE, NO TESTS and NOT IMPLEMENTED are not accepted.
Every report names the suite version, the fixture-set version,
a digest over the suite, and the tools whose tests have never run against an implementation.

### Settings the suite gives a server

The suite starts each server under test with these settings, and with no other `NCI_SI_*`
setting of the operator's environment. A server is configured by them: in fixture mode every
upstream base URL names the fixture server, and a scenario that needs a credential, a short
timeout or a log level sets it.

| Setting | When the suite sets it | Format |
|---|---|---|
| `NCI_SI_UPSTREAM_MODE` | Always | `fixture` or `live` |
| `NCI_SI_DATA_DIR` | Always | A directory of the server's own, fresh for each server; a copy of the prepare command's where one is given |
| `NCI_SI_EVS_BASE_URL`, `NCI_SI_EVS_FHIR_BASE_URL`, `NCI_SI_CADSR_BASE_URL`, `NCI_SI_CADSR_FTP_URL`, `NCI_SI_SSIS_FACADE_URL`, `NCI_SI_SSIS_SPARQL_URL` | Fixture mode | A base URL, to which the server adds the platform's own paths as it does to the production one (`…/evs` + `/api/v1/…`; `…/cadsr` + `/NCIAPI/1.0/api/…`) |
| `NCI_SI_EVS_LICENSE_KEY` | Live mode, from the operator; the `license/restricted` scenario | The key, sent as the `X-EVSRESTAPI-License-Key` header, only with licensed content |
| `NCI_SI_CADSR_CREDENTIAL` | Live mode, from the operator; the `cadsr/credentialed` and `cadsr/match-timeout` scenarios | `user:password`, sent as HTTP Basic authentication (`Authorization: Basic` and its base64), as every caDSR contract declares |
| `NCI_SI_TIMEOUT_SECONDS` | The `upstream/unavailable` scenario | Seconds an upstream request may take |
| `NCI_SI_MATCH_TIMEOUT_SECONDS` | The `cadsr/match-timeout` scenario | Seconds a caDSR match request may take |
| `NCI_SI_LOG_LEVEL` | The `license/restricted` scenario | `DEBUG`, so that a secret logged as a detail shows |
| `NCI_SI_ACCEPTANCE_INDEX_CODES` | The prepare command only | A file of the concept codes to index, one per line |

The Prototype Baseline Assessment reads the outcomes of a run against the furnished prototype:
a tool that passes is a reuse candidate, one that fails or is INCOMPLETE a hardening candidate,
and one NOT IMPLEMENTED new development.

The suite does not test response time and throughput (the benchmark), the ranking quality of
semantic search (the retrieval evaluation set), security controls (the contractor's security
tests), the platform APIs themselves (the conformance suite), or operator procedures such as
building, activating and rolling back the index (the contractor's integration tests and the
deployment guide). It tests what each of these must show at the tool surface: a structured
timeout error, the index release in provenance, correlation, and no secret in a result or log.

Changes to this specification, the suite, the fixture set and the request forms follow a
versioned change request, an impact assessment and the written approval of the branch chief or
a delegate, from the furnished tag. The request forms, prompt templates and resource definitions
are furnished as initial versions for the EVS and caDSR teams to refine through that record.
The suite and the fixture set are versioned independently. A contractor may propose a test, but
may not substitute its own tests for the suite as the basis of acceptance.
Every suite test cites the requirements it enforces, and at the furnished tag every requirement
is cited by one; a report whose digest is not that of an approved release reads MODIFIED;
changes under `spec/` and `acceptance/` need a code owner's approval; and the change log records
the approval and the requirement each change serves.
