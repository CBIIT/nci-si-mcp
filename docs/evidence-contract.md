# Validation run evidence

The [Phase 7 companion plan](portal-plan.md), issue
[#191](https://github.com/CBIIT/nci-si-mcp/issues/191), changes no MCP record or tool.
Design credit: **Semantic Infrastructure (SI) team**, MCP Architecture, 1 October 2026.

All local features work for repository users without application login. Public engineering CI
remains public. Only UAT/PROD administrative instances require platform authentication and
explicit maintainer authorization; public documentation remains anonymous. Upstream systems
retain their access rules. The envelope's access label classifies its deployed administrative
surface; it is neither an authentication mechanism nor a restriction on local developer use.

## Run envelope version 1

`scripts/evidence_envelope.py::validate_envelope` validates UTF-8 JSON metadata and binds it to
separately supplied report bytes. A wrapper records metadata independently of the report.
Consumers must establish the producer's authority through their execution/storage boundary;
this validator does not authenticate it. A checksum proves byte agreement, not trusted origin,
deployment authorization or a passing test result.

| Field | Value and meaning |
| --- | --- |
| `schema` | Integer `1`; booleans and other versions rejected |
| `access` | `maintainer-admin`: deployed admin classification; local access needs no login |
| `run_id` | 32 lowercase hexadecimal characters assigned by the wrapper |
| `kind` | `acceptance` or `benchmark` |
| `runner_commit` | 40 lowercase hexadecimal characters identifying runner source |
| `catalogue_sha256` | Digest of reviewed case inventory and attribution at its original revision |
| `stories_sha256` | Digest of that revision's story mapping |
| `expectations_sha256` | Digest of its expected outcomes, not today's expectations |
| `selection_sha256` | Digest of the exact selected inventory, not just a count |
| `started_at`, `finished_at` | ISO timestamps with `T`, seconds and offset; finish cannot precede start |
| `state` | `completed`, `failed`, `cancelled`, `interrupted` or `unavailable` |
| `exit_code` | Integer 0–255 or null when unknown; never bool/float |
| `report_sha256` | SHA-256 of exact report bytes; null only when no report exists |
| `server_commit` | Separately recorded server source commit or null; never inferred from the suite |

Every field occurs exactly once. Unknown fields, duplicate keys and nonfinite numbers fail.
SHA-256 values contain 64 lowercase hexadecimal characters. No arbitrary labels, paths, URLs,
credentials, result bodies or assertions of trust belong here. Limits are 64 KiB envelope and
16 MiB report bytes. The later file reader must enforce limits during reading, before unbounded
allocation. Errors do not echo rejected values.

Completed means zero process exit and an available report, **not** a passing or complete test
inventory. Failed requires a known nonzero exit, with or without a report. Cancelled/interrupted
may retain a partial report and unknown exit. Unavailable has no report. Client cancellation
never proves remote server termination. An absent report must not become zero tests passed.

The current first implementation slice validates structure, byte limits, timestamps, termination
consistency and report digest binding only. Safe report projections, actual catalogue/digest
validation, wrapper generation, persistence and comparison remain pending in #191 and dependent
issues. No CLI exporter, website, access service or deployed endpoint is introduced by this slice.
Historical metadata remains unknown rather than being synthesized from upload time.

## Projection and comparison requirements

Adapters will validate native report schemas before projecting summaries, preserving harness
outcomes, selected/missing/unrun cases and original inventory/story/expectation identity.
No raw exceptions, unmatched URLs, tool bodies or secrets are copied. A plausible report from
an interrupted process never becomes a complete run.

Benchmark projections separate client duration/status/size from unknown server telemetry.
Do not invent zero attempts or cold-cache state. Compare fingerprints covering mode/transport,
definitions, workload/profile, release/index/model, hardware/environment/client placement and
warm-up/sample/concurrency/timeouts. Unknown or incompatible data blocks dependent claims.
Show errors/sample counts with latency; this is not automatically SLO or capacity evidence.

The public documentation build excludes operational UAT/PROD records from pages, search indexes
and downloads. Local result browsing has no login requirement. A shared UAT/PROD deployment must
protect all admin metadata, artifacts and actions through the approved platform integration
before exposing them; #197 tracks that deployment work. No repository visibility change is needed.
