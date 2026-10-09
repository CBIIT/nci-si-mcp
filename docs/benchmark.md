# Unified-profile validation and benchmark

The Phase 5 evidence separates three things: specification acceptance against fixtures,
the suite's live-capable protocol checks, and representative live tool measurements.
A passing fixture case never establishes access to a credentialed service or support for
a requested future platform contract.

## Reproduce the benchmark

```bash
pdm run python scripts/benchmark.py --repetitions 20 --output tmp/benchmark-fixture.json
pdm run python scripts/benchmark.py --mode live --repetitions 20 --output tmp/benchmark-live.json
```

Run these sequentially. `--case TOOL` selects a case; `--repetitions 1` is an orchestration
smoke test, not the published measurement. Each run exercises the actual MCP stdio boundary
with the examples and NCIt pin from `acceptance/tests/calls.yaml` and the fixture manifest.
The ten scenarios cover EVS lookup and lexical search, caDSR lookup/form/search/matching,
a Shared SI join, and all three workflows. Credentialed fixture scenarios apply only in
fixture mode; live mode never receives their synthetic credential or answer.

- **Cold:** a fresh server process and data directory for each measured call. MCP startup
  and initialization finish before timing begins; this does not measure deployment startup.
- **Warm:** one process, one excluded priming call, then twenty measured calls. The MCP
  client's response cache is disabled. The server's actual outbound attempt count records
  what it reuses; “warm” does not promise zero upstream requests.
- **Timing:** wall-clock client call duration, in milliseconds. p50/p95 use nearest rank
  (`ceil(n × fraction)`). Tool errors remain in the distribution and error-rate denominator.
- **Size:** UTF-8 bytes of compact structured content, excluding the MCP envelope. Counts,
  response codes and addressed release/registry identity come from the uniquely correlated
  completion record. Missing or contradictory evidence fails the run.

The benchmark client's read timeout is 300 seconds, allowing normal upstream timeout/retry
cycles to finish; the acceptance helper's ordinary 60-second default is unchanged. Reports
name the expected scenarios and remain `complete: false` until all finish. The initial live
attempt stopped during `get_form` at the former 60-second client timeout; its completed
samples and explicit interruption are retained separately, with no invented outbound count
for the interrupted call.

The reports retain every sample and aggregate, the suite/fixture identity, installed package
version, Python/platform, and SHA-256 of the server sources and benchmark runner. An absent
caDSR registry identifier stays absent. Both success and error responses are measured, so a
fast refusal is not evidence of a fast successful service. No result bodies or credentials
are written to the report. Owned temporary directories and server processes are reaped.

The recorded workstation is an Apple M4 Max with 128 GiB RAM. These sequential-call results
are not a load test, an SLO, or Cloud One capacity sizing. No index is prepared: search is
lexical, and no sample supplies a production quality or latency floor. Full-corpus semantic
retrieval has its own [evaluation evidence](retrieval-evaluation.md).

## Bounded HTTP measurements

The HTTP runner uses a separate version-1 report; the stdio commands and historical definitions
above are unchanged. Design credit: the Semantic Infrastructure (SI) team. Run the fixed local
profile with no credentials or application login:

```bash
pdm run operator-worker benchmark-http-fixture --output tmp/http-benchmark/report.json
pdm run operator-worker acceptance-http-fixture --output tmp/http-acceptance/report.json
```

The benchmark profile runs all ten furnished examples, including the cross-domain workflows,
against an owned HTTP server and recorded upstream fixtures. Five first-call and five warmed
measurements per case follow one warm-up in the warmed session. Fixture credentials are
synthetic. No production data directory or deployment environment is inherited. Acceptance
uses its full prepared HTTP fixture suite and a private report path, retaining its four documented
remote-unprepared skips. Temporary data, logs, control sockets and owned children are cleaned up.
These processes use loopback, not an OS network sandbox: the network-isolated worker composition
in #195 must deny external egress and publish no fixture ports. Do not deploy this local worker
as a production administration endpoint.

Remote measurements are a separate, explicitly authorized **read-only probe**, not a live
conformance run. The target must exactly match a normalized HTTPS allowlist entry. TLS
certificates are verified; redirects, URL credentials, query strings and fragments are rejected.
There are no remote prepare/restart/state hooks. If required, supply the Authorization header
through `NCI_SI_BENCHMARK_AUTHORIZATION` in the process environment, never a command argument.

```bash
pdm run benchmark-http --target https://approved.example/mcp --allow-target https://approved.example/mcp --allow-remote --case get_concept --output tmp/remote-probe/report.json
```

The example is a placeholder; select an endpoint you are authorized to measure. Furnished cases
retain the requested NCIt release from the fixture manifest; this does not establish the release
actually served remotely. A release refusal remains a measured error, not a successful lookup.

| HTTP measurement | Meaning |
| --- | --- |
| First call | First measured call in a new **client session**, never claimed to be a cold server |
| Warmed call | Same client session after recorded warm-ups; server cache state remains unknown |
| Latency/size | Client tool-call duration and compact UTF-8 structured-result bytes; no result bodies retained |
| Requests | HTTP requests admitted by this client, including setup, warm-ups and cleanup; **not upstream attempts** |
| Errors | Tool errors and client failures remain explicit; a timeout has unknown result size |
| Server telemetry | Upstream attempts, cache state, source commit and replica are unknown; no audit endpoint is invented |

Concurrency is fixed at one. Defaults are 500 total HTTP requests, 120 seconds aggregate,
15 seconds per operation, five measured repetitions and one warm-up. Remote CLI options can
lower or raise these within hard bounds of 1,000 requests, 1,200 aggregate seconds, 120 seconds
per operation, 20 repetitions and three warm-ups. Initialization is outside tool-call latency
but inside the campaign budget. Fixture process startup has a separate 15-second readiness bound.
Owned process termination waits up to ten seconds before killing and reaping the child.

Cancellation and budget exhaustion stop new request admissions, preserve incomplete evidence
and close client resources. They do not establish that in-flight remote work stopped. There is
no whole-campaign retry. Reports are saved atomically before HTTP work and after each sample;
all selected cases remain visible even when unmeasured. HTTP 4xx/5xx, redirects and oversized or
compressed responses fail closed without retaining upstream bodies. The response limit is 8 MiB.

p50/p95 use nearest rank and include errors; warm-ups have their own counts and are excluded
from measured phases. Five samples are a small diagnostic sample, not an SLO or capacity claim.
The [dashboard](local-validation.md) and [evidence projection](evidence-contract.md) preserve
these definitions. Missing comparison dimensions block comparisons with other HTTP runs or
historical stdio results. Raw reports alone are not execution envelopes; #199 supplies bound
local run records and browser controls.

## Acceptance evidence and its limits

Run the required gates and prepared fixture suite as described in
[CONTRIBUTING](../CONTRIBUTING.md). The HTTP run is `pdm run acceptance-http`; its four
remote-unprepared skips are a deliberate transport contract and remain visible.
Run the live-capable suite with:

```bash
NCI_SI_ACCEPTANCE_MODE=live NCI_SI_ACCEPTANCE_PREPARE='nci-si-mcp index-sample $(cat "$NCI_SI_ACCEPTANCE_INDEX_CODES")' pdm run acceptance -n 4 --report=live.json
pdm run python -m nci_si_acceptance.report acceptance/fixture.json --live acceptance/live.json
```

At this snapshot the suite has **16 live-capable protocol cases**; the **928 remaining cases
are fixture-only**, including content assertions and 25 resource/correlation protocol gates.
The live report records them as `not_live` and lists those 25 gates as unrun. The combined renderer
retains the fixture PASS verdict when no live test failed. Read that table together with
the live coverage counts: it is not proof of live content acceptance. The suite identity
also reads **MODIFIED**, because this is not an approved furnished suite release.

No live acceptance failure was invented to produce a `PASS (fixture only)` row. The known
upstream dependencies still apply, including C-1 registry releases, C-3 keyword search and
C-6 matching parameters/access. The [upstream requirements packages](upstream/README.md)
records their reproduction evidence and affected cases separately; a skipped live test
is never recast as a passed live test. Representative benchmark results supplement the
acceptance evidence and do not replace its assertions.

The [evidence directory](evidence/phase-5/) holds the measured samples and acceptance reports.
