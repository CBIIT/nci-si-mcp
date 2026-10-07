# SI architecture alignment

**Status:** D1–D3 portable enforcement implemented by #177; #178 adds required HTTP configuration
and identity binding. Production identity remains deferred/off; D5 integrations retain their gates.
**Date:** 7 October 2026. **Delivery:** [Phase 6 plan, #175](https://github.com/CBIIT/nci-si-mcp/issues/175).
The [owner decision on #176](https://github.com/CBIIT/nci-si-mcp/issues/176#issuecomment-6043097407)
approves the [issue plan](https://github.com/CBIIT/nci-si-mcp/issues/176#issuecomment-6043062487).
`spec/` remains the source of record; #177 activates the caller-policy and denial contracts.

Design credit belongs to the **Semantic Infrastructure (SI) team**, *MCP Architecture*, SI Team
Meeting, 1 October 2026, slides 2–4 (source: `MCP High Level Architecture SI Team Meeting - 20261001.pptx`).
The presented MCP was not built. This repository supersedes that proposal; there is no separate
implementation to migrate. Its single deployable and semantic core are retained.

## From the proposal to this codebase

| SI component | Existing implementation | Phase 6 disposition |
| --- | --- | --- |
| Unified MCP and domain tools | `server.py`, `registry.py`, content and workflow modules | Implemented; reuse the same handlers and records. |
| EVS, caDSR and Shared SI access | `evs.py`, `cadsr.py`, `ssis.py`, shared `http_client.py` | Implemented; caDSR fixture evidence is not live credentialed validation. |
| Authentication, authorization and audit | SDK authentication and policy, required HTTP entry point, `audit.py` | Portable implementation in #177/#178; production identity remains deferred/off. |
| Focused MCP addresses | One `/mcp` address | D4 retains one address. No aliases without a demonstrated consumer need and decision. |
| `ask(question)` planner | Deterministic workflows and client prompt templates | Conditional: measure the client baseline in #179; a server pilot needs its own decision in #180. |
| wxMCP integration | Direct MCP is the conformance reference | Bounded evaluation in #181; no replacement or platform adoption approved. |
| Protocol DB | No implemented client or published contract used here | Deferred until contracts, access and scope exist. |

## Approved portable decisions

The owning issue changes behavior, `spec/`, its generated reference and acceptance tests together.
Portable enforcement and the permission_denied record are implemented in #177; later integrations
remain subject to their separate decisions.

| Decision | Approved contract | Activation |
| --- | --- | --- |
| D1 — Governed access | Opt-in secured mode intersects the deployment profile with verified caller capabilities. For a fixed authority context the catalogue remains stable across releases and upstream failures. Trusted-local behavior remains available explicitly. | #177, #178; M1.2/M1.5 and M5.1 amendments |
| D2 — Isolation | Secured discovery, content and refusals use `ttlMs: 0`, `cacheScope: private` and HTTP `Cache-Control: no-store`. No shared authenticated response cache. Unrestricted mode retains its existing policy. | #177, #178; M2.1–M2.3 amendments |
| D3 — Denials | Add `permission_denied` through the one shared error path. Return a generic next-step message and correlation ID, without disclosing restricted identifiers or required capabilities. | #177; error record and output unions |
| D4 — HTTP boundary | One `/mcp` endpoint; portable SDK/policy wiring and local HTTP tests. Production identity/Cloud One integration stays disabled and unvalidated until separately approved. | #178 |
| D5 — Conditional work | Offline baseline/evaluation mechanics proceed. Real-model execution, provider/data/spend, generated capability, pilot entry/shipping and hosted wxMCP experiments/adoption retain their later decisions. | #179–#181 |

### Identity, policy and nested execution

The transport supplies validated identity; arguments, prompts, upstream text, session IDs and
cursors cannot supply it. The identity key includes issuer and subject and, where applicable,
tenant and client. An immutable per-call authority snapshot carries the approved capabilities,
policy version and expiry. Unknown capabilities grant nothing. A missing, expired or unavailable
required policy denies protected work; it never falls back to the unrestricted local mode.

Every request revalidates authority, including a continued page and a request in an existing
session. In-flight work uses its bounded snapshot; new child operations must not continue beyond
its expiry. Document the production provider's revocation freshness before enablement; a local
fixture is not proof of immediate remote revocation. Previously delivered content cannot be recalled.
Security-relevant configuration changes invalidate affected sessions rather than preserving stale
authorization or another principal's release pin.

The capability map must cover all tool, prompt and resource surfaces, compound dependencies and
argument-selected branches. Workflows currently call content producers and upstream clients
directly as well as through the registry. Check before each restricted producer/client access;
do not force all internal calls through public MCP just to authorize them. Prompt rendering and
resource reads use the same policy. Hidden or denied names cannot be invoked by guessing them.
Cursor contents remain continuation data, not permission; reauthorize on every page and reject
incompatible continuation context without revealing protected content.

A6.0 keeps the upstream platform authoritative. An application permission cannot grant upstream
rights, and a shared service credential is not end-user delegation. Inbound and upstream
credentials remain separate and origin-bound. The stock server must not trust arbitrary forwarded
identity headers. Any later proxy identity integration needs an authenticated, documented trust
boundary and tests for direct access and spoofed headers.

### Errors, caching and sessions

D3 distinguishes a forbidden operation from bad input or an unavailable platform capability.
HTTP authentication failures remain SDK 401 responses; transport scope refusal uses the SDK's
403 path. An authenticated operation denied by the module uses its structured `permission_denied`
error, not an empty result. All paths preserve safe correlation/audit and reveal no credential.
The #177 output schemas include this code.

D2 governs actual response headers as well as MCP hints, including supported protocol versions
without native hint fields. A private catalogue alone does not protect a cached tool/resource
result. Verify isolation after policy changes and between concurrent principals. Existing raw
upstream/index storage is not an authorization decision; all outward reads are checked.

X-22 remains unchanged: implicit NCIt calls use the session pin; explicit calls do not replace it.
Stateless calls resolve per call and multi-call tasks need an explicit release. Stateful sessions
belong to one replica; misrouting/restart returns the documented session failure, never a new pin
silently. Test principal ownership independently of successful load-balancer routing.

## Decisions deliberately left for later

| Decision | Current state | Evidence needed to enable |
| --- | --- | --- |
| Production identity and hosting | Deferred/off under D4 | Approved issuer/audience, capability mapping, revocation freshness, proxy/network boundary, operator inputs and live integration evidence; security owner decision. |
| Model/provider and transcript policy | Deferred/off under D5 | Approved model revision, spend and permitted data classes for the entire transcript: caller values, retrieved/licensed metadata, tool results and traces; retention, region, training use and egress policy. |
| Server-side generated capability | Conditional, not approved | #179 unmet-need evidence, explicit A9.2 written amendment naming capability and owner, output schema and M1.4 annotation decision, then #180 paired shipping evidence and owner decision. |
| wxMCP experiment/adoption | Desk evaluation only until its gate | Version-specific feasibility and approved effort/spend limits before hosted/adapter work; adoption separately scoped. No license purchase or infrastructure provisioning. |

Generated explanations are not a platform capability under current A9.2. A future amendment
must leave retrieved evidence unchanged and clearly separate generation from retrieval. Any ask
result is computed: **0/private even with an explicit release**, with protected HTTP no-store;
child-call cache hints cannot overwrite this. No ask tool is added to discovery by this record.

#179 delivers an [offline fixture baseline](../assisted-evaluation.md); fresh independent
domain review and actual external-model planning remain unmeasured. The target evaluation
requires a domain-reviewed, held-out task set. Pre-register success, evidence support, correct identities/releases, abstention,
request bounds, failures/incomplete runs, latency, tokens and cost before measurement. The
baseline can establish an unmet need, not server-side superiority. #180 needs paired candidate
evidence under comparable models, data, releases and budgets before shipping. Offline model
doubles test mechanics only; unavailable approvals lead to an explicit defer, not invented results.

If approved later, ask must use a host-built evidence ledger, nonrecursive allowed tools,
inherited total request/retry budgets, cancellation/deadlines, byte/token/cost limits and bounded
local admission. Platform quotas remain authoritative under A6.5. Verify direct tools remain
responsive under competing load. A rejected pilot leaves no unsupported executable feature.
The wxMCP evaluation stops if it needs broad REST exposure, duplicated domain logic, incompatible
credential delegation, weaker conformance or more than its approved budget.

## Acceptance mapping and evidence

AUTH-1–AUTH-3 below are now executable as X-25–X-27; the other identifiers remain reserved
**in this decision record only**. Their owning issues activate requirements and assertions together; no active requirement is weakened or
marked failing merely to reserve a future feature. Each new behavioral case gets a domain story
and remains covered by the generated catalogue's completeness check.

| Planned requirement | Observable evidence | Owner |
| --- | --- | --- |
| AUTH-1 | Fixed-context catalogue and prompt/resource selection; guessed targets refused. Existing P-1/P-5/P-6/P-8/P-9 retain unrestricted behavior. | #177 |
| AUTH-2 | Allowed parent/denied child makes zero restricted requests across every argument-selected workflow path. | #177 |
| AUTH-3 | Principal collision, policy expiry/change/outage, cache and cursor replay tests cannot leak content or grant permission. | #177, #178 |
| HTTP-1 | Missing/invalid/expired/wrong issuer/audience tokens, spoofed headers, startup without required auth, and two replicas preserve the approved boundary and X-22. | #178 |
| EVAL-1 | Scoring includes failed/incomplete runs and missing evidence; deterministic execution tests are labeled separately from actual model quality. | #179 |
| ASK-1 | Conditional only: forged citations, mixed releases, hidden tool errors, recursive calls, cancellation and concurrent exhaustion fail safely; implicit/session/explicit outer results stay 0/private. | #180 |
| GW-1 | Versioned gateway matrix reports every unsupported/untested requirement and stops at its approved limits. | #181 |
| COMPAT-1 | Local/secured, protocol, stateful/stateless and conditional pilot modes have explicit tests/omissions plus secure upgrade/rollback evidence. | #182 |

The [Phase 6 starting evidence](../evidence/phase-6/baseline.json) identifies the commit, suite
digest, exact collected case inventory digest and expected outcomes. The inventory itself is
recoverable from the pinned commit's `acceptance/expected/fixture.json`; the fresh run is compared
case-for-case before its digest is recorded. This is an implementation comparison point, **not**
a furnished-suite approval, new tag or change to `acceptance/approved.yaml` (milestone 7 remains separate).

Implementation uses strict red/green/refactor TDD with observable assertions and retained red/green
evidence in issue PRs. Inspect line and branch misses rather than relaxing exclusions or thresholds.
#176 was documentation-only; #177 adds secured fixture cases without changing unrestricted outcomes. Documentation, generated
specification, usage examples, diagrams and behavioral stories move with each implementing change.
Mandatory security gaps block completion; optional integrations can complete as owner-approved
deferred/off with their unexecuted checks visible. Neither a skip nor a fixture run proves live readiness.

The ADR labels AUTH-1, AUTH-2 and AUTH-3 map to executable requirements X-25, X-26 and
X-27 respectively. This preserves the established P-/X-/tool requirement-ID convention.
