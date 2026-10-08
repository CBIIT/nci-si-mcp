# Local validation dashboard

The companion displays acceptance and benchmark evidence on `127.0.0.1`, independently of the
MCP server. Repository users need no account. UAT/PROD administration remains disabled pending
platform integration in #197. Design credit: the Semantic Infrastructure (SI) team,
*MCP Architecture*, 1 October 2026.

## Open and import

```bash
pdm install
pdm run portal serve
```

Open `http://127.0.0.1:8081/`; Ctrl+C closes the listener. `--port` changes the local port;
there is no public bind option. Results have `no-store` responses, host checks and no external
scripts or analytics. Tables, filters and comparisons work without JavaScript.

The interface uses published NCIDS cerulean (`#00314b`, `#004971`) and neutral (`#f0f0f0`)
colors, with white cards separating the latest attempt from complete evidence. Golden warning
and cranberry failure statuses include words as well as color. Choose **View run** to inspect a
record. **Help & guide** is available
on every page; contextual links beside results explain status, filters, verdicts, provenance,
latency percentiles and the conditions for a meaningful comparison. The guide is served locally
at `/help`, requires no account and makes no third-party requests.
NCI policy and agency links are in the footer. NCIDS component/typography integration is tracked
in the [assurance plan](government-site-assurance.md). Production review is handled separately
by the owner and does not block local development or the Phase 7 release.

A bound bundle follows the [evidence contract](evidence-contract.md): `envelope.json`, optional
`report.json`, and original `catalogue.json`, `stories.json`, `expectations.json`, `selection.json`
for acceptance. Benchmarks may supply their bound selection manifest; without it, comparison
stays unverified. Never substitute the current checkout for missing historical snapshots.

```bash
pdm run portal import /path/to/original-run-bundle
```

Only those fixed filenames are read, symlinks are rejected, and total input is bounded to
24 MiB. Identical imports are idempotent; conflicting reuse of a retained run ID fails.
Hashes validate byte bindings, not producer identity. Imported origin and declared server
identity remain unverified. Importing does not execute tests.

Old reports without a bound original inventory can be retained explicitly without verdicts:

```bash
pdm run portal legacy docs/evidence/phase-5/benchmark-fixture.json --kind benchmark
```

Legacy import records a digest and retains the bytes locally; it does not interpret PASS or
latency claims. Browser uploads are not enabled.

## Run and cancel checks

Choose **Run checks**, a validation profile, then **Start validation run**. The default profiles
run HTTP acceptance checks or representative HTTP benchmarks against disposable fixture services.
One worker runs at a time, with at most two queued jobs. Acceptance workers have a **900-second**
deadline; benchmarks have **240 seconds**, including setup. Worker console output is discarded;
reports are bounded to 16 MiB and complete evidence bundles to 24 MiB.

Each job archives the committed checkout source and captures the original inventory before
execution. **Uncommitted edits are excluded.** The installed Python environment supplies runtime
dependencies; no installation or arbitrary command is accepted through the dashboard. Workers
use separate fixture indexes and do not reconfigure the serving MCP.

Use **Refresh status** to inspect progress and **Cancel this run** to stop queued or owned work.
Available reports remain evidence, including failures and partial results. Missing results are
unknown. Cancellation does not establish that an in-flight remote request stopped. After a
restart, abandoned jobs are marked interrupted and never automatically retried. Repeating the
same retained form submission returns its existing job; a fresh form creates a new attempt.

Remote probes require explicit local startup configuration:

```bash
pdm run portal serve --allow-remote --remote-target https://approved.example/mcp
```

Replace the example with the exact approved HTTPS endpoint. If needed, provide its authorization
header through `NCI_SI_BENCHMARK_AUTHORIZATION`, never through a URL or command argument. It is
passed only to the explicitly enabled remote worker, not fixture workers or inventory collection.
The browser cannot supply targets, credentials, commands or state-changing hooks. This enables
a local operator to probe that endpoint; it does not enable deployed UAT/PROD administration.
See the [profile limits](benchmark.md#bounded-http-measurements) before measuring a remote service.

## Interpret results

- Latest attempt follows an atomic **local import sequence**, not a supplied timestamp.
  Interrupted, cancelled and no-report attempts stay visible.
- Latest complete evidence is separate and may be older. Complete means the selected inventory
  was recorded; failures and skipped/unrun cases remain their native outcomes. Completeness is
  not a PASS verdict, freshness guarantee or proof of live upstream behavior.
- Acceptance views retain original story IDs, expected outcomes, tool verdicts and gate flags.
  Filters change rows, not verdicts. Missing outcomes stay unknown. Case IDs are hashes; raw
  identifiers, unmatched requests and credentials are not displayed.
- Benchmark views put sample/error counts beside latency. Unknown or differing fingerprints
  and incomplete selections block comparison. Matching imported fingerprints still do not
  authenticate origin or establish an SLO/capacity claim.

HTTP benchmark views label first calls in new client sessions, warmed calls and warm-ups
separately. A new session does not establish a cold server. Client timeouts retain their elapsed
time and error status with an unknown result size. See [bounded HTTP profiles](benchmark.md#bounded-http-measurements)
for the fixed local commands, remote opt-in and measurement limits.

## Storage and retention

The default `.nci-si-portal/evidence.sqlite` is Git-ignored and separate from the MCP index.
Use `--store` before the subcommand to choose another local file. Retention defaults to **100
terminal records**; `--retention` accepts 1–1000. New successful imports atomically prune oldest
local sequences; failed imports leave history unchanged. Pruning does not run merely on opening
a store. ID-conflict checks cover retained records. Read failures report unavailable, never empty.

The sibling `evidence.jobs/` directory holds the durable queue and bounded job workspaces.
An exclusive process lock refuses a second controller over the same workspace. Job history uses
submission sequence; imported results use import sequence. The same retention setting bounds
terminal jobs independently of result imports. Completed source snapshots are removed after
measurement; retained original inventories, bindings and reports support reproduction until
pruned. Ctrl+C stops owned workers and releases the lock. A process failure is not a PASS result.

Original bytes are retained for reproducibility and can contain private operational details.
Do not publish, commit or copy this store into the public documentation artifact. An encoded
bundle may occupy roughly 32 MiB plus its bounded projection/database overhead. Keep the file
on local disk; archive authorized evidence separately if needed beyond retention. This limit
is not an agency records schedule.

## Public artifacts remain separate

CI uploads only the reviewed static build at `tmp/docs-site/`, as a per-commit preview retained
for **30 days**. It neither uploads the store nor imports UAT/PROD results. No PR artifact is
promoted to a release or deployed by this job. Existing public engineering CI remains unchanged
in visibility. [Government website assurance](government-site-assurance.md) records the Section
508 and deployment applicability checks required in #196.
