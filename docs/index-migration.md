# Moving the interim NCIt index into EVS

For the team that would host semantic search in EVS. Phase 30 separates the local index from
MCP; **caller impact is none**. The hosted service, HTTP read adapter, one-setting switch and
retirement of local operators are **not implemented**. This guide prescribes no EVS deployment
technology or future command syntax.

## What moves and what stays

Move the top-level `ncit_index` package (`index.py`, `storage.py`, `scoring.py`, `embeddings.py`,
`records.py`, `errors.py`), its schema 6 data, and ownership of the build/evaluate/activate/rollback
cycle. Carry the embedding model assets and pinned revision, provider/model/dimensions identity,
versioned evaluation judgments, calibration and build-specific reports. Preserve recorded EVS
releases and retrieval timestamps. See the [schema and lifecycle](../ARCHITECTURE.md#index-lifecycle)
and [evaluation protocol](retrieval-evaluation.md), including its model and full-corpus evidence.

MCP keeps `IndexPort`, an adapter, request validation, cursor encoding, provenance projection,
audit and `invocation._ERROR_CODES`. The local adapter converts package records and supplies an
event-to-audit callback; the package imports no MCP code. EVS normalization stays in the MCP
build orchestration until the future host supplies that ingestion step.

Today, CLI operators open the package directly through the small factory in `indexing.py`.
`evaluation.py` remains tooling in the MCP distribution and calls the package operator API,
not the port. Context has no operator accessor. Future EVS operators own ingestion, sample/upsert,
rebuild, activation/rollback, build listing, candidate search and evaluation records; these are
not service operations exposed to the MCP server.

## Required read contract

The future service must support these semantics behind a replacement read adapter. This is
the current Python port, not an HTTP endpoint or wire-format specification. A provider object
is a local collaborator; the future transport must agree how its identity and query embedding
are represented without changing retrieval behavior.

| Port operation and inputs | Result and serve-time caller |
| --- | --- |
| `get_active_manifest()` | Active manifest or absence; release/status reporting and manifest resource. |
| `verify_active(provider)` | Usable active manifest or failure, without embedding or upstream I/O in the local implementation; readiness/startup. |
| `get_concept_snapshot(code)` | Manifest and optional cached concept from one snapshot, both absent when no build is active; cached CLI lookup. |
| `search_page(query, provider, limit, mode, requested_release, offset, build_id, retired_status)` | Ranked hits, exact filtered total and manifest from one snapshot; indexed tool search and legacy CLI search. |

- **Identity:** one build holds one recorded EVS release. Verify the requested release and
  embedding compatibility in the same snapshot as the result. Results name that release;
  an empty page still has a manifest and provenance. Never silently substitute another build.
- **Paging:** MCP owns opaque cursors binding applied arguments, release and build id. The
  adapter passes the expected build id and offset; a replacement expires the cursor even at
  the same release. Current indexed cursor identities require 32 lowercase hexadecimal characters.
  Filter retirement before counting and paging; retain deterministic ties.
  MCP pages until exhausted without truncation; CLI one-page truncation is constructed MCP-side.
- **Limits:** the read adapter accepts nonblank queries and limits 1–1000, default 10, with
  `bm25`, `vector` or `hybrid` (default). Offset defaults to zero; release, build id and retirement
  status are optional. MCP maps semantic mode to vector; retirement selection uses the pinned
  terminology's advertised status. Existing public validation and limits remain at each caller.
- **Errors:** preserve `release_mismatch`, `cursor_expired` and `capability_unavailable` with
  their current details and messages. No active index is absence for manifest/snapshot reads;
  search/readiness fail. MCP retains caller-specific absent-index handling. Compatibility and
  storage failures map to `internal_error`, never empty success; unexpected bugs propagate.
  The package defines exceptions, not protocol codes; the MCP invocation table translates them.

See [tool and error contracts](specification.md) for public semantics and
[current operator instructions](../QUICKSTART.md#build-a-small-local-index) for local operation.

## Future handover and rollback — not implemented

1. Stand up the package under EVS ownership from a recorded release, preserving schema,
   model pin and build identity. Build an inactive candidate, evaluate it and activate explicitly;
   retain the predecessor for build rollback. Choose deployment details with the EVS team.
2. Verify search quality against the versioned evaluation set and calibrated floors. Use the
   index-tool outcomes in [the fixture baseline](../acceptance/expected/fixture.json) as the
   acceptance target. Quality evaluation and specification acceptance are separate checks.
3. Implement an HTTP client adapter for the read port. Agree service transport, failure and
   identity contracts; run the [acceptance suite](../acceptance/README.md) through that adapter
   against the target, comparing results, pagination and error behavior through MCP.
4. Add one setting to select local or hosted reads, then switch after verification. No such
   setting exists today. Retain the working local index and matching model during transition.
5. After the rollback window, retire local operator commands and the local package together
   from the MCP distribution; EVS then owns the operator workflow.

Before removal, migration rollback means switching that future setting back to local reads;
the retained local index continues working. After removal, that switch-back path no longer exists.

**Caller contract:** Phase 30 changes no results, errors, audit records or provenance. For the
future hosted service, the intended caller-visible difference is provenance identifying EVS as
the serving source instead of the local index. Its exact fields and values require explicit
agreement in `spec/` before implementation; this guide introduces no source enum value and does
not relabel today's `evs_index` results. All other public behavior is intended to remain unchanged.
