# Phase 7: public documentation and restricted validation companion

Design credit: **Semantic Infrastructure (SI) team**, MCP Architecture, 1 October 2026.
Baseline main `4bea455a5540d6a947f2aca5245f8ed703d5891c`, v0.16.0; branch `milestone/phase-7`.
Existing capabilities: 29 tools, 964 fixture cases, HTTP acceptance, stdio benchmarks and
Markdown/diagrams/stories. Phase 7 adds a public static website and a separate local results
dashboard, bounded job controls and advisory configuration proposals. The delivery order below
records how these capabilities were built; UAT/PROD platform integration remains deferred.

## Final owner-confirmed boundary

Owner addition, 8 October 2026: #196 also verifies Section 508 and applicable federal/HHS
website requirements using the [government website assurance plan](government-site-assurance.md).
Record measured checks separately from deployment-only obligations and unverified manual checks.

The repository, source and normal engineering CI remain public; no repository status or access
setting changes. Local development exposes documentation, results, test/benchmark controls and
configuration features to anyone with repository access, without an application account or
institutional login. Bind local services to loopback by default; this is a local operator tool,
not a publicly deployed administration service.

ONLY UAT and PROD instances require protection. Public documentation and narrative stories
remain anonymous. Their result dashboards, downloads, test/benchmark controls and configuration
functions require platform-provided authentication AND explicit maintainer-admin authorization.
The roster is a small maintaining team. No bespoke IdP, users/passwords, token issuance or policy
service. Platform integration is deferred in #197 until the actual NCI/Cloud One choice is clear;
UAT/PROD admin exposure remains disabled until that integration is verified. Local functionality
is delivered now and does not depend on #197. Config inspection/proposals (#198) are included.

EVS/caDSR/other upstreams own their authorization and data restrictions; this phase does not
introduce another upstream permission service. Existing MCP behavior remains unchanged. Preserve
ordinary engineering hygiene: no credential logging/commits, no arbitrary command endpoint,
no accidental production target mutation, no weakened upstream checks. These do not create a
new institutional auth system. Operational UAT/PROD data must not enter public docs/CI artifacts;
ordinary repository fixture CI results and historical committed evidence remain public.

## Delivery order

| Order | Issue | Outcome |
| --- | --- | --- |
| 1 | #191 | Versioned restricted run envelope and safe report projection |
| 2 | #192 | Public versioned documentation with local search/assets/diagrams |
| 3 | #193 | Local results dashboard, run status and CI documentation artifacts |
| 4 | #194 | Bounded HTTP benchmarks and isolated operator validation profiles |
| 5 | #199 | Local bounded run/cancel controls over isolated worker profiles |
| 6 | #198 | Local effective configuration and validated change proposals |
| 7 | #195 | Local companion composition and cloud-neutral Cloud One blueprint |
| 8 | #196 | Accessibility, compatibility, security, review and release assurance |

Serve documentation independently of MCP. No portal proxy onto MCP/upstreams, no Docker socket,
no public state hooks or production volumes in workers. Use a small local admin process and isolated bounded workers; no distributed scheduler or custom identity backend. Actual Cloud One ingress/TLS/storage/identity remain owner decisions.

## Evidence, version and isolation requirements

- Acceptance JSON lacks run times, exit status, selected inventory and tested-server identity.
  An independent envelope binds report bytes by digest to runner/source/profile, reviewed
  inventory/story/expectation digests, start/end, termination and known/unknown tested identity.
  Suite identity is not server identity; checksums are not trustworthy origin. Historical/local
  imports remain unverified and cannot prove freshness, completeness or deployment conformance.
- Preserve native outcomes, missing/unrun cases and historical mapping at its original revision.
  Record failed/cancelled/no-report attempts when the wrapper can; latest attempted run is
  distinct from latest complete evidence. Bounded sizes, immutable IDs and atomic writes;
  explicit retention/gaps. Public docs artifacts have 30-day CI delivery retention, not archival
  guarantees. Restricted operational storage/retention needs an approved environment.
- Clean staging with reviewed explicit page/asset allowlist; never recursively copy docs/.
  Reject symlinks/path escapes. Sentinel tests inspect ALL output including downloads, search
  indexes and source maps for private results/raw files/secrets. Ordinary public engineering CI stays intact; new UAT/PROD operational inputs stay outside it. No privileged promotion of PR artifacts; separate namespaces/cache trust.
- Distinguish source commit, build channel, package version and verified release tag. Main/PR
  artifacts are unreleased until tag-to-commit verification; stale SCM version is not the label.
- HTTP client measurements cover MCP status/duration/size. Server attempts/cache/source/replica
  identity remain unknown without trusted correlated telemetry; no new audit-fetch endpoint.
  A new HTTP session is not a cold server. Keep existing stdio definitions unchanged.
- Benchmark comparison fingerprint includes transport/mode, definitions, scenario/argument
  digest, profile, release/index/model, hardware/environment/client placement, warm-up,
  sample/concurrency/timeout settings. Unknown/incompatible data blocks speedup claims. Show
  errors/sample counts; small-sample percentiles are descriptive, never SLO/capacity evidence.
- HTTPS/certificate verification for remote targets; exact normalized origin/path, no userinfo,
  query credentials, redirect forwarding or downgrade. Plain HTTP only for explicitly selected
  local disposable fixtures. Aggregate deadline/admitted-call budget includes warm-up/handshake;
  bounded concurrency/per-call timeout, no whole-campaign retries. Client cancellation does not
  prove in-flight server termination. Production read-only probes are explicit opt-in and never
  invoke fixture state hooks/restarts or claim full live conformance.
- Full fixture validation uses separate disposable network/index/volumes and an environment
  allowlist. No fixture ports published, production credentials inherited or production network
  attached. Loopback host bindings locally; integration tests verify isolation and cleanup.

## Documentation stack and accessibility

Evaluate maintained **Zensical** first in #192 with a bounded Python 3.14/PDM/Markdown/Mermaid/
offline-asset check and a pinned docs-only dependency. Material for MkDocs is in maintenance
mode; no uncritical new adoption. Never enable PDM uv mode or add a core dependency. Reuse
existing/generated source documents. Design/test against WCAG 2.2 AA: semantic headings/tables,
keyboard and visible focus, non-color statuses, textual diagram/chart alternatives, zoom/mobile
and essential no-JavaScript access. Manual keyboard/screen-reader checklist; no certification claim.

## Identity and configuration decisions

NIH RAS is a credible mixed NIH/external OIDC candidate with CRDC production precedent, not an
SI registration or admin policy. Confirm the actual maintainer roster and approved team identity
service for UAT/PROD with NCI/CBIIT/NIH IAM, client/audience, assurance, revocation and explicit enrollment.
Dataset visas/email domains grant no job/config rights. No custom IdP, users/password database, token service or policy engine. #197 covers every result/job read/action, CSRF/sessions,
object-level target/job rights, policy outage/revocation, audit/quotas and worker recovery.

#198 delivers local curated secret-free effective configuration and validated change proposals, also usable behind the approved UAT/PROD platform boundary. No settings/environment dump, credential disclosure, arbitrary paths/commands/URLs
or ability to disable its own security. Deployment tooling remains authoritative; no competing
mutable config database. Validate changes, scope permissions, check revisions, audit and rollback.

Sources checked 8 October 2026: [NIH RAS](https://datascience.nih.gov/researcher-auth-service-initiative),
[service offerings](https://auth.nih.gov/docs/RAS/serviceofferings.html),
[Material maintenance](https://github.com/squidfunk/mkdocs-material/issues/8523),
[Zensical](https://zensical.org/docs/get-started/), [WCAG 2.2](https://www.w3.org/TR/WCAG22/).

## Adversarial review dispositions

Two independent security/evidence agents found 13 gaps; all accepted, none rejected:

| Finding | Mitigation | Issues |
| --- | --- | --- |
| S1 accidental raw assets | Clean staging/allowlist, full-bundle sentinels | #192 #193 |
| S2 forged trust | External envelope bound to digest; claims cannot upgrade trust | #191 #193 |
| S3 private metadata leak | UAT/PROD results behind platform admin access; public docs exclude operational data | #191 #193 #195 |
| S4 worker isolation | Separate network/volumes/env, no fixture ports | #194 #195 |
| S5 unsafe credential transport | HTTPS remote, exact targets, no redirect forwarding | #194 |
| S6 aggregate load/cancellation | Total budget/deadline; honest cancellation | #194 |
| E1 no execution/server envelope | Independent metadata; unknown identity explicit | #191 |
| E2 missing remote audit | Client-observed versus unknown telemetry | #194 |
| E3 catalogue drift | Revision-bound inventory/story/expectation digests | #191 #193 |
| E4 misleading trends | Full comparison fingerprint, error/sample counts | #191 #193 #194 |
| E5 version confusion | Commit/channel/package/tag distinct | #192 #193 #196 |
| E6 inaccessible views | Semantic/keyboard/no-JS alternatives, checklist | #192 #193 #196 |
| E7 hidden failed attempt | Latest attempt separate, no-report/retention gaps | #193 #196 |

The final owner correction limits platform protection to UAT/PROD and permits all local features without application login. It preserves public repository/engineering CI and upstream-owned data authorization. These corrections supersede the earlier blanket deferral; reviewers recheck the updated issues. No verified finding
is left as a future fix without a mitigation in its issue.

## Workflow and completion

One issue at a time, plan comment before code, strict TDD and same-change docs. Owner requested
implementation and delegated reviews; reserved hosting/identity decisions remain deferred.
No MCP contract/expected-outcome movement planned. Full server/harness/hooks/prepared fixture
ratchet and relevant HTTP/browser checks. Five-pass review to convergence, independent mutation
review, exact-head clearance and green CI before main merge; verify CI/Audit/CodeQL/Release,
then close delivered issues/milestone and clean up. #197 stays Backlog. No provisioning,
public hosting/settings changes, paid services, new secrets or live caDSR/Cloud One certification.


## Adversarial recheck resolution

R1: results and admin controls are now expressly LOCAL deliverables; UAT/PROD exposure waits for #197. R2: the owner explicitly preserves public repository status and existing engineering CI. Only deployed UAT/PROD administration is platform-protected. No scope question remains. #198 moves from Backlog to this milestone under this clarification; #199 adds bounded local job control.

Final configuration recheck: #198 is local inspection plus validated proposals only, no live settings mutation. Views identify their selected process/trusted snapshot; remote values remain unknown without evidence. Local tests cover filtering/proposals/revisions/no-apply; platform denials belong to #197 and any future apply/restart/rollback requires separate scope. Both reviewers report no further findings after these corrections.
