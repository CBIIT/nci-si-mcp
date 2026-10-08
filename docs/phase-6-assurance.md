# Phase 6 compatibility and assurance

Design credit: **Semantic Infrastructure (SI) team**, *MCP Architecture*, 1 October 2026.
This record covers portable caller permissions, required HTTP integration and offline evaluation
mechanics. It is not production identity, live caDSR, actual-model or IBM-runtime certification.
The owner moved [#180 and #181 to Backlog](deferred-capabilities.md); both remain open and
deferred. Their absence does not suppress any mandatory security requirement.

## Delivered behavior and executable evidence

Paths below identify regression suites in this repository. Local HTTP tests use the real ASGI
application/SDK with synthetic identity; the HTTP acceptance gate also launches an actual
server process against fixture upstreams. Neither establishes a live identity-provider trust
relationship. The [governed HTTP guide](governed-http.md) specifies the integration boundary.

| Configuration / behavior | Evidence | Limitation |
| --- | --- | --- |
| Default trusted-local stdio, registry tools/resources/prompts | `tests/test_server.py`; full stdio fixture acceptance | Local default is deliberately unrestricted; never expose it as a secured service. |
| Trusted-local HTTP, handshake 2025-11-25 and single-exchange 2026-07-28 | `tests/test_transport.py`; `tests/test_release_selection.py`; HTTP fixture acceptance | Single-exchange requests have no session pin, even with stateful configuration. |
| Required HTTP startup and verified identity binding | `tests/test_http_access.py::RequiredAccessTest` | An operator-installed approved verifier/policy integration is necessary; stock code supplies no production provider. Missing integration fails before listening. |
| Missing/invalid/expired/wrong issuer/audience token and missing scopes | `tests/test_http_access.py::GovernedBoundaryTest`; `tests/test_transport.py::HTTPAuthTest` | Synthetic verifier tests bind the SDK boundary, not JWT cryptography or production revocation freshness. |
| Capability-filtered discovery, guessed calls, resources/prompts, denied workflow children | `tests/test_permissions.py`; `acceptance/tests/test_permissions.py` | Per-request policy is authoritative; profile selection cannot grant missing capability. |
| Concurrent callers, revoked/expired/unavailable policy and cursor replay | Both permission suites | Fixture policy changes are immediate; production freshness must be specified separately. |
| Private cache hints and HTTP no-store, including explicit release and refusals | `ProtectedHTTPTest`, `RegistryPermissionTest`; security acceptance cases | Previously delivered content cannot be recalled. Raw storage is not an authorization grant. |
| Stateful session ownership across issuer/tenant/client and replica restart/misrouting | `ProtectedHTTPTest`; `GovernedBoundaryTest.test_stateful_replicas_require_affinity_and_reauthentication_after_restart` | Affinity belongs to the load balancer. Another/restarted replica returns 404; no distributed session migration. |
| Implicit NCIt pin, explicit override, content failure and withdrawal | `tests/test_transport.py::HTTPTest`; `tests/test_release_selection.py` | A new session can choose a newer release. Supply explicit release for multi-call stateless tasks. |
| Required mode cannot silently become unrestricted stdio; probes/host admission | `RequiredAccessTest`; `GovernedBoundaryTest`; transport tests | Health/readiness disclose status only; readiness does not prove upstream availability. |
| Evaluation scoring and reproducible fixed recipes | `tests/test_task_evaluation.py`; `tests/test_assisted_runner.py`; [measured report](assisted-evaluation.md) | Offline fixture mechanics; no model quality, generated-prose support or server-side benefit claim. |
| Internal ask and wxMCP runtime | No executable path or public tool; [deferred research strategy](deferred-capabilities.md) | No pilot-on compatibility claim, provider dependency, gateway experiment or production adoption. |

## Upgrade, policy changes and safe rollback

1. Retain trusted-local mode only for an intentionally trusted local environment. For secured
   HTTP, install the approved integration and set required mode, its factory, external host/
   origin allowlists and approved TLS/network routing. Test missing integration as a startup
   failure before enabling traffic. Do not use forwarded identity headers as authority.
2. Treat policy/identity configuration changes as a session boundary: drain/restart affected
   replicas and require clients to authenticate and initialize again. Ordinary policy updates
   are also checked on each request. A pinned release never preserves stale authorization.
3. Do not copy in-memory sessions or cached authenticated responses across principals or
   deployments. A replacement replica returns 404 for an old session; its replacement may
   resolve a newer implicit release. Explicit release arguments preserve caller intent.
4. Roll back code only while retaining required authentication and the approved boundary.
   A pre-Phase-6 version lacking the startup guard is unsuitable for that secured configuration;
   keep service unavailable or obtain an independently approved boundary. Never regain
   availability by silently changing to trusted-local. The database/index rollback procedure
   is separate from security rollback; Phase 6 adds no database schema migration.

## Acceptance preservation

The immutable [starting evidence](evidence/phase-6/baseline.json) pins `bb50f91d5c034fbba37c69077e372b2d12450928`
and its 944 cases. Comparing the current expected file preserves all 944 identifiers and
outcomes unchanged; 20 new cases pass, giving 964. No expectation was removed or weakened.
All additions below belong to `acceptance/tests/test_permissions.py`; before count is zero.

| Test function | Before → after passing cases |
| --- | --- |
| `test_each_caller_sees_only_its_permitted_tools_and_resources` | 0 → 2 |
| `test_a_guessed_tool_is_refused_without_upstream_reads_or_sensitive_logs` | 0 → 1 |
| `test_guessed_resources_and_prompts_cannot_bypass_permissions` | 0 → 1 |
| `test_allowed_workflows_cannot_read_denied_children` | 0 → 3 |
| `test_grounding_checks_each_selected_child_before_any_read` | 0 → 5 |
| `test_revoked_and_expired_policy_deny_the_next_request` | 0 → 1 |
| `test_missing_policy_never_falls_back_to_trusted_local_access` | 0 → 1 |
| `test_concurrent_verified_callers_keep_separate_catalogues_and_results` | 0 → 1 |
| `test_explicit_release_content_and_discovery_disable_shared_http_caching` | 0 → 1 |
| `test_unauthenticated_and_spoofed_callers_cannot_enter_mcp` | 0 → 3 |
| `test_public_probes_reveal_only_status_and_reject_an_untrusted_host` | 0 → 1 |

The generated [behavioral stories](behavioural-tests.md) map every collected case, including
governed access. HTTP fixture acceptance has four explicitly required `unprepared` skips;
the stdio fixture gate must still run all 964 cases. No security case is waived by those skips.
The separate furnished-baseline milestone and `acceptance/approved.yaml` are unchanged.

## Review and release evidence

Issue PRs #183–#187 contain the implementation/desk-assessment gates and strict TDD evidence
where behavior changed. The assurance PR and final milestone PR add current local gate results,
the five review rounds, mutation dispositions, exact-head CI/clearance and post-merge verification.
The release is not considered complete until CI, Audit, CodeQL and Release pass on its merge
commit, all remaining Phase 6 issues close, milestone 8 closes through the API, installed version
metadata refreshes, and owned scratch/branches/processes are removed. Deferred Backlog issues
#180/#181 are intentionally excluded from closing references.

The [evaluation report](assisted-evaluation.md) names measured inputs, source hashes, releases,
request counts and fixture latency. It must be regenerated if its runner/scorer changes during
review. Its latency is not a production benchmark and its fixed recipes are not real-model
accuracy evidence. Independent fresh domain review, live caDSR, production identity/hosting,
model execution and hosted wxMCP checks remain unexecuted and explicitly out of this release.
