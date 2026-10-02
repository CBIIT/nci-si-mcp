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
| M2.2 | A release-pinned result has a long ttlMs, an unpinned one a short ttlMs, and resolve_release, resolve_registry_release and get_release_alignment ttlMs 0; tools/list is long and public. |
| M2.3 | cacheScope is public for governed content and private for results computed from caller-supplied values. |
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
| M6.1 | Pagination is cursor-based, cursor in and nextCursor out, with totalKnown where it can be determined. |

### M7 · Correlation on the wire

| Id | Convention |
|---|---|
| M7.1 | A caller passes its correlation identifier as correlationId in the _meta of tools/call. The module sends it upstream on every platform request the call makes, in the X-Correlation-ID header until the platform defines a parameter or header of its own (A6.2a), and returns it in the provenance of each item and in the error record. |

### The provenance record

Every returned item carries one, beside its identifier and status, which are fields of the item (A4.1, A4.4). Field names are camelCase.

| Field | Content | Rule |
|---|---|---|
| `release` | The release in effect: { terminology \| registry, identifier, date } | A3.3 |
| `source` | The surface that supplied the item: one of `evs_rest`, `evs_fhir`, `evs_index`, `cadsr_rest`, `ssis_facade`, `ssis_sparql` | A4.1 |
| `servedBy` | Where the answer came from: one of `live`, `cache`, `index`, `fixture` | A4.1 |
| `retrievedAt` | When it was retrieved, ISO-8601 | A4.1 |
| `sourceUri` | The upstream URL that produced the item | A4.1 |
| `correlationId` | The call's correlation identifier | M7.1 |
| `graphs` | For items served by the Shared SI Service: the identifier and date of each graph touched | A1.5 |
| `upstream` | The fields the platform supplied, passed through unchanged | A4.3 |

### The provenance record of an item reached by traversal

The provenance record, with these fields added (A4.2).

| Field | Content | Rule |
|---|---|---|
| `depth` | Steps from the concept the caller asked about; an edge has the depth of the item it reaches | A4.2 |
| `relationship` | The relationship that brought the item in: { code, name, kind }; the code decides polarity | A5.7 |
| `direction` | Whether the assertion points outward from the origin or inward to it | A4.2 |
| `polarity` | Positive or negative: one of `positive`, `negative` | A5.6 |
| `qualifiers` | Any qualifying detail the platform attaches | A4.2 |
| `evidence` | Supporting evidence, where the platform supplies it | A4.2 |

### The error record

A failed call returns { error } as its structuredContent, with isError set (M3.2); it never resembles an empty result (A2.5).

| Field | Content | Rule |
|---|---|---|
| `code` | The class of the failure: those A2.5 names, a release mismatch (A3.4), a timeout, a capability not yet available (M1.2) and a cursor whose release is superseded (M2.4): one of `invalid_request`, `not_found`, `release_not_available`, `release_mismatch`, `upstream_unavailable`, `timeout`, `bound_exceeded`, `capability_unavailable`, `cursor_expired`, `internal_error` | A2.5 |
| `message` | What failed, in words | A2.5 |
| `details` | An object holding what the caller needs for its next step, such as the release requested and the release served, the bound, its limit and the amount reached, or the surface, status and attempts of a failed upstream request (optional) | A2.5 |
| `correlationId` | The call's correlation identifier | M7.1 |

## 2. Tools

### EVS tools

| Tool | Inputs → result | What it does |
|---|---|---|
| `resolve_release` | `(terminology, channel?) → { terminology, channel, version, date, alternatives[] }` | Which release of a terminology is current, by channel; called explicitly, and its answer is passed to every later call. Items: `.`. |
| `get_concept` | `(terminology, release, code, include[]?) → concept` | One concept with the detail selected. `include`: synonyms, definitions, properties, semanticType, status. Items: `.`. |
| `get_concepts` | `(terminology, release, codes[], include[]?) → { concepts[], missing[] }` | Many concepts in one platform call, with the detail selected. Items: `concepts[]`. |
| `search_concepts` | `(terminology, release, query, mode?, limit?, cursor?) → { results[{ concept, score?, matchedOn? }], nextCursor? }` | Ranked search of a terminology; semantic and hybrid from the interim NCIt index (M4.1). `mode`: lexical, typeahead, semantic, hybrid. Items: `results[].concept`. |
| `get_concept_hierarchy` | `(terminology, release, code, direction, depth?, limit?, cursor?) → { nodes[], truncation }` | A concept's parents, children or paths to the root, bounded. `direction`: parent, child, pathsToRoot. Items: `nodes[]`. |
| `expand_value_set` | `(terminology, release, valueSet \| code, count?, offset?, activeOnly?) → { members[], total?, truncation }` | The members of a value set, paged and bounded by the tool itself; $lookup, $validate-code, $subsumes and $translate where a caller asks. Items: `members[]`. |
| `get_concept_neighborhood` | `(terminology, release, code, depth?, kinds[]?, maxNodes?, maxEdges?, budgetPerKind?, includeNegative?) → { nodes[], edges[], truncation }` | Bounded traversal across roles and associations with a budget per kind; negative assertions returned marked, left out of positive expansion unless includeNegative. `kinds`: parent, child, role, association, inverseRole, inverseAssociation. Items: `nodes[]`, `edges[]`. |
| `get_concept_subsets` | `(terminology, release, code) → { subsets[] }` | The subsets and value sets a concept belongs to. Items: `subsets[]`. |
| `get_concept_mappings` | `(terminology, release, code, targetTerminology?) → { mappings[] }` | A concept's mappings to other terminologies, each mapset with its own name and version. Items: `mappings[]`. |
| `resolve_retired_code` | `(terminology, release, code) → { code, terminology, status, replacements[] }` | Whether a code is retired, and what replaces it. Items: `.`, `replacements[]`. |
| `list_relationships` | `(terminology, release) → { relationships[{ code, name, kind, polarity, axisFamily }] }` | The relationship catalogue of a release, polarity marked by code. Items: `relationships[]`. |
| `list_terminologies` | `() → { terminologies[] }` | The terminologies available, with their current releases. Items: `terminologies[]`. |

### caDSR tools

| Tool | Inputs → result | What it does |
|---|---|---|
| `resolve_registry_release` | `() → { identifier, generatedAt, sourceDistribution }` | The registry's content state; no identifier is invented where caDSR publishes none. |
| `get_data_element` | `(publicId \| longName \| questionText, version?, include[]?) → dataElement` | One data element with the detail selected. `include`: permissibleValues, valueDomain, conceptAssociations, alternateNames, provenance. |
| `search_data_elements` | `(query, mode?, filters?, limit?, cursor?) → { results[{ dataElement, score?, matchedOn? }], nextCursor?, totalKnown? }` | Search of data elements, filtered by context, status and value-domain type. `mode`: lexical, semantic, hybrid. `filters`: context, workflowStatus, registrationStatus, valueDomainType. |
| `match_data_elements` | `(entities[{ name, userTip?, permissibleValues[]? }], matchLimit?, modelVariant?, similarityThreshold?, filters?, cursor?) → { matches[{ dataElement, score, rule, matchedText }], nextCursor? }` | Data elements matched to described entities, keyword and AI-enhanced, scored. |
| `match_value_meanings` | `(values[], strictness?, terminologyScope?, cursor?) → { matches[{ valueMeaning, score, crosswalk[] }], nextCursor? }` | Value meanings matched to values, with crosswalk codes. |
| `get_form` | `(publicId? \| keyword?, version?, includeModules?) → form` | A form or case report form with its modules and questions. |
| `get_permissible_value` | `(permissibleValueId) → permissibleValue` | A permissible value by its identifier. |
| `get_code_map` | `(sourceSystem?, targetContext?, dataElementId?, cursor?) → { codeMaps[], nextCursor? }` | Code maps between source code systems and registered value sets, the CRDC crosswalk included. |
| `list_contexts` | `(cursor?) → { contexts[], nextCursor? }` | The registry's contexts. |
| `list_classification_schemes` | `(context?, cursor?) → { classificationSchemes[], nextCursor? }` | Classification schemes as objects with their nested items. |

### Cross-domain tools

| Tool | Inputs → result | What it does |
|---|---|---|
| `find_data_elements_for_concept` | `(conceptCode, terminology?, release?, expandDescendants?, includePermissibleValues?, limit?, cursor?) → { dataElements[], truncation }` | The data elements and permissible values that use a concept, optionally across its descendants. |
| `get_concept_for_permissible_value` | `(permissibleValueId \| { dataElementId, value }) → concept` | The concept a permissible value stands for. |
| `resolve_stored_value` | `(conceptCode, commons, dataElementId?) → { storedValues[], confidence, evidence }` | The literal a data commons stores for a concept. |
| `get_release_alignment` | `() → { datasets[{ name, version, date }], maxIntervalDays, warning? }` | The release of every dataset a cross-domain answer touches, and the interval between them. |

### Workflow tools

| Tool | Inputs → result | What it does |
|---|---|---|
| `ground_value` | `(conceptCode \| text, commons?, release, registryRelease) → { concept, dataElements[], permissibleValues[], storedValues[], provenance, truncation }` | Concept to data element to permissible value to stored value, under one provenance envelope naming both content states. |
| `expand_cohort` | `(conceptCode, release, maxDepth?, includeNegative?, maxNodes?) → { codes[], excluded[], edges[], truncation, provenance }` | The codes a cohort query should use, exclusion assertions withheld and listed. |
| `harmonize_data_dictionary` | `(columns[{ name, description?, sampleValues[]? }], registryRelease, filters?) → { columns[{ matches[{ dataElement, score, rule }], permissibleValueAlignment }], unmatched[], provenance }` | A data dictionary's columns matched to data elements, with permissible-value alignment. |

## 3. Requirements

### Protocol gates: once per server, before any tool test

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| P-1 | tools/list names every tool of the profile under test, and no other. | M1.2, M1.5 | `tests/test_protocol.py::test_tools_list_names_the_tools_of_the_profile_and_no_other` | tested |
| P-2 | Every tool declares an outputSchema that is valid JSON Schema and covers the error record, admitting one and refusing one with a code outside its closed set or with no code. | M3.1, error | `tests/test_protocol.py::test_every_output_schema_admits_the_error_record_and_refuses_a_malformed_one` | tested |
| P-3 | No tool description, nor any description in a tool's schemas, contains placeholder, debug or development text. | A2.4, A10.3 | `tests/test_protocol.py::test_no_description_holds_placeholder_or_debug_text` | tested |
| P-4 | Tool names are verb-led, lowercase and underscore-separated. | A2.1 | `tests/test_protocol.py::test_tool_names_are_verb_led_lowercase_and_underscore_separated` | tested |
| P-5 | tools/list carries a positive ttlMs and cacheScope public. | M2.1, M2.2 | `tests/test_protocol.py::test_tools_list_may_be_cached_and_shared` | tested |
| P-6 | tools/list is the same after calls that pin a terminology and release, and while the platform is unavailable. | M1.2 | `tests/test_protocol.py::test_tools_list_is_the_same_after_a_call_that_pins_a_terminology_and_release`, `tests/test_protocol.py::test_tools_list_is_the_same_while_the_platform_is_unavailable` | tested |
| P-7 | A correlation identifier passed on a call is sent upstream on every platform request the call makes, and returned in the provenance of each item. | A6.2, M7.1 | `tests/test_protocol.py::test_a_correlation_identifier_goes_upstream_and_comes_back` | tested |
| P-8 | prompts/list and resources/list list the furnished prompts and resources with their arguments, and a prompt names only tools present in the profile. | M5.1 | — | planned #60 |
| P-9 | resources/read results carry ttlMs, cacheScope and a provenance record. | M2.1, A4.1, M5.1 | — | planned #60 |
| P-10 | Every tool declares readOnlyHint true, destructiveHint false, idempotentHint true and openWorldHint true. | M1.4 | `tests/test_protocol.py::test_every_tool_is_annotated_read_only_idempotent_and_open_world` | tested |
| P-11 | No tool description shows an operator, wildcard, filter syntax or parameter value that a tool test shows unsupported. | A2.3, A10.3 | — | planned #53 |
| P-12 | Each tool takes the parameters the specification names for it, under those names, and requires exactly those not marked optional, so that equivalent operations of the two modules share parameter names. | A2.2 | `tests/test_protocol.py::test_each_tool_takes_the_parameters_the_specification_names` | tested |

### Cross-cutting: against every content-returning tool

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| X-1 | The release in every returned item's provenance equals the release requested. | A3.1, A3.3 | `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concept]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concepts]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[search_concepts]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[expand_value_set]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concept_subsets]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[get_concept_mappings]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[resolve_retired_code]`, `tests/test_crosscutting.py::test_every_item_carries_the_release_requested[list_relationships]` | planned #55 |
| X-2 | A request for a release the platform does not serve fails closed, with the requested-release-not-available error and no content. | A3.4, A3.5 | — | planned #52 |
| X-3 | Content the upstream serves from another release than the one requested fails closed, with the mismatch error. | A3.4 | — | planned #52 |
| X-4 | A query that legitimately matches nothing returns an empty result with provenance, not an error. | A2.5, A2.6, M3.2 | — | planned #52 |
| X-5 | An upstream failure, including one masked as a successful response, is an upstream error, never an empty success. | A2.5, M3.2 | — | planned #52 |
| X-6 | structuredContent validates against the tool's declared outputSchema, for successes and errors alike. | M3.1 | `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[resolve_release]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concept]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concepts]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[search_concepts]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[expand_value_set]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concept_subsets]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[get_concept_mappings]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[resolve_retired_code]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[list_relationships]`, `tests/test_crosscutting.py::test_a_result_validates_against_the_declared_output_schema[list_terminologies]` | planned #52 |
| X-7 | Every item carries release, source surface, retrieval time and servedBy, and, where reached by traversal, depth, relationship, direction and polarity. | A4.1, A4.2, A4.4, provenance, traversal | `tests/test_crosscutting.py::test_every_item_carries_its_provenance[resolve_release]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concept]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concepts]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[search_concepts]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[expand_value_set]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concept_subsets]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[get_concept_mappings]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[resolve_retired_code]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[list_relationships]`, `tests/test_crosscutting.py::test_every_item_carries_its_provenance[list_terminologies]`, `tests/test_crosscutting.py::test_an_item_reached_by_traversal_says_how[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_an_item_reached_by_traversal_says_how[get_concept_neighborhood]` | planned #55 |
| X-8 | Fields the upstream supplied appear unchanged in provenance; none is dropped or renamed. | A4.3, provenance | — | planned #52 |
| X-9 | A code is returned bare, with its terminology in a separate field; never with an embedded prefix or as a URI. | A1.2, A1.3 | `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[resolve_release]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concept]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concepts]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[search_concepts]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[expand_value_set]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concept_subsets]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[get_concept_mappings]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[resolve_retired_code]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[list_relationships]`, `tests/test_crosscutting.py::test_codes_are_bare_with_their_terminology_beside_them[list_terminologies]` | planned #55 |
| X-10 | With a caller limit smaller than the result, truncation is reported with the bound reached and the magnitude omitted. | A5.1, A5.4 | — | planned #52 |
| X-11 | No upstream endpoint is called twice with identical parameters within one tool call. | A5.8 | `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[resolve_release]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concept]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concepts]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[search_concepts]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[expand_value_set]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concept_subsets]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[get_concept_mappings]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[resolve_retired_code]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[list_relationships]`, `tests/test_crosscutting.py::test_no_upstream_request_is_repeated_within_a_call[list_terminologies]` | planned #55 |
| X-12 | The licence key reaches the upstream request and appears in no log, error message or result. | A7.5 | — | planned #52 |
| X-13 | Every release-pinned result carries a positive ttlMs in its _meta, with cacheScope public for governed content and private for results computed from caller-supplied values. | M2.2, M2.3, M2.5 | `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concept]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concepts]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[search_concepts]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[expand_value_set]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concept_subsets]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[get_concept_mappings]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[resolve_retired_code]`, `tests/test_crosscutting.py::test_a_release_pinned_result_may_be_cached[list_relationships]` | planned #55 |
| X-14 | Every result is a JSON object, its lists of items named fields of it. | M3.3 | `tests/test_crosscutting.py::test_a_result_is_an_object[resolve_release]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concept]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concepts]`, `tests/test_crosscutting.py::test_a_result_is_an_object[search_concepts]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concept_hierarchy]`, `tests/test_crosscutting.py::test_a_result_is_an_object[expand_value_set]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concept_neighborhood]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concept_subsets]`, `tests/test_crosscutting.py::test_a_result_is_an_object[get_concept_mappings]`, `tests/test_crosscutting.py::test_a_result_is_an_object[resolve_retired_code]`, `tests/test_crosscutting.py::test_a_result_is_an_object[list_relationships]`, `tests/test_crosscutting.py::test_a_result_is_an_object[list_terminologies]` | planned #55 |

### EVS tools

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| resolve_release-1 | One release per channel, resolved by tag and never by the first latest row; with two latest rows and no channel it fails closed; ttlMs 0. | resolve_release, A3.6, M2.2 | — | planned #53 |
| get_concept-1 | Each include value returns its section and nothing else; descendants is refused as an include value. | get_concept | — | planned #53 |
| get_concepts-1 | A batch of n codes is one upstream call; a code the upstream omits is named in the result; results are in request order or keyed by code, never positional. | get_concepts, A5.8 | — | planned #53 |
| search_concepts-1 | lexical and typeahead return ranked results with matchedOn; semantic and hybrid return a score and the field matched from the interim index, its release in provenance; a mode the profile does not offer is an invalid request, not an empty result; an index built for another release fails closed. | search_concepts, M4.1, A3.4 | — | planned #53 |
| get_concept_hierarchy-1 | The depth bound holds, pathsToRoot returns complete paths, and truncation is reported. | get_concept_hierarchy, A5.1 | — | planned #53 |
| expand_value_set-1 | The tool applies count, offset and activeOnly itself, which the upstream ignores, and reports the total; activeOnly leaves out retired members. | expand_value_set, A5.1 | — | planned #53 |
| get_concept_neighborhood-1 | Per-kind budgets hold and no kind is starved; exclusion edges are marked and left out of positive expansion unless includeNegative; polarity follows the role code; the outbound budget counts retries; truncation names cause and magnitude. | get_concept_neighborhood, A5.3, A5.4, A5.5, A5.6, A5.7 | — | planned #53 |
| get_concept_subsets-1 | Membership is returned with subset codes, and the GDC Value Terminology subset resolves. | get_concept_subsets | — | planned #53 |
| get_concept_mappings-1 | Mapsets are first-class objects; licensed targets carry attribution; a mapset whose version is not the NCIt release is a content state of its own. | get_concept_mappings, A7.3 | — | planned #53 |
| resolve_retired_code-1 | A retired code returns status retired with its replacement and an active code status active; a batch fails as a whole on one bad code only where the upstream does, and says which. | resolve_retired_code, A8 | — | planned #53 |
| list_relationships-1 | The pinned release's roles are listed, the exclusion roles with negative polarity, derived from the code and not the name. | list_relationships, A5.6, A5.7 | — | planned #53 |
| list_terminologies-1 | The available terminologies are listed with their current releases, none the platform offers left out. | list_terminologies, A7.2 | — | planned #53 |

### caDSR tools

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| resolve_registry_release-1 | Without a registry release identifier upstream, the export date is returned and the absence stated, never an invented identifier; with one, it is returned; ttlMs 0. | resolve_registry_release, A3.8, M2.2 | — | planned #54 |
| get_data_element-1 | Each include returns its section; the data element's own version and status are surfaced; the caller may pin an item version. | get_data_element, A3.8.3 | — | planned #54 |
| search_data_elements-1 | Filters by context, workflow status, registration status and value-domain type apply; totalKnown is present where the upstream counts; truncation at the upstream cap is reported. | search_data_elements, A5.4, M6.1 | — | planned #54 |
| match_data_elements-1 | Matches are scored and rule-attributed; modelVariant and similarityThreshold are honoured or refused as an invalid request, never ignored; a slow upstream (28.9 s measured) is answered within the tool's declared timeout, and beyond it the error is a structured timeout. | match_data_elements | — | planned #54 |
| match_value_meanings-1 | As match_data_elements, for value meanings, with crosswalk codes per match. | match_value_meanings | — | planned #54 |
| get_form-1 | A form retrieved by public id returns its modules and questions; a keyword search works or is an invalid request stating that the upstream needs an identifier. | get_form | — | planned #54 |
| get_permissible_value-1 | The value is returned with its NCIt concept and the absence of a stable identifier stated; where an identifier exists, retrieval by it works. | get_permissible_value | — | planned #54 |
| get_code_map-1 | The crosswalk is returned per commons with coverage per node; a commons without value-level binding says so rather than returning empty. | get_code_map | — | planned #54 |
| list_contexts-1 | Contexts come from the registry's context names; classification schemes are first-class objects with their nested items (list_contexts, list_classification_schemes). | list_contexts, list_classification_schemes, A7.2 | — | planned #54 |

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
