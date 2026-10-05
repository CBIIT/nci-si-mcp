# Implementation plan: from EVS-first prototype to the shared NCI Semantic Infrastructure MCP platform

**Status:** accepted for implementation · **Written:** 1 October 2026 · **Updated:** 2 October 2026, to the code on `main` after `v0.2.0` · **Furnished commit:** tagged when the owner furnishes it, after Phase 3 (§1.2, §11)

The work is tracked on GitHub as one milestone per phase and one for the furnished package (§11), with one issue per deliverable; issues cite this document by section. Where a section describes the code "today", it means `main` at the *Updated* date above.

This document is the implementation plan: how `nci_si_mcp` is extended from the current EVS-first prototype into the shared platform, EVS module and caDSR module that the two Statements of Work scope, together with the acceptance suite that is the acceptance instrument for both. It is written against the code as it is, so every change is stated as a delta from a named module, and it is ordered so that each phase leaves the repository releasable.

The Statements of Work frame and bound the scope; within it, the specification of the required tools and their behaviour is the source of record. It lives in this repository as data in `spec/` (conventions, tools, requirements), rendered as [`docs/specification.md`](specification.md), and is cited below by convention id (A1–A11, M1–M5) and requirement id. The requests to the platform teams stay in the programme's *Platform API Specification*. Where this plan is more specific than the specification, it is because the code forces a decision the specification leaves open.

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

Twenty-nine tools in total, named and typed exactly as in the specification (`spec/tools.yaml`). Three profiles: `evs`, `cadsr`, `unified`. A profile determines which tools `tools/list` returns and nothing else (M1.5); the surface within a profile is static (M1.2).

### 1.2 Baseline

Done:

- The hardening branch is merged (#1) and tagged `v0.1.0`. It changed traversal node codes, embedding-provider selection, the release check in `upsert_concepts` and the error module, and added `ARCHITECTURE.md` and CI.
- The migration to `mcp>=2.0,<3` is committed.
- Since `v0.1.0`: Python 3.13 and PDM (#43), every function below cyclomatic complexity 8 (#44), the lint and quality gates with the engineering standards in `CONTRIBUTING.md` (#45), and automatic releases from Conventional Commit pull request titles (#46). `v0.2.0` is the first automatic release.
- The package is licensed under the Apache License 2.0 (#50).

Remaining:

- Tag the furnished commit, when the owner decides to furnish it (§11), as `baseline-YYYY-MM` for the month it is cut (`baseline-2026-10` if that is this month). This is the commit the Prototype Baseline Package furnishes and the commit the baseline run reports against. It is a manual tag beside the automatic `vX.Y.Z` release tags.

### 1.3 Ground rules carried forward from the current code

These hold today and continue to hold:

- The core has no runtime dependencies. `mcp` and `sentence_transformers` are optional extras, imported lazily; nothing at module level in `server.py` imports `mcp`. The supported Python is 3.14 and newer (owner decision of 3 October 2026, replacing 3.13 and newer of 1 October 2026, which replaced the earlier rule that the core stay importable on Python 3.9).
- The package version is derived from the git tag at build time and written in no file; `setup.py` is gone.
- Unit tests are `unittest.TestCase` classes run by pytest, offline, with hand-written doubles, and every change passes the gates in `CONTRIBUTING.md`. The acceptance suite is a separate package with pytest conventions of its own (fixtures, markers, a fixture server), so that the two do not meet.
- `cadsr.py` must never return fabricated CDE data. That rule survives, restated: **no tool returns content it did not retrieve from a platform or from a fixture that declares itself as such.**

### 1.4 Ground rules that change

| Today | After |
|---|---|
| Every concept request addresses `ncit_{release}` of the one current monthly release, and each concept payload's `version` is checked; the terminology and the release cannot be chosen. An unknown release is `evs_invalid_response` on the batch and descendants endpoints but `concept_not_found` on a single-concept lookup (a 404 there cannot be told from an unknown code), and a payload mismatch is `evs_invalid_response` | Every content request addresses `{terminology}_{release}` from the call's `ReleaseContext` explicitly (verified to work and to fail closed with 404 on an unknown release); the payload check becomes a second guard, not the only one; an unknown release is `release_not_available` and a payload mismatch `release_mismatch` (A3.1, A3.4) |
| Expected exceptions are mapped to codes by one table (`_ERROR_CODES`, applied by `service._enveloped`); with the envelopes that the service, the CLI and the resources build directly, `ErrorCode` has fourteen values | One error model, the error record's closed set of codes (A2.5), one serialisation — every tool returns a structured error through the same path (§3.1) |
| `include_raw` and `live_only` are MCP tool parameters | Both are removed from the MCP surface. `include_raw` stays on the CLI for debugging; `live_only` becomes the `servedBy` field in provenance, reported rather than requested |
| Edge types are selected independently of relationship names, but the name filter also applies to hierarchy edges, which carry the pseudo-names `is_a_parent`, `is_a_child`, `is_a_descendant`: a role-name filter drops them unless those names are listed | Edge kinds and relationship names are separate fields, and hierarchy edges carry no invented name; a filter on one never silently removes the other (A5.5) |
| Exclusion polarity is not represented | Polarity is derived from the relationship **code** against the pinned release's catalogue, never from the label (A5.7) |
| Traversal bounds nodes and edges, nearest first | Implemented in §3.5: the budget also counts outbound requests **including retries** (A5.3), with per-kind node allowances |
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
    caching.py              ttlMs / cacheScope policy per tool class (M2)
    schema.py               outputSchema generation from the dataclasses; static-surface assertion
    registry.py             ToolSpec: name, group, profile, input model, output model, handler
    transport.py            stdio and streamable-HTTP entry points

  evs/
    client.py               was evs.py; release-pinned addressing; batch; FHIR; replacements; mapsets
    catalogue.py            relationship catalogue per release with polarity by code
    traversal.py            reworked walker (A5)
    index/                  was index.py, embeddings.py, retrieval.py, evaluation.py
      store.py              SQLite; per-release tables; manifests with embedding metadata
      activation.py         atomic activation and rollback (M4.1)
      build.py              full-NCIt build from the batch endpoint
      evaluate.py           retrieval evaluation set and scoring protocol
    tools.py                the 12 EVS tools

  cadsr/
    client.py               Data Element, Form 2.0, LOV, Model, CDE Match, VM Match, FTP listing
    registry_state.py       export date and item versions in lieu of a registry release (A3.8)
    tools.py                the 10 caDSR tools

  seam/
    ssis.py                 Shared SI façade and SPARQL client; required-parameter enforcement
    tools.py                the 4 cross-domain tools

  workflows/
    tools.py                the 3 workflow tools

acceptance/                 separate package, see §9
```

`service.py` is retired. Its role — composing clients, index, traversal and adapter — is taken by `platform.registry`, which holds one `ToolSpec` per tool and is the single place `server.py` and `cli.py` read from. The parameter lists that `server.py` and `cli.py` each declare today disappear with it.

---

## 3. Shared platform

### 3.1 Error model (`platform/errors.py`)

Replace the fourteen-value `ErrorCode` literal in `errors.py`, and the `_ERROR_CODES` table in `service.py`, with the codes of the specification's error record (`spec/records.yaml`): each failure carries its `code`, a message, the call's correlation identifier, and the `details` the caller needs for its next step. The prototype's codes map onto them as follows:

| Code | Replaces | `details` carry |
|---|---|---|
| `invalid_request` | `invalid_request`, `invalid_configuration` | parameter, reason |
| `not_found` | `concept_not_found`, `concepts_missing` | identifiers not found |
| `release_not_available` | `release_unresolved`, `release_not_active`, `index_not_active` | requested, source, found (optional, ambiguous channel versions) |
| `release_mismatch` | `version_mismatch` | requested, served, source |
| `upstream_unavailable` | `evs_unavailable`, `evs_invalid_response` | surface, status, attempts, retry-after |
| `timeout` | — | surface, seconds waited |
| `bound_exceeded` | — | bound, limit, reached |
| `capability_unavailable` | — | the capability |
| `cursor_expired` | — | the cursor's release, the current one |
| `internal_error` | `startup_failed`, `no_active_index`, `index_incompatible`, `index_storage_error` | — |

Two rules. **An empty result is never an error**: a tool that matched nothing returns its normal shape with an empty collection and a complete provenance envelope. **A platform failure carried inside a `2xx` body is an error**: the HTTP client (§3.4) recognises the webMethods envelope (`apiResponse.type == "E"`), FHIR `OperationOutcome` with severity `error`, and an HTML body where JSON was requested, and raises `upstream_unavailable` before any tool sees the payload.

Serialisation: every tool handler returns a dataclass or raises a `PlatformError`; the registry converts the latter to an MCP result with `isError: true` and `structuredContent` conforming to the error schema. No handler builds an `isError` dict itself.

### 3.2 Provenance (`platform/provenance.py`)

Extend `models.py`'s per-concept fields into one `ProvenanceEnvelope` attached **per item** (A4.4), with the fields of the specification's provenance record (`spec/records.yaml`), and a `TraversalProvenance` adding those of its traversal record. For caDSR, `release` carries the export date and says that no registry identifier exists (A3.8.2). The `raw` payload is dropped from MCP results and kept only behind the CLI flag.

`Truncation` carries the fields of the specification's truncation record (`spec/records.yaml`): `omitted` is always a number, with `exact` false where it is only a lower bound or an estimate (A5.4).

### 3.3 Release model (`platform/release.py`)

`ReleaseContext` is resolved once per tool call and threaded through every upstream request. It is never resolved implicitly inside another tool — `resolve_release` and `resolve_registry_release` are the only discovery operations and the only unversioned upstream calls (A3.2).

**EVS.** `resolve_evs_release(terminology, channel)` calls `/metadata/terminologies?terminology=…&latest=true&tag={channel}` and requires exactly one row. It replaces `select_monthly_ncit_release`; `latest` is channel-scoped, and the one-row query moves the selection upstream. Zero or several rows, or a row without a version, raise `release_not_available`; `requested` names the requested channel and optional `found` lists the ambiguous versions. The serialized release contains `terminology`, `channel`, `version` and `date`; the pinned path stays internal. Content requests address `/concept/{terminology}_{release}/…`; a 404 with `Terminology not found` maps to `release_not_available`. The payload's `version` is compared as a second guard and a mismatch is `release_mismatch`, never silently accepted. Tool/key/alias renames and `alternatives[]` remain #18.

**caDSR.** The pure `registry_state(generation_date, upstream_identifier, source_distribution=…)` builds the specification's `registry_release` record: `{published, identifier?, generatedAt, sourceDistribution}`. Without a published release, `published` is false, `identifier` is absent, and the export's `Last-Modified` for `releasedCDEsXML-OD.zip` becomes an ISO-8601 UTC `generatedAt`. When a registry release appears upstream (C-1), `published` is true and its identifier and own ISO-8601 date are passed through unchanged. `sourceDistribution` names the distribution the caller read the date from. A missing or invalid date, blank supplied identifier or missing distribution raises `RegistryMetadataError`; the shared error path reports `upstream_unavailable` with `surface: cadsr`. No identifier or date is invented, and registry reproducibility is not achievable while no registry release is published. The instrumented HEAD request and tool exposure belong to #31.

**Shared SI.** Every SSIS call records the identity of each graph it touched, read from the content graphs' `owl:versionInfo` / `dc:date` with two queries and no property paths, and cached with the SSIS release-alignment TTL (§3.6). These queries belong to #36.

### 3.4 HTTP client (`platform/http.py`)

One client for all surfaces, replacing `EVSClient._get_json` and the per-module ad hoc calls:

- Always sends `Accept: application/json` (several caDSR routes return HTML otherwise) and the correlation header (M7.1).
- Retries only `5xx` and connection errors, with jittered backoff, **and counts every attempt against the call's `Budget`** (A5.3). Honours `Retry-After` on `429`.
- Classifies the response before returning it (§3.1): status, content type, webMethods envelope, FHIR `OperationOutcome`.
- Exposes a request-log hook. In production it feeds the audit record; under the acceptance suite it is what the fixture server mirrors.
- Holds credentials (licence keys, any caDSR credential) from configuration and never logs them.

### 3.5 Bounds (`platform/bounds.py`)

Implemented in `bounds.py`: the service creates one `Budget`, including depth, from caller limits clamped to the documented maxima and passes it explicitly to the walker. Its context variable is scoped and restored like the correlation context so the HTTP client uses that same instance. The HTTP client counts attempts, including release discovery, retries and split batches, against the 200-request allowance declared for hierarchy and neighborhood. Other calls have no request budget unless their specification declares one. Without graph content exhaustion returns `bound_exceeded`; with graph content it returns a partial graph. The first bound that dropped anything wins, so request exhaustion reports `requests` only when no earlier bound applies. Unknown per-kind omissions use the lower bound zero with `exact: false`.

`clamp_limits`, `clamp_edge_limit` and the `HARD_MAX_*` constants now live in `bounds.py`; the defaults and maxima are the tools' `bounds` in `spec/tools.yaml`. The walker rotates relationship kinds across each breadth-first frontier. Starts count against the global node limit; a kind's optional allowance counts only new nodes it admits. Existing-node edges, duplicates and filtered edges spend no node allowance. Truncation includes per-kind records for mixed-kind walks.

### 3.6 Caching hints (`platform/caching.py`)

The values are the specification's M2.2 and M2.3 (`spec/conventions.yaml`): release-pinned content 86,400,000 and public; governed content no release pins (caDSR content while caDSR publishes no registry release) short and positive, at most 3,600,000, public; results computed from caller-supplied values 0 and private; the resolve tools 0 and public; `tools/list` long and public.

A tool result carries both in its `_meta` (M2.5). A cursor encodes the release it was issued against; presenting it after that release is superseded returns `cursor_expired` (M2.4).

### 3.7 Schema generation (`platform/schema.py`)

`outputSchema` is generated from the result dataclasses for every tool, success and error shapes alike, and checked by a unit test that renders `tools/list` for each profile and validates every schema. A second test asserts the rendered surface is byte-identical across terminology, release and upstream mode (static surface, M1.2). A third asserts no description contains placeholder text or an operator a tool test shows unsupported (A2.3, A2.4). An input schema states no `maximum` for a bounded argument: a value above it is applied as the maximum (the tools' `bounds` in `spec/tools.yaml`), and the argument's description states its default and maximum.

### 3.8 Transport (`platform/transport.py`)

stdio stays. Add the NCI-approved remote transport — streamable HTTP in `mcp>=2.0` — behind the same registry. Authentication and authorisation are hooks on the transport layer with a no-op default; the mechanism is NCI's to approve, and the hook is what lets it be supplied without touching tools.

### 3.9 Audit (`platform/audit.py`)

One structured record per tool call: correlation id, timestamp, tool, target (terminology or context), release context, status, outbound request count including retries, latency, truncation. Free text, credentials and licence keys are redacted at the record boundary, not by each tool.

---

## 4. EVS module

### 4.1 Client (`evs/client.py`)

Delta from `evs.py`:

| Method | Change |
|---|---|
| `get_concept`, `get_concepts_by_codes`, `get_related`, `search` | Address `/concept/{terminology}_{release}/…`; take `ReleaseContext` |
| `get_concepts_by_codes` | The endpoint omits unresolvable codes silently and keeps no order: mostly lexicographic, but the same request answered in two orders on 2 October 2026. Traversal and indexing already reconcile requested against returned codes by code and request only the relation lists they need through `include=`. Remaining: return `{found: {code: concept}, missing: [codes]}` to the tools, never relying on position (get_concepts-1) |
| `get_replacements(codes)` | New. `/history/{t}_{r}/replacements?list=` — note it errors the whole batch on one bad code, the opposite of the batch concept endpoint; split and retry per code on error |
| `get_roles_catalogue(release)`, `get_associations_catalogue(release)` | New; feed `evs/catalogue.py` |
| `get_subsets`, `get_subset_members`, `get_mapsets`, `get_mapset_maps` | New; the 9 subset/mapset paths and 18 mapsets verified present |
| `fhir_expand`, `fhir_lookup`, `fhir_validate_code`, `fhir_subsumes`, `fhir_translate` | New; R4. **`$expand` ignores `count` and rejects `_count`** (verified), so `expand_value_set` applies its own bound to the full expansion and reports truncation itself |
| `search` | `type` restricted to the seven documented values; `exact` is accepted upstream but behaves as `OR` and is **not** exposed |
| licence-restricted terminologies | `X-EVSRESTAPI-License-Key` from configuration when the terminology requires it; the 403 message is mapped to `invalid_request` with the terminology named |

### 4.2 Relationship catalogue (`evs/catalogue.py`)

Per release: roles and associations with `code`, `name`, `kind`, and `polarity`. Polarity is `negative` for a code in the exclusion set, which is **configured by code** (`R135`–`R142` for current releases) and validated at load against the catalogue: a configured code absent from the release's catalogue is an `internal_error` at startup, not a silent positive. This replaces label matching (design review, Ontoprism's `axes.py` pattern) and is the executable form of E-4 until the catalogue publishes polarity itself.

### 4.3 Traversal (`evs/traversal.py`)

In place today: each depth is read in batched requests to the batch endpoint (50 concepts a request, 10 when inverse relations are followed), asking only for the selected relation lists (A5.8), with halving on an oversized response; `descendant` edges come from one `/descendants` request per start code; node and edge limits are claimed nearest first; edge types are selected independently of relationship names (the name filter still applies to hierarchy edges); concepts whose relations exceed the response-size limit are reported under `unexpanded_codes`.

Remaining, around `Budget`:

- `get_concept_hierarchy` (`parent` | `child` | `pathsToRoot`) split from `get_concept_neighborhood`.
- Each visited node fetched **once**, its `summary` in the same batched request as its relation lists (today the walk asks for `minimal` plus the lists); the nodes of the last depth in one `minimal` batch, for their status (the node record, A8.1).
- `kinds` filter (`parent`, `child`, `role`, `association`, `inverseRole`, `inverseAssociation`) selects edge kinds; `relationshipNames` filters within a kind; neither removes the other. The `is_a_*` pseudo-names go.
- Every edge carries `TraversalProvenance` with polarity by code (the exclusion set in `spec/records.yaml`). Negative edges and the nodes they reach are returned, marked; with `includeNegative=false` (default) a node only negative edges reach is not followed further.
- Depth-limit truncation is still due in #18. Depth, node and edge maxima, per-kind rotation and truncation, and the outbound request budget including retries are implemented (§3.5).

### 4.4 Index (`evs/index/`)

The interim index (M4.1), built from `index.py` / `embeddings.py` / `retrieval.py` / `evaluation.py`:

- **Per field**: the preferred name, each synonym and each definition are indexed as texts of their own, so that a search names the field it matched (`matchedOn`) and a query equal to a preferred name scores that name highest under any model (search_concepts-3). Today the index embeds one concatenated text per concept, and the -3 tests fail until #28 rebuilds it.
- **Per-release tables**, keyed `(release, code)`, plus a `manifests` table carrying release, embedding provider, model, dimension, build timestamp, evaluation-set version and score, and `active` flag. Today the index holds exactly one release (schema 4), and indexing another release replaces it in place.
- **Atomic activation and rollback**: a build writes under a new manifest; activation flips `active` in one transaction; rollback flips it back. `search_concepts` reads only the active manifest's release and refuses with `release_mismatch` if it differs from the requested release.
- **Full NCIt build** from the batch endpoint in pages of 1,000 (the enforced `pageSize` maximum), release-pinned; the current `index-sample` stays as a developer command.
- **The two traps** — provider selection requires both provider and model to be set, and a mismatch is `invalid_configuration` at startup; `cosine_similarity` normalises, and a dimension mismatch is `index_incompatible`, never `0.0`. Changing provider or model invalidates the manifest. Today: provider and model are validated together at startup (a failure is `invalid_configuration`), and a provider, model or dimension mismatch is refused on every write and search. `cosine_similarity` is a dot product that relies on the providers returning unit vectors, which both do; it does not normalise itself.
- **Evaluation set** (`evaluate.py`): versioned NCIt scenarios with expected concepts and scoring thresholds; run on every build; score recorded in the manifest. This is the retrieval evaluation set in executable form.
- **Retirement condition**: when EVS exposes a semantic mode (E-9), `search_concepts(mode=semantic|hybrid)` is re-pointed to it behind the same tool and the index is deactivated. The tool surface does not change.

### 4.5 Tools (`evs/tools.py`)

Twelve tools, signatures in the specification (`spec/tools.yaml`, group `evs`). Mapping from the current surface:

| Current | Becomes | Note |
|---|---|---|
| `ncit_release_info` | `resolve_release(terminology, channel?)` + `list_terminologies()` | one row per channel; `ttlMs` 0 |
| `ncit_lookup` | `get_concept(terminology, release, code, include[]?)` | `live_only` and `include_raw` removed |
| — | `get_concepts(…, codes[], include[]?)` | returns `found` + `missing` |
| `ncit_search` | `search_concepts(…, query, mode?, limit?, cursor?)` | `lexical`/`typeahead` → EVS REST, EVS's highlight as `matchedOn` where it gives one and no score; `semantic`/`hybrid` → index, with a score and the field matched |
| `ncit_traverse` | `get_concept_hierarchy(…)` and `get_concept_neighborhood(…)` | hierarchy = `parent|child|pathsToRoot` only; neighbourhood = §4.3 |
| — | `expand_value_set`, `get_concept_subsets`, `get_concept_mappings`, `resolve_retired_code`, `list_relationships` | new |

### 4.6 Resources

The furnished resources, EVS and caDSR, and the prompt templates are specified in `spec/resources.yaml` and `spec/prompts.yaml` (rendered in `docs/specification.md`, section 3). Today's templates are `nci-si://concept/ncit/{code}`, `nci-si://release/ncit/{version}` and `nci-si://index/ncit/{version}/manifest`. They become `ncit://concept/{release}/{code}` (the release is mandatory for every concept read), `ncit://release/{version}` and `ncit://index/manifest/{release}`. The caDSR module adds `cadsr://data-element/{publicId}` and `cadsr://data-element/{publicId}/{version}`, `cadsr://registry/release` and `cadsr://crosswalk/crdc`. Every `resources/read` result carries `ttlMs` / `cacheScope` per §3.6, of the class §3.6 gives what the resource holds, and a provenance record; its content is compared with the answer of the tool it names on identity and release, not section by section. The acceptance suite tests all of this at protocol level (P-8, P-9).

---

## 5. caDSR module

### 5.1 Client (`cadsr/client.py`)

Built on `platform/http.py`. Endpoints, all verified live:

| Operation | Route | Rules |
|---|---|---|
| data element by public id | `GET /rad/NCIAPI/1.0/api/DataElement/{id}` | `version` is the **item's** version, exposed as such |
| data elements by concept | `GET /rad/NCIAPI/1.0/api/DataElements/Concept?conceptCode=&headerOnly=true` | single concept only; 9.7–20 s measured — tool declares a 30 s timeout |
| CRDC crosswalk | `GET …/DataElements/getCRDCList` | unparameterised; cached per export date |
| data element search | `GET …/DataElement/search?…` | **1,000 cap, no paging**: a result of exactly 1,000 rows is reported as truncated with `bound` `upstream_cap`; `totalKnown` filled from `getJSON`'s `recordCounter` where the query can be expressed there gives `omitted` exactly; where it cannot, `omitted` is 0 with `exact` false, which says only that more may exist, and the missing count is an entry in the upstream requirements package (A5.4 cannot be met without it) |
| contexts, item types, workflow statuses | `GET /rad/NCILovAPI/1.0/api/getContextNames` etc. | enumerations; definitions are absent upstream and the result says so |
| classification schemes | from the data-element payload's `ClassificationSchemes[]` with nested items | first-class objects |
| forms | `GET /rad/NCIFormAPI.v2_0:NciFormApiRad/Form/{publicId}` and `/Form/query` | keyword search requires `publicId` or `protocolId` upstream; a keyword-only request is `invalid_request` with that stated |
| models and crosswalk mappings | `GET /rad/NCIModelAPI/1.0/api/Models`, `/CrossWalkMappings/Download` | feed `get_code_map` |
| CDE Match | `POST /rad/NCIAPI.v2_0.cdeMatch.api:cdeMatch_rad/cdeMatch` | built from the **JSON** contract, not the documentation page (which declares a dev host); 28.9 s measured — 45 s timeout, structured timeout error |
| VM Match | `POST /rad/vmMatch/v1/vmMatch` | 15.5 s measured; same handling |
| export date | `HEAD https://cadsr.nci.nih.gov/ftp/caDSR_Downloads/CDE/XML/releasedCDEsXML-OD.zip` | `Last-Modified` → registry state |

All matching calls hold any credential server-side (both contracts declare `401`, neither enforced it when tested; the module is correct under either outcome).

### 5.2 Tools (`cadsr/tools.py`)

Ten tools, signatures in the specification (group `cadsr`). Specific behaviours:

- `resolve_registry_release` → `{published: false, generatedAt, sourceDistribution}` today, with no `identifier`; when a registry release is published, `published` is true, `identifier` is present and `generatedAt` is the release's own date (§3.3); `ttlMs` 0.
- `search_data_elements` → filters by context, workflow status, registration status, value-domain type; truncation at the cap reported; `totalKnown` where available.
- `match_data_elements` → `modelVariant` and `similarityThreshold` are **rejected with `invalid_request`** naming the parameter when supplied, because the published contract does not expose them; the rejection message cites the upstream requirements package. When the contract gains them, the rejection is removed and nothing else changes.
- `get_form` → by public id with modules and questions; keyword search returns `invalid_request` stating the upstream constraint.
- `get_permissible_value` → by data element and value; states that no stable identifier exists in the SI projection (S-3).
- `get_code_map` → `getCRDCList` plus Model API crosswalks; per-node coverage stated; PDC and IDC report `no value-level binding` rather than empty.
- `list_contexts` / `list_classification_schemes` → from LOV and the DE payload; `definition: null` is explicit.

---

## 6. Cross-domain module

### 6.1 Shared SI client (`seam/ssis.py`)

- Façade base `https://cadsrapi.cancer.gov/si-api/v1`, spec at `/SSISAdvQueries/v1/swagger.yaml`. **Every operation declares all parameters required and only `200`/`401` as responses; a missing parameter returns `200` wrapping `apiResponse.type:"E"` or `"I"` ("No data found"), and every operation answers HTML unless `Accept: application/json` is sent; `with_specific_object_class` stops at 1,000 rows without saying so.** The client validates required parameters from the spec before calling, and the HTTP client's envelope check catches the rest. `with_concept_id` is keyed by `dec_pub_id`, not concept code, and is used only once a DEC is known.
- SPARQL at `https://shared.semantics.cancer.gov/sparql`, asked with a form-encoded POST (a direct `application/sparql-query` POST is refused) and the query texts the request-form register prescribes. The inspection layer refuses `SERVICE`, Virtuoso's `OPTION (TRANSITIVE)` and some `FILTER regex` patterns with an HTML `403`; property paths pass (3 October 2026). A `403` with HTML is `upstream_unavailable(reason="query rejected by inspection layer")`, never an empty result.
- Graph choice is explicit and recorded: `Thesaurus.rdf` for NCIt hierarchy (`Thesaurus.owl` runs its hierarchy through anonymous restrictions, so `subClassOf*` misses most of it: 7,109 descendants of C2991 against 22,854 on 26.09d), `caDSR` for data elements. Both graphs' identities go into `provenance.graphs[]`.

### 6.2 Tools (`seam/tools.py`)

- `find_data_elements_for_concept` → SPARQL join with optional subsumption expansion (bounded, per `Budget`); falls back to caDSR REST `/DataElements/Concept` with its timeout when SSIS is unavailable, except where a release is given (REST cannot name the NCIt release: `release_not_available`) or `includePermissibleValues` asks for the reverse lookup (REST has none: `capability_unavailable`); both release identities recorded (`provenance.release` and `provenance.registry`).
- `get_concept_for_permissible_value` → SPARQL lookup of a data element's value; the concept record of the pinned release from EVS; by `permissibleValueId` `capability_unavailable` (OP-C10).
- `resolve_stored_value` → GDC via `NCIt_Maps_To_GDC` (mapset and FHIR ConceptMap agree; the mapset is named in provenance); other commons via `getCRDCList`; `confidence` asserted or none, `evidence` naming each source; no stored value with a coverage statement otherwise. Never returns the preferred term as a stored value.
- `get_release_alignment(maxIntervalDays = 31)` → NCIt release, caDSR export date, SI graph dates, `intervalDays`, and a warning naming the threshold when `intervalDays` exceeds it; `ttlMs` 0.

---

## 7. Workflow module

Three composites (the specification's group `workflow`), implemented as orchestrations of the registry's own tools with one `Budget` and one `ReleaseContext` across the chain:

- `ground_value` — fails closed if either content state cannot be named; `registryRelease` optional (unpinned when absent); truncation from each hop carried through (`perHop`).
- `expand_cohort` — `codes[]` and `excluded[]`; asserted equal to composing `get_concept_neighborhood` + `get_concepts`.
- `harmonize_data_dictionary` — one match call per column, batched where the upstream allows; shared registry state.

---

## 8. Configuration and profiles (`config.py`)

Settings after the change. `NCI_SI_EVS_BASE_URL`, `NCI_SI_TIMEOUT_SECONDS`, `NCI_SI_DATA_DIR` and the embedding pair exist today; QUICKSTART.md's other settings (EVS retries, backoff and response limit, index batch size, log level) stay as they are. Each is validated at startup with a message naming its variable, as the existing ones are.

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

The settings the acceptance suite gives a server under test, and their formats, are specified in the specification's §4 (`spec/acceptance.md`): `NCI_SI_CADSR_CREDENTIAL` is `user:password`, sent as HTTP Basic.

---

## 9. Acceptance suite (`acceptance/`)

The requirements it tests and its acceptance rules are in the specification's §3 and §4; this section states how it lives in the repository.

### 9.1 Layout

```
acceptance/
  pyproject.toml            separate package: nci-si-acceptance, versioned on its own; pytest, mcp, jsonschema
  src/nci_si_acceptance/    the harness
    client.py               connect over stdio or streamable HTTP; tools/list, tools/call
    fixture_server.py       serves fixtures by (surface, method, path, params, headers, body), active scenarios first, with a request log endpoint
    concepts.py             composes EVS concept answers from one recording per concept (§9.6)
    record.py               re-records `recorded/` from live against the manifest's pins (`pdm run acceptance-record`)
    craft.py                crafts the scenario fixtures EVS does not produce on demand (`pdm run acceptance-craft`)
    register.py             writes the register of request forms from the manifest (`pdm run acceptance-register`)
    tools.py                baseline tool map application
    requirements.py         the rule that tests cite `spec/requirements.yaml` and every requirement is cited or planned
    report.py               per-tool outcome: PASS | PASS (fixture only) | FAIL | NO FIXTURE | INCOMPLETE | NOT IMPLEMENTED | NOT RUN | NO TESTS; marks rows served through the tool map
  fixtures/
    manifest.yaml           pinned NCIt release (caDSR export date and SI graph dates to come), concept rules, the scenarios, the requests recorded
    recorded/<surface>/…    captured responses with the request that produced them
    crafted/<requirement>/… hand-written responses naming the requirement they stand in for
    scenarios/<group>/<name>/ the fixtures of each scenario the manifest describes
    baseline_toolmap.yaml   required tool → prototype tool + parameter renaming, for the server before Phase 2 (§9.4)
  request-forms/            the register of request forms: a view for each team (EVS, caDSR, Shared SI) and one for all
  tests/
    test_protocol.py        the P requirements (protocol gates)
    test_crosscutting.py    the X requirements, one case per tool with a call in calls.yaml
    calls.yaml              that call of each content-returning tool, answered by the fixture set
    test_evs.py, test_cadsr.py, test_cross_domain.py, test_workflow.py   each group's tool requirements
  selftests/                the harness's own tests, run in CI in two shards and their coverage combined
```

The root project installs the package editable (dependency group `acceptance`); `pdm run acceptance` runs the suite and `pdm run acceptance-selftest` the harness's own tests.

### 9.2 Run modes

`NCI_SI_UPSTREAM_MODE=fixture` points the server under test at the fixture server; `live` at production. The harness sets the variable, launches the server (stdio) or connects (remote), and runs the suite. Tests marked `live_capable` run in both modes; the rest in `fixture` only.

### 9.3 The request log

The fixture server exposes `GET /_log` returning every request it received since `DELETE /_log`, each decoded (surface, path, parameters, body) and as sent (`raw`, the path and query undecoded). Tests use it for: hostile identifiers that reach no request, free text that arrives as one value, a code sent as one encoded path segment; outbound budget including retries; batch endpoint used instead of fan-out; no endpoint called twice with identical parameters in one tool call; `Accept: application/json` present on every caDSR call; licence key present on licensed calls and absent from results.

### 9.4 Baseline tool map

The map lets the suite call today's tools under the required names, so the tests of the mapped EVS tools run against an implementation from Phase 0 on instead of reporting NOT IMPLEMENTED; the report marks those rows *implemented under another name*. The harness applies an entry in every run while the required tool is absent from `tools/list`. Written against today's server, which serves NCIt's current monthly release only: every entry checks `terminology` is `ncit` and accepts `release` without passing either on, and any argument an entry does not list is unsupported:

| Required | Prototype | Parameters |
|---|---|---|
| `resolve_release` | `ncit_release_info` | `channel` weekly unsupported |
| `get_concept` | `ncit_lookup` | `code`; `include` unsupported |
| `get_concept_hierarchy` | `ncit_traverse` | `code` → `start_codes` (a list of one); `direction` → `edge_types` (`parent`, `child`; `pathsToRoot` unsupported) with `direction: both` fixed; `depth` → `max_depth`; `limit` → `max_nodes`; `cursor` unsupported |
| `get_concept_neighborhood` | `ncit_traverse` | `code` → `start_codes`; `depth` → `max_depth`; `kinds` → `edge_types` (`inverseRole` → `inverse_role`, `inverseAssociation` → `inverse_association`) with `direction: both` fixed; `maxNodes`, `maxEdges` → `max_nodes`, `max_edges`; `budgetPerKind` → `budget_per_kind`; `includeNegative: true` unsupported |
| all others | — | NOT IMPLEMENTED |

`search_concepts` has no stand-in: the prototype cannot search EVS, and its index search is not exposed as the required tool. The operator's prepare step builds the index the semantic and hybrid tests need (acceptance README). A test that depends on another release than the current one fails against the prototype. An unsupported argument or value is a capability the prototype lacks; a call using it reports NOT IMPLEMENTED rather than a failure. Self-tests check every stand-in, argument and value against the prototype's `tools/list` and make one call through each entry that the prototype must accept.

At the furnished commit (after Phase 3) the map is empty. The owner decided (2 October 2026) that the mechanism goes in the change that removes its last entry.

### 9.5 CI

A job beside the existing `quality` and `test` jobs (`acceptance`) runs the acceptance suite in `fixture` mode against the server built from the checkout, and becomes a required check on `main`. It is a ratchet over individual tests: the outcome of every test must equal a committed expected outcome (`acceptance/expected/fixture.json`: test id to outcome, nothing else), and the per-tool report, and the README's status table, are derived from them. The job stays green while tools are NOT IMPLEMENTED or FAIL, catches a test that stops passing inside a tool that still fails, and a pull request that changes an outcome updates the expected outcomes in the same change. `live` mode is a manual workflow (`acceptance-live.yml`) with the network location recorded: the runner, and which IP family reaches EVS.

Because the suite and the tools are written by the same hands, two rules keep the suite honest. A suite test asserts what the specification says, cites the requirements it enforces (`@pytest.mark.requirement`), and never asserts what the current implementation happens to return. A pull request that changes a suite test or fixture while making a tool pass lists each change with the passage that justifies it: correcting a wrong test before the interface baseline is frozen is expected, weakening one is not.

### 9.6 Request forms

The fixtures use the request forms of the platform operations (their `OP-` ids, from `operations.yaml` of the programme's platform conformance suite), release-pinned; the generated register (`acceptance/request-forms/`) gives each form's operation and rationale, including where the inventory's form and EVS differ. Where EVS does not yet answer the form a requirement prescribes, the ordinary fixture is crafted to the requirement and names it, and the live run shows the gap (*Acceptance Suite* §2.1). Two kinds of request are answered whatever their form:

- **EVS concepts.** One recording per concept answers every projection and relation list of that concept, and every batch is composed from the recordings of the concepts it names, through declared rules (`concepts.py`): project by `include` (the include-to-key table in the manifest; EVS's `include` is a clean key projection, verified 2 October 2026), select by `list` (each code once, unknown codes left out, in no particular order, since EVS keeps none), and one relation list on its own. `record.py` checks composed answers against real ones.
- **Ignored parameters.** A parameter the service is shown to ignore is declared with its evidence and left out of the match.

The recorder sends no licence key, so what it records is what EVS serves publicly; licensed content, which EVS refuses without the key (403), is never recorded, and a recording whose request carried the key is refused.

---

## 10. Tests in the main package

Keep `unittest`-style tests under the gates in `CONTRIBUTING.md`. Extend `tests/fakes.py` with doubles for the caDSR and SSIS clients. Required new tests, each named for the rule it enforces:

- `test_errors`: every `PlatformError` serialises to the error schema; empty results never produce `isError`; the three masked-error shapes are classified.
- `test_release`: one-row resolution; 404 → `release_not_available`; payload mismatch → `release_mismatch`; caDSR state never carries a fabricated identifier.
- `test_bounds`: retries decrement the request budget; per-kind rotation; truncation report fields.
- `test_catalogue`: polarity by code; a configured code absent from the catalogue fails startup.
- `test_batch`: `found`/`missing` reconciliation; any return order handled.
- `test_index`: atomic activation and rollback; provider/model mismatch rejected; dimension mismatch rejected.
- `test_schema`: `outputSchema` present and valid for every tool in every profile; surface static across settings; no placeholder text.
- `test_cadsr_client`, `test_ssis_client`: required-parameter validation; envelope errors; `Accept` header.
- `test_server`: `tools/list` per profile; `ttlMs`/`cacheScope` on list and discovery results.

`tests/test_service.py` moves to the tool handlers with `service.py`.

---

## 11. Phases and definition of done

The Prototype Baseline Package furnishes the specification, the acceptance suite with its fixture set, and the prototype: the shared platform, the EVS module and the caDSR client, tools and tests completed at award.

The suite is therefore completed first, for all twenty-nine tools, so that it is complete at whatever point the package is furnished. A tool the prototype lacks is a valid NOT IMPLEMENTED row; a required tool without tests is a defect in the package. The phases then make the tools pass their tests, in the order of the table below.

Each phase ends with the unit suite green, the acceptance suite's expected outcomes updated for every test whose outcome changed, and a `vX.Y.Z` release where a merged `feat`, `fix` or `perf` pull request cuts one.

| Phase | Delivers | Done when |
|---|---|---|
| **0 · Acceptance suite** | Licence; §9: harness, fixture mechanics (scenarios, response sequences, request bodies), recorded and crafted fixtures for every surface and all sixteen scenarios, protocol gates, cross-cutting tests, per-tool tests for every tool group, the baseline tool map as a working aid, the per-tool report, the fixture-mode CI job | every required tool has its tests; the report runs against the server at the end of Phase 0, through the tool map where one applies |
| **1 · Platform** | §3 in full; `service.py` retired; EVS tools re-homed on the registry with no behaviour change | §3 protocol gates pass; existing EVS behaviour unchanged under the new error and provenance model |
| **2 · EVS module** | §4: release-pinned addressing, batch reconciliation, catalogue polarity, traversal rewrite, FHIR, mapsets, retired codes; index with activation and rollback | all EVS tools PASS in fixture mode; live-capable tests PASS live |
| **3 · caDSR module** | §5 | all caDSR tools PASS in fixture mode; PASS (fixture only) rows name their upstream requirement |
| **Furnished package** | §1.2: the tag and the Prototype Baseline Package, with the baseline run report against that commit, naming the tools whose tests have never passed against any implementation | the tag is on the commit the report ran against and both SOWs' package checklists are met (#3); cut when the owner decides, not before Phase 3 is done (*Project Plan* §7: "repository Phases 0–3 to the point where both modules yield a meaningful baseline report") |
| **4 · Cross-domain** | §6 | cross-domain tools PASS; both release identities on every result |
| **5 · Workflows, remote transport, audit** | §7, §3.8, §3.9 | workflow tools PASS; unified profile accepted under the specification's §4 |

Work proceeds in the order of the table above until award. What remains at the furnished commit is the contractors' work under the two Statements of Work, and the specification is what the Prototype Baseline Assessment measures the prototype against.

---

## 12. Removals

- `service.py`, `cadsr.py` (the stub), `include_raw` and `live_only` on the MCP surface.
- Label-based exclusion detection, wherever it appears.
- The `is_a_parent` / `is_a_child` / `is_a_descendant` pseudo-relationship names.

## 13. Non-goals

No NCIm vectorisation or local NCIm graph; no hosted vector database; no writes to any registry or terminology; no re-embedding or index operation after the period of performance; no implementation of upstream API changes — every upstream gap this specification works around is an entry in the upstream requirements package, and the workaround is removed when the gap closes.
