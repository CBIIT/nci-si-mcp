"""Local dashboard guidance, available without scripts, external assets or an account."""

from scripts.portal_views import page


def help_page() -> str:
    return page(
        "Dashboard guide",
        """
<p>Use this workspace to inspect recorded checks of the MCP server. Acceptance evidence answers
“did the tested behavior match the requirements?” Benchmarks answer “what did these measured
calls cost under these recorded conditions?” Neither replaces a deployment readiness review.</p>
<nav class="help-nav" aria-label="Guide topics">
<a href="#getting-started">Get started</a><a href="#run-controls">Run checks</a>
<a href="#run-status">Run status</a>
<a href="#configuration">Configuration</a>
<a href="#acceptance">Acceptance results</a><a href="#benchmarks">Benchmarks</a>
<a href="#provenance">Evidence provenance</a></nav>
<section id="getting-started"><h2>Get started</h2>
<ol><li>Import a recorded bundle from the repository terminal using
<code>pdm run portal import PATH_TO_BUNDLE</code>. A bundle includes its original envelope,
report and required inventory snapshots. It is checked before entering history.</li>
<li>Open <a href="/">Run history</a> and choose <strong>View run</strong>.</li>
<li>Read the run state and evidence limitations before interpreting results. Filter acceptance
cases by tool or story, or choose two benchmark runs to compare their recorded conditions.</li></ol>
<p>If only an older report is available, use <code>pdm run portal legacy PATH_TO_REPORT
--kind acceptance</code> (or <code>--kind benchmark</code>). Its bytes are retained as
<strong>unverified</strong>; missing original inventory is never reconstructed as a pass.</p>
<p>Import is a terminal operation. Use the same <code>--store</code> option before the subcommand
when importing into a custom store. Local use requires no account.</p>
</section>
<section id="configuration"><h2>Recorded configuration and proposals</h2>
<p>Start the selected MCP with <code>serve --configuration-snapshot PATH</code>, then start
the companion with <code>portal serve --configuration-snapshot PATH</code>. Use the same new,
local file path; the parent directory must exist. A running target owns that file and removes
its unchanged snapshot on normal shutdown. An abrupt stop can leave historical evidence;
inspect it before removing it to restart. Do not overwrite another target's record.</p>
<p>The <a href="/configuration">Configuration</a> page shows only a curated set of safe values
captured at startup, with target instance, capture time and revision. It does not prove that
the process is still running or describe an unknown remote deployment. Default means no
environment or CLI override was supplied; environment and CLI identify the target's source.
No value is inferred from the companion's environment.
Missing or invalid evidence stays unknown.</p>
<p>Select a tuning setting and enter a proposed value to preview the recorded before/after
change. Timeouts and retry backoff accept numbers; attempts, response bytes and batch size
require integers; log level uses its named choices. Existing server validation applies.
A changed target/revision requires reloading the form. A proposal is not saved or applied:
the deployment owner reviews it and changes the authoritative environment or IaC, arranging
restart, replica coordination and rollback. This interface cannot change authentication,
trusted origins, credentials, endpoints, index activation or its own access boundary.</p></section>
<section id="run-controls"><h2>Run checks and recover interrupted work</h2>
<p>Open <a href="/jobs">Run checks</a>, choose a fixed validation profile and select
<strong>Start validation run</strong>. Fixture acceptance checks behavior against recorded or
crafted upstream responses. Fixture benchmarks measure representative calls against a disposable
server. Neither establishes live-service readiness.</p>
<p>Each run snapshots committed source; uncommitted edits are excluded. The run details record
that commit separately from any remote server identity, which may be unknown. These controls
do not change the serving MCP's configuration or index.</p>
<p>One worker runs at a time, with at most two queued runs. Acceptance workers have a limit of
900 seconds; benchmark workers have a limit of 240 seconds, including setup. Request and output
limits also apply. A remote read-only benchmark is offered only when the operator explicitly
enables an exact HTTPS target at startup. You cannot enter a target, command or credential here.</p>
<p>Choose <strong>Refresh status</strong> to see progress. <strong>Cancel this run</strong> removes
queued work or stops the owned worker and its processes. Cancellation cannot prove that an
in-flight remote request stopped. A deadline or output limit preserves available evidence and
reports the stop reason; missing outcomes are unknown, never passes.</p>
<p>Interrupted work is never retried automatically. After a restart, abandoned pending or
running jobs are marked interrupted. Inspect their available results before starting a new run.
Resubmitting the same retained form intent returns the original job instead of creating a
duplicate. A fresh form creates a new attempt.</p>
<p>Run checks orders jobs by submission sequence. Results orders imported bundles by import
sequence. A job without a validated bundle remains visible in Run checks even when it has no
result link. Retention bounds both histories; archive needed evidence before it is pruned.</p>
</section>
<section id="run-status"><h2>Read run status</h2>
<p><strong>Latest attempt</strong> is the most recently imported record, including interrupted or
failed work. <strong>Latest complete evidence</strong> is the most recent record with a complete
reported inventory. A complete run can still contain failures. The cards may therefore name
different runs.</p>
<dl><dt>Completed</dt><dd>The recorded execution finished; inspect its actual results.</dd>
<dt>Failed</dt><dd>The execution recorded failure. Available outcomes remain visible.</dd>
<dt>Cancelled / Interrupted / Unavailable</dt><dd>Execution or reporting did not complete normally.
Missing outcomes remain unknown, never successful.</dd>
<dt>Unverified</dt><dd>A legacy report lacks the original evidence needed for interpretation.</dd>
</dl>
<p>History uses local import sequence, not report timestamps. Reimporting the same retained bundle
does not create a new attempt. By default only the newest 100 imports are retained; configure
<code>--retention</code> from 1 to 1,000. Pruned records are unavailable in this store.</p>
</section>
<section id="acceptance"><h2>Understand acceptance results</h2>
<p>Tool verdicts are shown only for complete evidence and preserve the harness's own decision.
Incomplete runs retain their recorded cases but show no tool verdict. Individual cases show the
original expected outcome alongside the recorded outcome. An expected fixture result is a
regression baseline, not a substitute for the requirement or proof of production correctness.</p>
<p>A gate checks a shared requirement that can affect a tool verdict even when its ordinary
cases pass. “Gate-only failure” identifies that situation. An unreported case stays unknown.</p>
<p>Tool and Story filters match exact recorded names. Leave either blank to include every value;
leave both blank and choose <strong>Filter</strong> to reset. The displayed case count shows the
filtered subset. Tool verdicts still describe the full recorded run. Case IDs are stable opaque
identifiers; original free-text inputs are not shown.</p>
<p>Fixture results use recorded or crafted upstream responses: they are
<strong>not proof of live-service readiness</strong>, issued credentials or production data quality.
Look at the run mode before drawing a conclusion.</p>
</section>
<section id="benchmarks"><h2>Read benchmark measurements</h2>
<p><strong>Samples</strong> counts recorded calls; <strong>Errors</strong> counts failed calls.
Always read both with latency. <strong>p50</strong> is the middle percentile and
<strong>p95</strong> the 95th percentile, in milliseconds, using the native report's nearest-rank
calculation. Displayed milliseconds are rounded to two decimals; original measurements are
retained unchanged. Few samples cannot support a reliable tail-latency or capacity claim.</p>
<p>Cold and warm refer to the report's measurement procedure. A new HTTP session alone does not
establish that a remote server, model or cache was cold. Client-side measurements cannot reveal
unknown server-side activity.</p>
<p>HTTP reports label the first call in a new client session separately from the warmed client
session. Warm-up rows show their own samples and errors; they are excluded from the two measured
phases. A client timeout remains a failed measured attempt with unknown result size, not a
successful fast response.</p>
<p>Comparison requires matching recorded conditions: transport, workload, environment, model,
release/index, sample policy and other fingerprint dimensions. Unknown or mismatched dimensions
block comparison and list the reason. Matching digests establish consistency of recorded values,
not authenticity of the machine or results. No comparison here establishes an SLO.</p>
</section>
<section id="provenance"><h2>Know the evidence limits</h2>
<p>Origin is unverified for locally imported evidence. A checksum binds bytes; it does not
authenticate the runner. The runner source commit identifies the stated test code, while the
reported server commit may be unknown and is not independently attested.
These are separate facts.</p>
<p>The dashboard displays validated projections, not raw report content. Original bundles remain
in the local SQLite store and may contain sensitive operational material. They are never copied
into the public documentation site. Do not expose this local listener as a deployed admin site:
UAT/PROD access requires the separately approved platform integration.</p>
</section>
<p><a class="action" href="/">Return to run history</a></p>
""",
        section="/help",
    )
