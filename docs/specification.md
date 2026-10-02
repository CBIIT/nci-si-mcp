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
| A1.2 | A concept code is exchanged as the bare code the terminology publishes (C4817), with the terminology in a separate field; never with an embedded prefix, URI expansion or terminology punctuation as its primary form. |
| A1.3 | A code of the other module's domain is returned in the A1.2 form, ready to pass to the other module unchanged. |
| A1.4 | Neither module needs the other to be present to work. |
| A1.5 | Content obtained from the Shared Semantic Infrastructure Service says so in its provenance, with the release identity of both source graphs. |

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

### A3 · Release identity

| Id | Convention |
|---|---|
| A3.1 | Every platform request names the release explicitly; resolving it once at start-up does not count. |
| A3.2 | Unversioned platform access is used only to discover which releases exist. |
| A3.3 | The release in effect is recorded in the provenance of every result. |
| A3.4 | A cache or index derived from platform content is bound to its release; any mismatch between the release requested, the release the content reports and the release of derived content fails closed with a structured error. |
| A3.5 | Acceptance tests show fail-closed behaviour for each mismatch of A3.4. |
| A3.6.1 | The NCIt release is resolved by tag (monthly), never by the latest flag. |
| A3.6.2 | Monthly and weekly builds are told apart by tag, and the choice is explicit in configuration. |
| A3.6.3 | A release with conflicting or duplicate tags fails closed and is reported in a form fit to send upstream. |
| A3.7.1 | A query of the Shared SI Service reports the release identity of each graph it touched and the interval between them, with a warning above a configured threshold. |
| A3.8.1 | caDSR publishes no registry release identifier, and none is implied; a data element's version is never presented as the registry's state. |
| A3.8.2 | Until a registry release identifier exists, the most specific provenance the platform gives (a generation timestamp) is surfaced, and registry-level reproducibility is documented as not achievable. |
| A3.8.3 | Every data element is returned with its own version and registration status. |

### A4 · Provenance and citation

| Id | Convention |
|---|---|
| A4.1 | Every item carries its canonical identifier and, where published, its status as fields of the item, and a camelCase provenance record of at least release, source and retrievedAt. |
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
| A5.5 | Traversal over several relationship kinds does not spend its budget on one kind at the expense of another, and reports truncation per kind. |
| A5.6 | Negative assertions are kept, marked with negative polarity and full provenance, and left out of positive expansion by default, which the caller can override. |
| A5.7 | Negative assertions are identified by relationship code against the release's relationship catalogue, never by the relationship's name. |
| A5.8 | The same information is not retrieved twice within one operation. |

### A6 · Audit context, correlation and telemetry

| Id | Convention |
|---|---|
| A6.0 | The authoritative audit record, rate limits and quotas are the platform's; the module supplies what only it knows. |
| A6.1 | Every tool call emits telemetry of timestamp, tool, parameters, release, outbound requests with retries, result size, truncation, response code and elapsed time. |
| A6.2 | A caller's correlation identifier is accepted, recorded on every telemetry entry, passed upstream on every platform request and returned; one is generated where none is supplied. |
| A6.2a | The platform is given the audit context only the module knows (tool, consumer, workflow correlation) through the parameters or headers it defines. |
| A6.3 | Log records share one structured format and field vocabulary across both modules. |
| A6.4 | Telemetry records no more content than needed, and no caller free text where a hash suffices. |
| A6.5 | The module does not rate-limit in place of the platform; it honours 429 and Retry-After with jittered back-off and counts retries against its budget. |

### A7 · Identifiers, terminologies and scoping

| Id | Convention |
|---|---|
| A7.1 | Terminology and context identifiers come from the platform's metadata, never hard-coded or guessed. |
| A7.2 | A tool lists the terminologies, contexts or classification schemes available, with identifiers and current releases. |
| A7.3 | Results from a terminology with licence conditions carry its attribution. |
| A7.4 | A credentialed operation degrades explicitly when the credential is absent and is never presented as public. |
| A7.5 | A credential or licence key reaches only the platform request that needs it, and appears in no log, error message or result. |

### A8 · Content status and retired identifiers

| Id | Convention |
|---|---|
| A8.1 | An item's published status (retired, obsolete, provisional) is always surfaced. |
| A8.2 | Replacement and history of a retired identifier are exposed, and a retired identifier never resolves silently as current. |
| A8.3 | How search and expansion treat retired content is documented and caller-overridable. |

### A9 · The lean module

| Id | Convention |
|---|---|
| A9.1 | The module is a thin interface over its platform; it returns results unmodified in substance. |
| A9.2 | Without a written amendment naming the capability, the module keeps no embeddings, search index or persistent copy of platform content, computes no ranking or score, and derives no content (the interim NCIt index is that amendment, MCP API §5.5). |
| A9.3 | A capability that needs more is a platform dependency, recorded as such. |
| A9.4 | Request-scoped caching and caching bound to a release under A3.4 are allowed. |
| A9.5 | An absent platform capability is shown to the caller, never compensated for silently. |
| A9.6 | Reading from a governed SI service that joins or scores is allowed; the module does not own such capability. |

### A10 · Joint conformance and acceptance

| Id | Convention |
|---|---|
| A10.1 | A joint conformance test spans both modules and checks A1.2, A3, A4, A5.4 and correlation propagation. |
| A10.3 | No tool description contains placeholder text or an unsupported operator, parameter or syntax example. |
| A10.4 | Response-time criteria are relative to the platform's capability at delivery. |

### A11 · Performance measurement

| Id | Convention |
|---|---|
| A11.1 | A benchmark reports, per representative scenario, release, cache state, result size, outbound calls, error rate and p50/p95 response time. |
| A11.2 | The benchmark reports what the platform supports and what it does not. |
| A11.3 | The scenarios include a workflow across both modules, measured end to end. |

### M1 · A static, self-describing tool surface

| Id | Convention |
|---|---|
| M1.1 | Each tool description stands alone, naming its domain, its object and how it pins a release, without reference to another tool. |
| M1.2 | The tool surface does not vary with the terminology, the release pinned or what the platform offers today; a capability not yet available is a tool returning a structured unavailable error, never an absent tool. |
| M1.3 | Each tool's group (terminology, metadata, cross-domain, workflow) is carried in its metadata. |
| M1.4 | Every tool declares the read-only annotations readOnlyHint true, destructiveHint false, idempotentHint true and openWorldHint true (owner decision, 2 October 2026). |

### M2 · Caching hints

| Id | Convention |
|---|---|
| M2.1 | tools/list, resources/list and resources/read carry ttlMs and cacheScope. |
| M2.2 | A release-pinned result has a long ttlMs, an unpinned one a short ttlMs, and resolve_release, resolve_registry_release and get_release_alignment ttlMs 0; tools/list is long and public. |
| M2.3 | cacheScope is public for governed content and private for results computed from caller-supplied values. |
| M2.4 | A cursor pins the release it was issued against; presented after that release is superseded, it is a structured error. |

### M3 · Typed results

| Id | Convention |
|---|---|
| M3.1 | Every tool declares an outputSchema covering its success and its error shape. |
| M3.2 | A platform failure, including one wrapped in an HTTP 200, is a result with isError; a genuine empty result has its provenance and no isError. |

### M4 · The interim NCIt index

| Id | Convention |
|---|---|
| M4.1 | NCIt semantic and hybrid search are served from a release-bound local index (the amendment A9.2 asks for) until EVS serves them; the index's release is in provenance, and the index is activated and rolled back atomically. |

### M5 · Prompts and resources

| Id | Convention |
|---|---|
| M5.1 | The furnished prompt templates and resources are listed by prompts/list and resources/list with their arguments, and a prompt names only tools present in the profile. |

## 2. Tools

### A · Terminology

| Tool | Inputs → result | What it does |
|---|---|---|
| `resolve_release` | `(terminology, channel?) → { terminology, channel, version, date, alternatives[] }` | Which release of a terminology is current, by channel; called explicitly, and its answer is passed to every later call. |
| `get_concept` | `(terminology, release, code, include[]?) → concept` | One concept with the detail selected. `include`: synonyms, definitions, properties, semanticType, status. |
| `get_concepts` | `(terminology, release, codes[], include[]?) → concept[]` | Many concepts in one platform call, with the detail selected. |
| `search_concepts` | `(terminology, release, query, mode?, limit?, cursor?) → { results[{ concept, score?, matchedOn? }], nextCursor? }` | Ranked search of a terminology; semantic and hybrid from the interim NCIt index (M4.1). `mode`: lexical, typeahead, semantic, hybrid. |
| `get_concept_hierarchy` | `(terminology, release, code, direction, depth?, limit?, cursor?) → { nodes[], truncation }` | A concept's parents, children or paths to the root, bounded. `direction`: parent, child, pathsToRoot. |
| `expand_value_set` | `(terminology, release, valueSet \| code, count?, offset?, activeOnly?) → { members[], total?, truncation }` | The members of a value set, paged and bounded by the tool itself; $lookup, $validate-code, $subsumes and $translate where a caller asks. |
| `get_concept_neighborhood` | `(terminology, release, code, depth?, kinds[]?, maxNodes?, maxEdges?, budgetPerKind?, includeNegative?) → { nodes[], edges[], truncation }` | Bounded traversal across roles and associations with a budget per kind; negative assertions returned marked, left out of positive expansion unless includeNegative. `kinds`: parent, child, role, association, inverseRole, inverseAssociation. |
| `get_concept_subsets` | `(terminology, release, code) → subset[]` | The subsets and value sets a concept belongs to. |
| `get_concept_mappings` | `(terminology, release, code, targetTerminology?) → mapping[]` | A concept's mappings to other terminologies, each mapset with its own name and version. |
| `resolve_retired_code` | `(terminology, release, code) → { status, replacements[] }` | Whether a code is retired, and what replaces it. |
| `list_relationships` | `(terminology, release) → relationship[{ code, name, kind, polarity, axisFamily }]` | The relationship catalogue of a release, polarity marked by code. |
| `list_terminologies` | `() → terminology[]` | The terminologies available, with their current releases. |

### B · Metadata

| Tool | Inputs → result | What it does |
|---|---|---|
| `resolve_registry_release` | `() → { identifier, generatedAt, sourceDistribution }` | The registry's content state; no identifier is invented where caDSR publishes none. |
| `get_data_element` | `(publicId, version? \| longName? \| questionText?, include[]?) → dataElement` | One data element with the detail selected. `include`: permissibleValues, valueDomain, conceptAssociations, alternateNames, provenance. |
| `search_data_elements` | `(query, mode?, filters?, limit?, cursor?) → { results[{ dataElement, score?, matchedOn? }], nextCursor?, totalKnown? }` | Search of data elements, filtered by context, status and value-domain type. `mode`: lexical, semantic, hybrid. `filters`: context, workflowStatus, registrationStatus, valueDomainType. |
| `match_data_elements` | `(entities[{ name, userTip?, permissibleValues[]? }], matchLimit?, modelVariant?, similarityThreshold?, filters?, cursor?) → { matches[{ dataElement, score, rule, matchedText }], nextCursor? }` | Data elements matched to described entities, keyword and AI-enhanced, scored. |
| `match_value_meanings` | `(values[], strictness?, terminologyScope?, cursor?) → { matches[{ valueMeaning, score, crosswalk[] }], nextCursor? }` | Value meanings matched to values, with crosswalk codes. |
| `get_form` | `(publicId? \| keyword?, version?, includeModules?) → form` | A form or case report form with its modules and questions. |
| `get_permissible_value` | `(permissibleValueId) → permissibleValue` | A permissible value by its identifier. |
| `get_code_map` | `(sourceSystem?, targetContext?, dataElementId?, cursor?) → codeMap[]` | Code maps between source code systems and registered value sets, the CRDC crosswalk included. |
| `list_contexts` | `(cursor?) → context[]` | The registry's contexts. |
| `list_classification_schemes` | `(context?, cursor?) → classificationScheme[]` | Classification schemes as objects with their nested items. |

### C · Cross-domain

| Tool | Inputs → result | What it does |
|---|---|---|
| `find_data_elements_for_concept` | `(conceptCode, terminology?, release?, expandDescendants?, includePermissibleValues?, limit?, cursor?) → { dataElements[], truncation }` | The data elements and permissible values that use a concept, optionally across its descendants. |
| `get_concept_for_permissible_value` | `(permissibleValueId \| { dataElementId, value }) → concept` | The concept a permissible value stands for. |
| `resolve_stored_value` | `(conceptCode, commons, dataElementId?) → { storedValues[], confidence, evidence }` | The literal a data commons stores for a concept. |
| `get_release_alignment` | `() → { datasets[{ name, version, date }], maxIntervalDays, warning? }` | The release of every dataset a cross-domain answer touches, and the interval between them. |

### W · Workflow

| Tool | Inputs → result | What it does |
|---|---|---|
| `ground_value` | `(conceptCode \| text, commons?, release, registryRelease) → { concept, dataElements[], permissibleValues[], storedValues[], provenance, truncation }` | Concept to data element to permissible value to stored value, under one provenance envelope naming both content states. |
| `expand_cohort` | `(conceptCode, release, maxDepth?, includeNegative?, maxNodes?) → { codes[], excluded[], edges[], truncation, provenance }` | The codes a cohort query should use, exclusion assertions withheld and listed. |
| `harmonize_data_dictionary` | `(columns[{ name, description?, sampleValues[]? }], registryRelease, filters?) → { columns[{ matches[{ dataElement, score, rule }], permissibleValueAlignment }], unmatched[], provenance }` | A data dictionary's columns matched to data elements, with permissible-value alignment. |

## 3. Requirements

### Protocol gates: once per server, before any tool test

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| P-1 | tools/list names every tool of the profile under test, and no other. | M1.2 | `tests/test_protocol.py::test_tools_list_names_each_tool_with_its_input_schema` | planned #52 |
| P-2 | Every tool declares an outputSchema that is valid JSON Schema and covers both the success and the error shape. | M3.1 | — | planned #52 |
| P-3 | No tool description contains an operator, wildcard or parameter value a tool test shows unsupported, nor placeholder or debug text. | A2.3, A2.4 | — | planned #52 |
| P-4 | Tool names are verb-led, lowercase and underscore-separated, and equivalent operations share a name pattern across the EVS and caDSR modules. | A2.1, A2.2 | — | planned #52 |
| P-5 | tools/list carries ttlMs and cacheScope public; release discovery results carry ttlMs 0 and release-pinned results a positive ttlMs. | M2.1, M2.2 | — | planned #52 |
| P-6 | tools/list is identical whatever the terminology selected, the release pinned and the run mode. | M1.2 | — | planned #52 |
| P-7 | A correlation identifier supplied on a call appears in the result and in the upstream request log. | A6.2 | — | planned #52 |
| P-8 | prompts/list and resources/list list the furnished prompts and resources with their arguments, and a prompt names only tools present in the profile. | M5.1 | — | planned #60 |
| P-9 | resources/read results carry ttlMs, cacheScope and a provenance record. | M2.1, A4.1, M5.1 | — | planned #60 |
| P-10 | Every tool declares readOnlyHint true, destructiveHint false, idempotentHint true and openWorldHint true. | M1.4 | — | planned #52 |

### Cross-cutting: against every content-returning tool

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| X-1 | The release in every returned item's provenance equals the release requested. | A3.1, A3.3 | — | planned #52 |
| X-2 | A request for a release the platform does not serve fails closed, with the requested-release-not-available error and no content. | A3.4, A3.5 | — | planned #52 |
| X-3 | Content the upstream serves from another release than the one requested fails closed, with the mismatch error. | A3.4 | — | planned #52 |
| X-4 | A query that legitimately matches nothing returns an empty result with provenance, not an error. | A2.5, A2.6, M3.2 | — | planned #52 |
| X-5 | An upstream failure, including one masked as a successful response, is an upstream error, never an empty success. | A2.5, M3.2 | — | planned #52 |
| X-6 | structuredContent validates against the tool's declared outputSchema, for successes and errors alike. | M3.1 | — | planned #52 |
| X-7 | Every item carries release, source surface, retrieval time and servedBy, and, where reached by traversal, depth, relationship, direction and polarity. | A4.1, A4.2, A4.4 | — | planned #52 |
| X-8 | Fields the upstream supplied appear unchanged in provenance; none is dropped or renamed. | A4.3 | — | planned #52 |
| X-9 | A code is returned bare, with its terminology in a separate field; never with an embedded prefix or as a URI. | A1.2, A1.3 | — | planned #52 |
| X-10 | With a caller limit smaller than the result, truncation is reported with the bound reached and the magnitude omitted. | A5.1, A5.4 | — | planned #52 |
| X-11 | No upstream endpoint is called twice with identical parameters within one tool call. | A5.8 | — | planned #52 |
| X-12 | The licence key reaches the upstream request and appears in no log, error message or result. | A7.5 | — | planned #52 |

### Terminology tools

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| resolve_release-1 | One release per channel, resolved by tag and never by the first latest row; with two latest rows and no channel it fails closed; ttlMs 0. | resolve_release, A3.6 | — | planned #53 |
| get_concept-1 | Each include value returns its section and nothing else; descendants is refused as an include value. | get_concept | — | planned #53 |
| get_concepts-1 | A batch of n codes is one upstream call; a code the upstream omits is named in the result; results are in request order or keyed by code, never positional. | get_concepts, A5.8 | — | planned #53 |
| search_concepts-1 | lexical and typeahead return ranked results with matchedOn; semantic and hybrid return a score and the field matched from the interim index, its release in provenance; a mode the profile does not offer is an invalid request, not an empty result; an index built for another release fails closed. | search_concepts, M4.1, A3.4 | — | planned #53 |
| get_concept_hierarchy-1 | The depth bound holds, pathsToRoot returns complete paths, and truncation is reported. | get_concept_hierarchy, A5.1 | — | planned #53 |
| expand_value_set-1 | The tool applies count, offset and activeOnly itself, which the upstream ignores, and reports the total; activeOnly leaves out retired members. | expand_value_set, A5.1 | — | planned #53 |
| get_concept_neighborhood-1 | Per-kind budgets hold and no kind is starved; exclusion edges are marked and left out of positive expansion unless includeNegative; polarity follows the role code; the outbound budget counts retries; truncation names cause and magnitude. | get_concept_neighborhood, A5.5, A5.6, A5.7 | — | planned #53 |
| get_concept_subsets-1 | Membership is returned with subset codes, and the GDC Value Terminology subset resolves. | get_concept_subsets | — | planned #53 |
| get_concept_mappings-1 | Mapsets are first-class objects; licensed targets carry attribution; a mapset whose version is not the NCIt release is a content state of its own. | get_concept_mappings, A7.3 | — | planned #53 |
| resolve_retired_code-1 | A retired code returns status retired with its replacement and an active code status active; a batch fails as a whole on one bad code only where the upstream does, and says which. | resolve_retired_code, A8 | — | planned #53 |
| list_relationships-1 | The pinned release's roles are listed, the exclusion roles with negative polarity, derived from the code and not the name. | list_relationships, A5.6 | — | planned #53 |
| list_terminologies-1 | The available terminologies are listed with their current releases, none the platform offers left out. | list_terminologies, A7.2 | — | planned #53 |

### Metadata tools

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| resolve_registry_release-1 | Without a registry release identifier upstream, the export date is returned and the absence stated, never an invented identifier; with one, it is returned; ttlMs 0. | resolve_registry_release, A3.8 | — | planned #54 |
| get_data_element-1 | Each include returns its section; the data element's own version and status are surfaced; the caller may pin an item version. | get_data_element, A3.8.3 | — | planned #54 |
| search_data_elements-1 | Filters by context, workflow status, registration status and value-domain type apply; totalKnown is present where the upstream counts; truncation at the upstream cap is reported. | search_data_elements, A5.4 | — | planned #54 |
| match_data_elements-1 | Matches are scored and rule-attributed; modelVariant and similarityThreshold are honoured or refused as an invalid request, never ignored; a slow upstream (28.9 s measured) is answered within the tool's declared timeout, and beyond it the error is a structured timeout. | match_data_elements | — | planned #54 |
| match_value_meanings-1 | As match_data_elements, for value meanings, with crosswalk codes per match. | match_value_meanings | — | planned #54 |
| get_form-1 | A form retrieved by public id returns its modules and questions; a keyword search works or is an invalid request stating that the upstream needs an identifier. | get_form | — | planned #54 |
| get_permissible_value-1 | The value is returned with its NCIt concept and the absence of a stable identifier stated; where an identifier exists, retrieval by it works. | get_permissible_value | — | planned #54 |
| get_code_map-1 | The crosswalk is returned per commons with coverage per node; a commons without value-level binding says so rather than returning empty. | get_code_map | — | planned #54 |
| list_contexts-1 | Contexts come from the registry's context names; classification schemes are first-class objects with their nested items (list_contexts, list_classification_schemes). | list_contexts, list_classification_schemes, A7.2 | — | planned #54 |

### Cross-domain tools

| Id | Requirement | Basis | Tests | Status |
|---|---|---|---|---|
| find_data_elements_for_concept-1 | The batch form is used where available; descendant expansion is bounded and reported; where served from the Shared SI Service, both graphs' release identities are recorded; a masked upstream failure is an error. | find_data_elements_for_concept, A3.7 | — | planned #55 |
| get_concept_for_permissible_value-1 | Resolves to a concept with both release identities. | get_concept_for_permissible_value | — | planned #55 |
| resolve_stored_value-1 | A GDC value resolves through the mapset named in provenance; a node without a mapping reports no mapping with its coverage, never the preferred term as if stored. | resolve_stored_value | — | planned #55 |
| get_release_alignment-1 | Every dataset's release and date are returned with the interval, and a warning above the configured threshold; ttlMs 0. | get_release_alignment, A3.7 | — | planned #55 |

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

A module is accepted when no tool is FAIL and the gates pass. Every report names the suite
version, the fixture-set version, a digest over the suite, and the tools whose tests have never
run against an implementation.

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
Every suite test cites the requirements it enforces, and at the furnished tag every requirement
is cited by one; a report whose digest is not that of an approved release reads MODIFIED;
changes under `spec/` and `acceptance/` need a code owner's approval; and the change log records
the approval and the requirement each change serves.
