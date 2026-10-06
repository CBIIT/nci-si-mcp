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
C-6 matching parameters/access. The [upstream requirements work](https://github.com/hniedner/nci-si-mcp/issues/42)
records their reproduction evidence and affected cases separately; a skipped live test
is never recast as a passed live test. Representative benchmark results supplement the
acceptance evidence and do not replace its assertions.

The [evidence directory](evidence/phase-5/) holds the measured samples and acceptance reports.
