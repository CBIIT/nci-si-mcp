# Specification: from EVS-first prototype to the shared NCI Semantic Infrastructure MCP platform

**Status:** accepted for implementation · **Written:** 1 October 2026 · **Updated:** 2 October 2026, to the code on `main` after `v0.2.0` · **Baseline:** the commit tagged `baseline-2026-10` (§1.2)

The work is tracked on GitHub as one milestone per phase (§11) with one issue per deliverable; issues cite this document by section. Where a section describes the code "today", it means `main` at the *Updated* date above.

This document specifies how `nci_si_mcp` is extended from the current EVS-first prototype into the shared platform, EVS module and caDSR module that *Reconciled SOW v2.1 (EVS)* and *SOW v1.1 (caDSR)* describe, together with the government-furnished *MCP Behavioral Acceptance Suite* that is the acceptance instrument for both. It is written against the code as it is, so every change is stated as a delta from a named module, and it is ordered so that each phase leaves the repository releasable.

Governing documents, in precedence order where they differ: the two SOWs; *Platform API Specification* Part 6 (the shared conventions, cited below as A1–A11); *MCP API Specification* (the tool inventory and §8 client-execution requirements); *MCP Behavioral Acceptance Suite* (what the tests assert). They belong to the programme's document set and are not in this repository. Nothing here changes a requirement in those documents; where this document is more specific, it is because the code forces a decision they leave open.

---

## 1. Scope, baseline and ground rules

### 1.1 What is being built

| Deliverable | Content | Owner |
|---|---|---|
| **Shared platform** | Transport, authentication/authorization hooks, audit, common error and provenance envelopes, configuration, deployment profiles, release model, bounds, caching hints, `outputSchema` generation | this repository, `nci_si_mcp.platform` |
| **EVS module** | 12 terminology tools over EVS REST, EVS FHIR and the interim NCIt index | `nci_si_mcp.evs` |
| **caDSR module** | 10 metadata tools over the caDSR Open APIs | `nci_si_mcp.cadsr` |
| **Cross-domain** | 4 seam tools over the Shared SI Service and the two REST surfaces | `nci_si_mcp.seam` |
| **Workflow** | 3 composite tools | `nci_si_mcp.workflows` |
| **Acceptance suite** | Harness, fixture server, fixture set, baseline tool map, per-tool tests | `acceptance/` (separate package in this repository, separately versioned) |

Twenty-nine tools in total, named and typed exactly as in *MCP API Specification* §2–§4 and §8.4. Three profiles: `evs`, `cadsr`, `unified`. A profile determines which tools `tools/list` returns and nothing else; the surface within a profile is static (§8.1 there).

### 1.2 Baseline

Done:

- The hardening branch is merged (#1) and tagged `v0.1.0`. It changed traversal node codes, embedding-provider selection, the release check in `upsert_concepts` and the error module, and added `ARCHITECTURE.md` and CI.
- The migration to `mcp>=2.0,<3` is committed.
- Since `v0.1.0`: Python 3.13 and PDM (#43), every function below cyclomatic complexity 8 (#44), the lint and quality gates with the engineering standards in `CONTRIBUTING.md` (#45), and automatic releases from Conventional Commit pull request titles (#46). `v0.2.0` is the first automatic release.

Remaining (Phase 0):

- Add a `LICENSE` file and the `license` field in `pyproject.toml`. The package has neither, and it is the first thing a contractor's counsel checks.
- Tag the furnished commit `baseline-2026-10`. This is the commit SOW v2.1 §1 furnishes and the commit the baseline tool map (§9.4) is written against. It is a manual tag beside the automatic `vX.Y.Z` release tags, and it must contain the licence.

### 1.3 Ground rules carried forward from the current code

These hold today and continue to hold:

- The core has no runtime dependencies. `mcp` and `sentence_transformers` are optional extras, imported lazily; nothing at module level in `server.py` imports `mcp`. The supported Python is 3.13 and newer (owner decision of 1 October 2026; it replaces the earlier rule that the core stay importable on Python 3.9).
- The package version is derived from the git tag at build time and written in no file; `setup.py` is gone.
- Unit tests are `unittest.TestCase` classes run by pytest, offline, with hand-written doubles, and every change passes the gates in `CONTRIBUTING.md`. The acceptance suite is a separate package with pytest conventions of its own (fixtures, markers, a fixture server), so that the two do not meet.
- `cadsr.py` must never return fabricated CDE data. That rule survives, restated: **no tool returns content it did not retrieve from a platform or from a fixture that declares itself as such.**

### 1.4 Ground rules that change

| Today | After |
|---|---|
| Every concept request addresses `ncit_{release}` of the one current monthly release, and each concept payload's `version` is checked; the terminology and the release cannot be chosen. An unknown release is `evs_invalid_response` on the batch and descendants endpoints but `concept_not_found` on a single-concept lookup (a 404 there cannot be told from an unknown code), and a payload mismatch is `evs_invalid_response` | Every content request addresses `{terminology}_{release}` from the call's `ReleaseContext` explicitly (verified to work and to fail closed with 404 on an unknown release); the payload check becomes a second guard, not the only one; an unknown release and a payload mismatch are both `release_unavailable` (A3.1) |
| Expected exceptions are mapped to codes by one table (`_ERROR_CODES`, applied by `service._enveloped`); with the envelopes that the service, the CLI and the resources build directly, `ErrorCode` has fourteen values | One error model, one taxonomy of six classes (A2.5), one serialisation — every tool returns a structured error through the same path (§3.1) |
| `include_raw` and `live_only` are MCP tool parameters | Both are removed from the MCP surface. `include_raw` stays on the CLI for debugging; `live_only` becomes the `servedBy` field in provenance, reported rather than requested |
| Edge types are selected independently of relationship names, but the name filter also applies to hierarchy edges, which carry the pseudo-names `is_a_parent`, `is_a_child`, `is_a_descendant`: a role-name filter drops them unless those names are listed | Edge kinds and relationship names are separate fields, and hierarchy edges carry no invented name; a filter on one never silently removes the other (A5.5) |
| Exclusion polarity is not represented | Polarity is derived from the relationship **code** against the pinned release's catalogue, never from the label (A5.7) |
| Traversal bounds nodes and edges, nearest first; outbound requests are not counted | The budget counts outbound requests **including retries** (A5.3), with nodes and edges as additional bounds |
| `cadsr_status` is a stub | The caDSR module is real; the stub is deleted |


---

## 2. Package layout

```
src/nci_si_mcp/
  __init__.py
  config.py                 settings, profiles, upstream base URLs, timeouts, credentials source
  cli.py                    thin adapter; gains cadsr-* and seam commands
  server.py                 thin adapter; builds tools from the registry below, per profile

  platform/
    errors.py               the taxonomy (A2.5) and the single serialiser
    provenance.py           ProvenanceEnvelope, TraversalProvenance, Truncation (A4, A5.4)
    release.py              ReleaseContext: EVS release, caDSR registry state, SI graph identities (A3)
    bounds.py               Budget: outbound requests incl. retries, nodes, edges, per-kind (A5)
    http.py                 one instrumented HTTP client: retries, Accept, correlation header, request log hook
    audit.py                structured audit record per tool call (A6)
    caching.py              ttlMs / cacheScope policy per tool class (MCP API Spec §8.2)
    schema.py               outputSchema generation from the dataclasses; static-surface assertion
    registry.py             ToolSpec: name, group, profile, input model, output model, handler
    transport.py            stdio and streamable-HTTP entry points

  evs/
    client.py               was evs.py; release-pinned addressing; batch; FHIR; replacements; mapsets
    catalogue.py            relationship catalogue per release with polarity by code
    traversal.py            reworked walker (A5)
    index/                  was index.py, embeddings.py, retrieval.py, evaluation.py
      store.py              SQLite; per-release tables; manifests with embedding metadata
      activation.py         atomic activation and rollback (SOW v2 §3)
      build.py              full-NCIt build from the batch endpoint
      evaluate.py           retrieval evaluation set and scoring protocol
    tools.py                the 12 Group A tools

  cadsr/
    client.py               Data Element, Form 2.0, LOV, Model, CDE Match, VM Match, FTP listing
    registry_state.py       export date and item versions in lieu of a registry release (A3.8)
    tools.py                the 10 Group B tools

  seam/
    ssis.py                 Shared SI façade and SPARQL client; required-parameter enforcement
    tools.py                the 4 Group C tools

  workflows/
    tools.py                the 3 Group W tools

acceptance/                 separate package, see §9
```

`service.py` is retired. Its role — composing clients, index, traversal and adapter — is taken by `platform.registry`, which holds one `ToolSpec` per tool and is the single place `server.py` and `cli.py` read from. The parameter lists that `server.py` and `cli.py` each declare today disappear with it.

---

## 3. Shared platform

### 3.1 Error model (`platform/errors.py`)

Replace the fourteen-value `ErrorCode` literal in `errors.py`, and the `_ERROR_CODES` table in `service.py`, with the six classes A2.5 requires, each carrying a stable `code`, a human message, and structured `detail`:

| Class | Replaces | `detail` carries |
|---|---|---|
| `invalid_request` | `invalid_request`, `invalid_configuration` | parameter, reason |
| `not_found` | `concept_not_found`, `concepts_missing` | identifiers not found |
| `release_unavailable` | `release_unresolved`, `version_mismatch`, `release_not_active`, `index_not_active` | requested, served, source |
| `upstream_unavailable` | `evs_unavailable`, `evs_invalid_response` | surface, status, attempts, retry-after |
| `bound_exceeded` | — | bound, limit, reached |
| `internal` | `startup_failed`, `no_active_index`, `index_incompatible`, `index_storage_error` | — |

Two rules. **An empty result is never an error**: a tool that matched nothing returns its normal shape with an empty collection and a complete provenance envelope. **A platform failure carried inside a `2xx` body is an error**: the HTTP client (§3.4) recognises the webMethods envelope (`apiResponse.type == "E"`), FHIR `OperationOutcome` with severity `error`, and an HTML body where JSON was requested, and raises `upstream_unavailable` before any tool sees the payload.

Serialisation: every tool handler returns a dataclass or raises a `PlatformError`; the registry converts the latter to an MCP result with `isError: true` and `structuredContent` conforming to the error schema. No handler builds an `isError` dict itself.

### 3.2 Provenance (`platform/provenance.py`)

Extend `models.py`'s per-concept fields into one `ProvenanceEnvelope` attached **per item** (A4.4):

```
release          { terminology|registry, identifier, date }         — EVS: "26.08e"; caDSR: export date + "no registry identifier"
source           evs_rest | evs_fhir | evs_index | cadsr_rest | ssis_facade | ssis_sparql
servedBy         live | cache | index | fixture
retrievedAt      ISO-8601
sourceUri        the upstream URL that produced the item
correlationId
graphs[]         for SSIS-served items: one entry per graph touched, each with identifier and date (A1.5, A3.7.1)
```

`TraversalProvenance` adds `depth`, `relationship {code, name, kind}`, `direction`, `polarity`, `qualifiers`, `evidence`. Fields the upstream supplies are passed through unchanged under `upstream` (A4.3). The `raw` payload is dropped from MCP results and kept only behind the CLI flag.

`Truncation` carries `occurred`, `bound` (`results` | `depth` | `nodes` | `edges` | `requests` | `upstream_cap`), `limit`, `reached`, `omitted` (count or `unknown`), and `perKind` where traversal is involved (A5.4).

### 3.3 Release model (`platform/release.py`)

`ReleaseContext` is resolved once per tool call and threaded through every upstream request. It is never resolved implicitly inside another tool — `resolve_release` and `resolve_registry_release` are the only discovery operations and the only unversioned upstream calls (A3.2).

**EVS.** `resolve_evs_release(terminology, channel)` calls `/metadata/terminologies?terminology=…&latest=true&tag={channel}` and requires exactly one row. This replaces `select_monthly_ncit_release`, which already requires exactly one latest monthly row but filters the full listing itself; `latest` is channel-scoped, and the one-row query moves the selection upstream. A `ReleaseResolutionError` is raised only if the row count is not one. Content requests then address `/concept/{terminology}_{release}/…`; a 404 with `Terminology not found` maps to `release_unavailable`. The payload's `version` is compared as a second guard and a mismatch is `release_unavailable`, never silently accepted.

**caDSR.** `resolve_registry_state()` returns `{registryIdentifier: null, exportDate, itemVersioning: "per data element"}`, with `exportDate` read from the FTP listing's `Last-Modified` for `releasedCDEsXML-OD.zip`. The module never fabricates a registry identifier (A3.8.1). When a registry identifier appears upstream (C-1), the field becomes required and the same fail-closed path applies; the code path is written now and gated on the field being non-null.

**Shared SI.** Every SSIS call records the identity of each graph it touched, read once per process from the content graphs' `owl:versionInfo` / `dc:date` (two queries, no property paths — the WAF rejects them) and cached with the SSIS release-alignment TTL (§3.6).

### 3.4 HTTP client (`platform/http.py`)

One client for all surfaces, replacing `EVSClient._get_json` and the per-module ad hoc calls:

- Always sends `Accept: application/json` (several caDSR routes return HTML otherwise) and the correlation header.
- Retries only `5xx` and connection errors, with jittered backoff, **and counts every attempt against the call's `Budget`** (A5.3). Honours `Retry-After` on `429`.
- Classifies the response before returning it (§3.1): status, content type, webMethods envelope, FHIR `OperationOutcome`.
- Exposes a request-log hook. In production it feeds the audit record; under the acceptance suite it is what the fixture server mirrors.
- Holds credentials (licence keys, any caDSR credential) from configuration and never logs them.

### 3.5 Bounds (`platform/bounds.py`)

`Budget(requests, nodes, edges, perKind)` is created per tool call from caller limits clamped to documented maxima, decremented by the HTTP client (requests) and the traverser (nodes, edges, per kind), and reported in `Truncation` when any bound is reached. `clamp_limits`, `clamp_edge_limit` and the `HARD_MAX_*` constants (depth 4, 1,000 nodes, 5,000 edges) move here from `traversal.py`. Per-kind budgeting (A5.5) means the walker takes one unit from each relationship kind in rotation until a kind is exhausted, so an ordering never starves a kind.

### 3.6 Caching hints (`platform/caching.py`)

| Tool class | `ttlMs` | `cacheScope` |
|---|---|---|
| `tools/list` | 86,400,000 | public |
| `resolve_release`, `resolve_registry_release`, `get_release_alignment` | **0** | public |
| any release-pinned content read | 86,400,000 | public |
| unpinned read (only the discovery tools qualify) | 0 | public |
| results computed over caller-supplied content (`match_*`, `harmonize_data_dictionary`, `validate`-style results) | 0 | **private** |

A cursor encodes the release it was issued against; presenting it after that release is superseded returns `release_unavailable` (MCP API Spec §8.2).

### 3.7 Schema generation (`platform/schema.py`)

`outputSchema` is generated from the result dataclasses for every tool, success and error shapes alike, and checked by a unit test that renders `tools/list` for each profile and validates every schema. A second test asserts the rendered surface is byte-identical across terminology, release and upstream mode (static surface, *MCP API Specification* §8.1). A third asserts no description contains placeholder text or an operator a tool test shows unsupported (A2.3, A2.4).

### 3.8 Transport (`platform/transport.py`)

stdio stays. Add the NCI-approved remote transport — streamable HTTP in `mcp>=2.0` — behind the same registry. Authentication and authorisation are hooks on the transport layer with a no-op default; the SOW leaves the mechanism to NCI approval, and the hook is what lets it be supplied without touching tools.

### 3.9 Audit (`platform/audit.py`)

One structured record per tool call: correlation id, timestamp, tool, target (terminology or context), release context, status, outbound request count including retries, latency, truncation. Free text, credentials and licence keys are redacted at the record boundary, not by each tool.

---

## 4. EVS module

### 4.1 Client (`evs/client.py`)

Delta from `evs.py`:

| Method | Change |
|---|---|
| `get_concept`, `get_concepts_by_codes`, `get_related`, `search` | Address `/concept/{terminology}_{release}/…`; take `ReleaseContext` |
| `get_concepts_by_codes` | The endpoint omits unresolvable codes silently and returns lexicographic order (verified). Traversal and indexing already reconcile requested against returned codes by code and request only the relation lists they need through `include=`. Remaining: return `{found: {code: concept}, missing: [codes]}` to the tools, never relying on position (SOW v2 §5) |
| `get_replacements(codes)` | New. `/history/{t}_{r}/replacements?list=` — note it errors the whole batch on one bad code, the opposite of the batch concept endpoint; split and retry per code on error |
| `get_roles_catalogue(release)`, `get_associations_catalogue(release)` | New; feed `evs/catalogue.py` |
| `get_subsets`, `get_subset_members`, `get_mapsets`, `get_mapset_maps` | New; the 9 subset/mapset paths and 18 mapsets verified present |
| `fhir_expand`, `fhir_lookup`, `fhir_validate_code`, `fhir_subsumes`, `fhir_translate` | New; R4. **`$expand` ignores `count` and rejects `_count`** (verified), so `expand_value_set` applies its own bound to the full expansion and reports truncation itself |
| `search` | `type` restricted to the seven documented values; `exact` is accepted upstream but behaves as `OR` and is **not** exposed |
| licence-restricted terminologies | `X-EVSRESTAPI-License-Key` from configuration when the terminology requires it; the 403 message is mapped to `invalid_request` with the terminology named |

### 4.2 Relationship catalogue (`evs/catalogue.py`)

Per release: roles and associations with `code`, `name`, `kind`, and `polarity`. Polarity is `negative` for a code in the exclusion set, which is **configured by code** (`R135`–`R142` for current releases) and validated at load against the catalogue: a configured code absent from the release's catalogue is an `internal` error at startup, not a silent positive. This replaces label matching (design review, Ontoprism's `axes.py` pattern) and is the executable form of E-4 until the catalogue publishes polarity itself.

### 4.3 Traversal (`evs/traversal.py`)

In place today: each depth is read in batched requests to the batch endpoint (50 concepts a request, 10 when inverse relations are followed), asking only for the selected relation lists (A5.8; SOW v2 §5), with halving on an oversized response; `descendant` edges come from one `/descendants` request per start code; node and edge limits are claimed nearest first; edge types are selected independently of relationship names (the name filter still applies to hierarchy edges); concepts whose relations exceed the response-size limit are reported under `unexpanded_codes`.

Remaining, around `Budget`:

- `get_concept_hierarchy` (`parent` | `child` | `pathsToRoot`) split from `get_concept_neighborhood`.
- Each visited node fetched **once**, its `summary` in the same batched request as its relation lists (today the walk asks for `minimal` plus the lists).
- `kinds` filter (`parent`, `child`, `role`, `association`, `inverseRole`, `inverseAssociation`) selects edge kinds; `relationshipNames` filters within a kind; neither removes the other. The `is_a_*` pseudo-names go.
- Every edge carries `TraversalProvenance` with polarity from the catalogue. `includeNegative=false` (default) withholds negative edges from the returned node set and lists them under `excluded[]`; it never drops them silently.
- Per-kind rotation; truncation per kind; outbound budget includes retries.
- `maxDepth` ≤ 4, `maxNodes` ≤ 1,000, `maxEdges` ≤ 5,000, `maxRequests` ≤ 200 — documented, clamped, reported.

### 4.4 Index (`evs/index/`)

The interim index SOW v2 §3 requires, built from `index.py` / `embeddings.py` / `retrieval.py` / `evaluation.py`:

- **Per-release tables**, keyed `(release, code)`, plus a `manifests` table carrying release, embedding provider, model, dimension, build timestamp, evaluation-set version and score, and `active` flag. Today the index holds exactly one release (schema 4), and indexing another release replaces it in place.
- **Atomic activation and rollback**: a build writes under a new manifest; activation flips `active` in one transaction; rollback flips it back. `search_concepts` reads only the active manifest's release and refuses with `release_unavailable` if it differs from the requested release.
- **Full NCIt build** from the batch endpoint in pages of 1,000 (the enforced `pageSize` maximum), release-pinned; the current `index-sample` stays as a developer command.
- **The two traps** — provider selection requires both provider and model to be set, and a mismatch is `invalid_configuration` at startup; `cosine_similarity` normalises, and a dimension mismatch is `index_incompatible`, never `0.0`. Changing provider or model invalidates the manifest. Today: provider and model are validated together at startup (a failure is `invalid_configuration`), and a provider, model or dimension mismatch is refused on every write and search. `cosine_similarity` is a dot product that relies on the providers returning unit vectors, which both do; it does not normalise itself.
- **Evaluation set** (`evaluate.py`): versioned NCIt scenarios with expected concepts and scoring thresholds; run on every build; score recorded in the manifest. This is the SOW's retrieval evaluation deliverable in executable form.
- **Retirement condition**: when EVS exposes a semantic mode (E-9), `search_concepts(mode=semantic|hybrid)` is re-pointed to it behind the same tool and the index is deactivated. The tool surface does not change.

### 4.5 Tools (`evs/tools.py`) — Group A

Twelve tools, signatures per *MCP API Specification* §2. Mapping from the current surface:

| Current | Becomes | Note |
|---|---|---|
| `ncit_release_info` | `resolve_release(terminology, channel?)` + `list_terminologies()` | one row per channel; `ttlMs` 0 |
| `ncit_lookup` | `get_concept(terminology, release, code, include[]?)` | `live_only` and `include_raw` removed |
| — | `get_concepts(…, codes[], include[]?)` | returns `found` + `missing` |
| `ncit_search` | `search_concepts(…, query, mode?, limit?, cursor?)` | `lexical`/`typeahead` → EVS REST; `semantic`/`hybrid` → index; score and `matchedOn` |
| `ncit_traverse` | `get_concept_hierarchy(…)` and `get_concept_neighborhood(…)` | hierarchy = `parent|child|pathsToRoot` only; neighbourhood = §4.3 |
| — | `expand_value_set`, `get_concept_subsets`, `get_concept_mappings`, `resolve_retired_code`, `list_relationships` | new |

### 4.6 Resources

Today's templates are `nci-si://concept/ncit/{code}`, `nci-si://release/ncit/{version}` and `nci-si://index/ncit/{version}/manifest`. They become `ncit://concept/{code}`, `ncit://release/{version}` and `ncit://index/manifest/{release}`. Every `resources/read` result carries `ttlMs` / `cacheScope` per §3.6.

---

## 5. caDSR module

### 5.1 Client (`cadsr/client.py`)

Built on `platform/http.py`. Endpoints, all verified live:

| Operation | Route | Rules |
|---|---|---|
| data element by public id | `GET /rad/NCIAPI/1.0/api/DataElement/{id}` | `version` is the **item's** version, exposed as such |
| data elements by concept | `GET /rad/NCIAPI/1.0/api/DataElements/Concept?conceptCode=&headerOnly=true` | single concept only; 9.7–20 s measured — tool declares a 30 s timeout |
| CRDC crosswalk | `GET …/DataElements/getCRDCList` | unparameterised; cached per export date |
| data element search | `GET …/DataElement/search?…` | **1,000 cap, no paging**: a result of exactly 1,000 rows is reported as `Truncation(bound="upstream_cap", omitted="unknown")`; `totalKnown` filled from `getJSON`'s `recordCounter` where the query can be expressed there |
| contexts, item types, workflow statuses | `GET /rad/NCILovAPI/1.0/api/getContextNames` etc. | enumerations; definitions are absent upstream and the result says so |
| classification schemes | from the data-element payload's `ClassificationSchemes[]` with nested items | first-class objects |
| forms | `GET /rad/NCIFormAPI.v2_0:NciFormApiRad/Form/{publicId}` and `/Form/query` | keyword search requires `publicId` or `protocolId` upstream; a keyword-only request is `invalid_request` with that stated |
| models and crosswalk mappings | `GET /rad/NCIModelAPI/1.0/api/Models`, `/CrossWalkMappings/Download` | feed `get_code_map` |
| CDE Match | `POST /rad/NCIAPI.v2_0.cdeMatch.api:cdeMatch_rad/cdeMatch` | built from the **JSON** contract, not the documentation page (which declares a dev host); 28.9 s measured — 45 s timeout, structured timeout error |
| VM Match | `POST /rad/vmMatch/v1/vmMatch` | 15.5 s measured; same handling |
| export date | `HEAD https://cadsr.nci.nih.gov/ftp/caDSR_Downloads/CDE/XML/releasedCDEsXML-OD.zip` | `Last-Modified` → registry state |

All matching calls hold any credential server-side (both contracts declare `401`, neither enforced it when tested; the module is correct under either outcome).

### 5.2 Tools (`cadsr/tools.py`) — Group B

Ten tools per *MCP API Specification* §3. Specific behaviours:

- `resolve_registry_release` → `{identifier: null, exportDate, note}` today; `ttlMs` 0.
- `search_data_elements` → filters by context, workflow status, registration status, value-domain type; truncation at the cap reported; `totalKnown` where available.
- `match_data_elements` → `modelVariant` and `similarityThreshold` are **rejected with `invalid_request`** naming the parameter when supplied, because the published contract does not expose them; the rejection message cites the upstream requirements package. When the contract gains them, the rejection is removed and nothing else changes.
- `get_form` → by public id with modules and questions; keyword search returns `invalid_request` stating the upstream constraint.
- `get_permissible_value` → by data element and value; states that no stable identifier exists in the SI projection (S-3).
- `get_code_map` → `getCRDCList` plus Model API crosswalks; per-node coverage stated; PDC and IDC report `no value-level binding` rather than empty.
- `list_contexts` / `list_classification_schemes` → from LOV and the DE payload; `definition: null` is explicit.

---

## 6. Cross-domain module

### 6.1 Shared SI client (`seam/ssis.py`)

- Façade base `https://cadsrapi.cancer.gov/si-api/v1`, spec at `/SSISAdvQueries/v1/swagger.yaml`. **Every operation declares all parameters required and only `200`/`401` as responses; a missing parameter returns `200` wrapping `apiResponse.type:"E"`.** The client validates required parameters from the spec before calling, and the HTTP client's envelope check catches the rest. `with_concept_id` is keyed by `dec_pub_id`, not concept code, and is used only once a DEC is known.
- SPARQL at `https://shared.semantics.cancer.gov/sparql`. The WAF rejects property paths and `SERVICE` with HTML `403`; the client avoids property paths, and a `403` with HTML is `upstream_unavailable(reason="query rejected by inspection layer")`, never an empty result.
- Graph choice is explicit and recorded: `Thesaurus.rdf` for NCIt hierarchy (`Thesaurus.owl` has 24,049 named classes without a named parent and returns empty for `subClassOf*`), `caDSR` for data elements. Both graphs' identities go into `provenance.graphs[]`.

### 6.2 Tools (`seam/tools.py`) — Group C

- `find_data_elements_for_concept` → SPARQL join with optional subsumption expansion (bounded, per `Budget`); falls back to caDSR REST `/DataElements/Concept` with its timeout when SSIS is unavailable; both release identities recorded.
- `get_concept_for_permissible_value` → reverse SPARQL lookup.
- `resolve_stored_value` → GDC via `NCIt_Maps_To_GDC` (mapset and FHIR ConceptMap agree; the mapset is named in provenance); other commons via `getCRDCList`; `no mapping` with a coverage statement otherwise. Never returns the preferred term as a stored value.
- `get_release_alignment` → NCIt release, caDSR export date, SI graph dates, interval, warning above a configured threshold; `ttlMs` 0.

---

## 7. Workflow module

Three composites per *MCP API Specification* §8.4, implemented as orchestrations of the registry's own tools with one `Budget` and one `ReleaseContext` across the chain:

- `ground_value` — fails closed if either content state cannot be named; truncation from each hop carried through.
- `expand_cohort` — `codes[]` and `excluded[]`; asserted equal to composing `get_concept_neighborhood` + `get_concepts`.
- `harmonize_data_dictionary` — one match call per column, batched where the upstream allows; shared registry state.

---

## 8. Configuration and profiles (`config.py`)

Settings after the change. `NCI_SI_EVS_BASE_URL`, `NCI_SI_TIMEOUT_SECONDS`, `NCI_SI_DATA_DIR` and the embedding pair exist today; the README's other settings (EVS retries, backoff and response limit, index batch size, log level) stay as they are. Each is validated at startup with a message naming its variable, as the existing ones are.

| Setting | Default | Purpose |
|---|---|---|
| `NCI_SI_PROFILE` | `unified` | `evs` · `cadsr` · `unified` |
| `NCI_SI_EVS_BASE_URL`, `NCI_SI_EVS_FHIR_BASE_URL` | production | |
| `NCI_SI_CADSR_BASE_URL`, `NCI_SI_CADSR_FTP_URL` | production | |
| `NCI_SI_SSIS_FACADE_URL`, `NCI_SI_SSIS_SPARQL_URL` | production | |
| `NCI_SI_UPSTREAM_MODE` | `live` | `live` · `fixture` — selects base URLs as a set so the acceptance suite switches everything with one variable |
| `NCI_SI_RELEASE_CHANNEL` | `monthly` | |
| `NCI_SI_EXCLUSION_ROLE_CODES` | `R135,…,R142` | validated against the catalogue at startup |
| `NCI_SI_EVS_LICENSE_KEY`, `NCI_SI_CADSR_CREDENTIAL` | unset | never logged |
| `NCI_SI_EMBEDDING_PROVIDER`, `NCI_SI_EMBEDDING_MODEL` | unset | both required together (today both default to `hashing`) |
| `NCI_SI_DATA_DIR` | `.nci-si-mcp/` | |
| `NCI_SI_TIMEOUT_SECONDS`, `NCI_SI_MATCH_TIMEOUT_SECONDS` | 30, 45 | |

---

## 9. Acceptance suite (`acceptance/`)

Specified in full in *MCP Behavioral Acceptance Suite*; this section states how it lives in the repository.

### 9.1 Layout

```
acceptance/
  pyproject.toml            separate package: nci-si-acceptance; pytest, mcp, jsonschema
  harness/
    client.py               connect over stdio or streamable HTTP; tools/list, tools/call
    fixture_server.py       serves fixtures by (surface, method, path, params) with a request log endpoint
    toolmap.py              baseline tool map application
    report.py               per-tool outcome: PASS | PASS (fixture only) | FAIL | NOT IMPLEMENTED
  fixtures/
    manifest.yaml           pinned NCIt release, caDSR export date, SI graph dates, recorded-on dates
    recorded/<surface>/…    captured responses with the request that produced them
    crafted/<requirement>/… hand-written responses naming the requirement they stand in for
    scenarios/…             the sixteen scenario fixtures
    baseline_toolmap.yaml   required tool → prototype tool + parameter renaming, for baseline-2026-10
  tests/
    test_protocol.py        §3 gates
    test_crosscutting.py    §4, parameterised over the inventory
    test_group_a.py … test_group_w.py
  record.py                 re-records `recorded/` from live against the manifest's pins
```

### 9.2 Run modes

`NCI_SI_UPSTREAM_MODE=fixture` points the server under test at the fixture server; `live` at production. The harness sets the variable, launches the server (stdio) or connects (remote), and runs the suite. Tests marked `live_capable` run in both modes; the rest in `fixture` only.

### 9.3 The request log

The fixture server exposes `GET /_log` returning every request it received since `DELETE /_log`. Tests use it for: outbound budget including retries; batch endpoint used instead of fan-out; no endpoint called twice with identical parameters in one tool call; `Accept: application/json` present on every caDSR call; licence key present on licensed calls and absent from results.

### 9.4 Baseline tool map

For `baseline-2026-10`, written against the furnished commit:

| Required | Prototype | Parameters |
|---|---|---|
| `search_concepts` | `ncit_search` | `query`, `limit`; `mode` lexical → unsupported, semantic → `hybrid` |
| `get_concept` | `ncit_lookup` | `code` |
| `get_concept_hierarchy`, `get_concept_neighborhood` | `ncit_traverse` | `code` → `start_codes` (a list of one); `depth` → `max_depth`; `kinds` → `edge_types` (`inverseRole` → `inverse_role`, `inverseAssociation` → `inverse_association`) |
| `resolve_release` | `ncit_release_info` | — |
| all others | — | NOT IMPLEMENTED |

### 9.5 CI

A job beside the existing `quality` and `test` jobs runs the acceptance suite in `fixture` mode against the server built from the checkout, and becomes a required check on `main`. `live` mode is a manual workflow with the network location recorded.

---

## 10. Tests in the main package

Keep `unittest`-style tests under the gates in `CONTRIBUTING.md`. Extend `tests/fakes.py` with doubles for the caDSR and SSIS clients. Required new tests, each named for the rule it enforces:

- `test_errors`: every `PlatformError` serialises to the error schema; empty results never produce `isError`; the three masked-error shapes are classified.
- `test_release`: one-row resolution; 404 → `release_unavailable`; payload mismatch → `release_unavailable`; caDSR state never carries a fabricated identifier.
- `test_bounds`: retries decrement the request budget; per-kind rotation; truncation report fields.
- `test_catalogue`: polarity by code; a configured code absent from the catalogue fails startup.
- `test_batch`: `found`/`missing` reconciliation; lexicographic return order handled.
- `test_index`: atomic activation and rollback; provider/model mismatch rejected; dimension mismatch rejected.
- `test_schema`: `outputSchema` present and valid for every tool in every profile; surface static across settings; no placeholder text.
- `test_cadsr_client`, `test_ssis_client`: required-parameter validation; envelope errors; `Accept` header.
- `test_server`: `tools/list` per profile; `ttlMs`/`cacheScope` on list and discovery results.

`tests/test_service.py` moves to the tool handlers with `service.py`.

---

## 11. Phases and definition of done

Each phase ends with the unit suite green, the acceptance suite green in `fixture` mode for every tool implemented so far, and a tag: a `vX.Y.Z` release, which a merged `feat` or `fix` pull request cuts automatically, or for Phase 0 the manual `baseline-2026-10`.

| Phase | Delivers | Done when |
|---|---|---|
| **0 · Baseline** | §1.2: licence, `baseline-2026-10` tag; acceptance harness skeleton; EVS fixture set; baseline tool map; baseline run report; acceptance CI job (§9.5) | the furnished package exists and the baseline report is produced |
| **1 · Platform** | §3 in full; `service.py` retired; EVS tools re-homed on the registry with no behaviour change | §3 protocol gates pass; existing EVS behaviour unchanged under the new error and provenance model |
| **2 · EVS module** | §4: release-pinned addressing, batch reconciliation, catalogue polarity, traversal rewrite, FHIR, mapsets, retired codes; index with activation and rollback | all Group A tools PASS in fixture mode; live-capable tests PASS live |
| **3 · caDSR module** | §5 | all Group B tools PASS in fixture mode; PASS (fixture only) rows name their upstream requirement |
| **4 · Cross-domain** | §6 | Group C PASS; both release identities on every result |
| **5 · Workflows, remote transport, audit** | §7, §3.8, §3.9 | Group W PASS; unified profile accepted under §6 of the acceptance specification |

Phase 0 must complete before contract award; the rest is the contractor's work under SOW v2.1 and v1.1, and this specification is what the Prototype Baseline Assessment measures the prototype against.

---

## 12. Removals

- `service.py`, `cadsr.py` (the stub), `include_raw` and `live_only` on the MCP surface.
- Label-based exclusion detection, wherever it appears.
- The `is_a_parent` / `is_a_child` / `is_a_descendant` pseudo-relationship names.

## 13. Non-goals

No NCIm vectorisation or local NCIm graph; no hosted vector database; no writes to any registry or terminology; no re-embedding or index operation after the period of performance; no implementation of upstream API changes — every upstream gap this specification works around is an entry in the upstream requirements package, and the workaround is removed when the gap closes.
