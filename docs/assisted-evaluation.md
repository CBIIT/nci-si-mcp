# Assisted task evaluation

The server uses deterministic tools and workflows. It has no internal generative LLM,
planner or generated SPARQL/SQL. Semantic search can use an embedding model; that is
retrieval, not answer generation. An external MCP client may supply its own LLM.

[#179](https://github.com/CBIIT/nci-si-mcp/issues/179)
investigates the Semantic Infrastructure (SI) team's orchestration proposal without presuming that a server-side
planner improves on the existing workflows.

## What was measured

The [versioned task file](../evaluation/assisted-tasks.yaml) fixes 12 recipes, exact
structured-field rubrics, request ceilings and a development/held-out split. Each arm
runs every task three times. Both arms use real in-process MCP and the recorded/crafted
HTTP upstream fixtures. The `scripted-client` arm JSON-round-trips the same approved
recipe: it tests external-client mechanics, **not model planning or language quality**.
The question text contextualizes a recipe; it is not submitted to a model.

The [machine-readable report](evidence/phase-6/assisted-evaluation.json) retains all
72 planned runs. Both arms scored 36/36 correct: 15 complete-content results, 18
appropriate refusals and three explicitly partial results each. A correct refusal
does not mean that the requested content was obtained. All requests matched fixtures.
These repetitions are correlated checks of fixed inputs, not independent domain
samples; their perfect score supplies no population accuracy estimate or evidence of
generative benefit. Task rubrics derive from reviewed behavioral contracts, but fresh
independent domain review and blinded scoring of this experimental selection remain
outstanding. No expectations were tuned after measuring the task set.

Latency is measured from immediately before recipe validation to receipt of the final
structured MCP result, including fixture HTTP calls, excluding server/session setup.
The report gives median and nearest-rank p95, sample counts, and first-in-session versus
warm-session groups. Each task/arm starts a new context; subsequent repetitions reuse
that session. This is neither machine-cold startup nor production-network latency.
Small differences between arms are measurement noise, not a demonstrated advantage.
Repository validation was running concurrently; these timings are not an isolated benchmark.
Tokens, cost and generated-answer support are **unmeasured**, never invented zeros.

## Domain coverage

| Tasks | Behavioral story | Outcome being checked |
| --- | --- | --- |
| concept | [Concept details](behavioural-tests.md#concept-detail) | Correct concept and release |
| grounding; graph-release-disagreement | [Ground a value](behavioural-tests.md#ground-value) | Source agreement or explicit release refusal |
| data-element; ambiguous-selection | [Data element selection](behavioural-tests.md#data-element) | Published identity/version; ambiguity refused |
| form | [Forms](behavioural-tests.md#forms) | Retrieved form identity/version |
| dictionary | [Dictionary harmonization](behavioural-tests.md#harmonize-dictionary) | Preserve unmatched columns |
| unavailable-registry-operation | [Registry capabilities](behavioural-tests.md#registry-capabilities) | Explicit unavailable capability |
| unknown-value | [Exact permissible values](behavioural-tests.md#permissible-value-concept) | No approximate invention |
| denied-workflow-child | [Governed access](behavioural-tests.md#governed-caller-access) | Actual policy enforcement before upstream access |
| bounded-neighborhood | [Neighborhood](behavioural-tests.md#neighbourhood) | Explicit node truncation |
| hostile-identifier | [Input intent](behavioural-tests.md#preserve-input-intent) | Instruction-like input cannot become execution |

The other stories remain in the full acceptance suite, outside this experiment:
`discover-tools`, `interpretable-answers`, `prompts-and-resources`, `trace-an-answer`,
`choose-release`, `keep-session-release`, `reuse-content`, `distinguish-empty-and-failed`,
`respect-upstream-capacity`, `protect-credentials`, `preserve-attribution`,
`page-and-bound-results`, `registry-state`, `terminology-catalogue`, `batch-concepts`,
`lexical-search`, `indexed-search`, `search-retired`, `hierarchy`, `value-set`,
`subsets-and-mappings`, `retired-replacements`, `relationship-catalogue`,
`data-element-search`, `matching-candidates`, `code-maps`, `registry-contexts`,
`concept-uses`, `stored-values`, `release-alignment`, `expand-cohort`.
This selection represents 10 of 41 stories and does not replace the 964-case suite.

## Reproduce and interpret

```bash
pdm install
pdm run python -m scripts.assisted_evaluation --output tmp/assisted-evaluation.json
```

The runner permits only the registered recipe and supported tools; it never executes
model text, follows links or calls a provider. Hostile upstream text is preserved as
data. Fixture mode explicitly supplies all five local upstream addresses. A fixed
30-second recipe deadline stops further calls; upstream requests have a five-second
timeout and one attempt. Existing tool request bounds remain enforced, and the smaller
per-task request ceiling is a scoring check. Cancellation cannot forcibly terminate an
already executing synchronous request; that request retains its transport timeout.

Scoring checks exact typed fields, record count, explicitly measured matched upstream requests,
and agreement between each MCP error flag and its structured result. Missing or malformed
protocol/provenance measurements cannot score as correct. It also checks finite
nonnegative measurements and request bounds. Content with an error or reported
truncation cannot score as complete. Failed, timed-out, cancelled and unstarted runs
remain in the denominator. Measured failure latencies stay in the distribution; absent
measurements are null. Exceptions propagate after writing the report; a completed run
with incorrect outcomes exits nonzero. Scratch indexes/logs are deleted on exit.
Reports keep checks, protocol error flags and result hashes, not transcripts or credentials. Correlation IDs
can change result hashes between otherwise equivalent runs.

Provenance includes Python/SDK versions, base commit, dirty-tree state and hashes of the
task file, runner, scorer and lockfile. The committed measurement was regenerated with the
milestone review fixes still uncommitted; its source hashes identify the exact measured implementation.
The base commit identifies the fixture/server baseline, not an assertion that it already
contained the new runner. New source changes require a fresh report before citing them.

## Recommendation and remaining decisions

**Retain deterministic workflows and external-client orchestration; defer the server
`ask` pilot.** This fixture baseline finds no demonstrated unmet need that justifies a
generated server capability. It does not establish that no such need exists.

Actual-model evaluation still needs approved provider/model revision, full-transcript
data/retention/region/training policy, egress and spend. Live caDSR validation still
needs credentials. Domain review, natural-language answer support and paired candidate
evidence remain unmeasured. The owner approved deferral on 8 October 2026;
[#180](https://github.com/CBIIT/nci-si-mcp/issues/180) remains open in Backlog. The
[research strategy](deferred-capabilities.md) defines evidence for reconsideration; this report
does not approve or ship a pilot. Any later candidate must use paired tasks and comparable model/data/
release/budgets, and meet separately approved thresholds before shipping.
