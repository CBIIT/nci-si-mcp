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
<a href="#getting-started">Get started</a><a href="#run-status">Run status</a>
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
<p>The current dashboard is read-only. Import is a terminal operation; running, cancelling and
changing server settings are not available here yet. Use the same <code>--store</code> option
before the subcommand when importing into a custom store. Local use requires no account.</p>
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
<p>Tool verdicts preserve the acceptance harness's own decision. Individual cases show the
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
calculation. Few samples cannot support a reliable tail-latency or capacity claim.</p>
<p>Cold and warm refer to the report's measurement procedure. A new HTTP session alone does not
establish that a remote server, model or cache was cold. Client-side measurements cannot reveal
unknown server-side activity.</p>
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
    )
