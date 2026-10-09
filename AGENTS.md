# AGENTS.md

Guidance for coding agents and contributors working in this repository. It is the one set of
project instructions; the README, QUICKSTART, CONTRIBUTING and ARCHITECTURE documents hold the
detail.

## What this is

The government-furnished prototype of the NCI Semantic Infrastructure MCP server, and the
acceptance suite it and its successors are measured by. Two Statements of Work build on it:
EVS v2.1 and caDSR v1.1. It is a prototype, not a production service. The server never returns
caDSR or other upstream content it did not retrieve. Until NCI issues caDSR credentials,
caDSR behavior is built and tested against fixtures crafted from the published contracts.
The milestones and issues on GitHub (Phases 0 to 7 delivered; later phases open) are the plan. README.md gives the current
status per tool group; QUICKSTART.md holds the usage details.

Documentation, from short to detailed: `README.md` (what the repository is, who it is for, the
status per tool group), `QUICKSTART.md` (install and run; settings, tools, resources, error
codes), `CONTRIBUTING.md` (how to work on it: commands, gates, standards, releases),
`ARCHITECTURE.md` (components, the four main flows, the SQLite schema).

The specification of the required tools and their behaviour is owned by this repository. Its
source of record is the data in `spec/` (conventions, records, tools, requirements);
`docs/specification.md` is generated from it with `pdm run spec-render` and is never edited by
hand. `docs/implementation-plan.md` plans the implementation, and `acceptance/` holds the
acceptance suite that tests the requirements (its README says how). The programme's other
documents (the Statements of Work, which frame and bound the scope and are not a specification,
and the Platform API Specification) live outside this repository; do not rely on them being present.

## Engineering standards

These are the owner's rules. They apply to every change.

- **Never lose sight of the stated goal.** Do what the task asks; do not drift into adjacent work.
- **Tests are written for their value, always.** Each test verifies behaviour a user or caller
  could observe and must be able to fail on a regression.
- **Coverage: the checked minimum is 90%, the aim is above 95%.** The gap exists so that nobody
  pads coverage to pass a threshold. Take every real opportunity to cover untested behaviour;
  never write a test whose purpose is the number (no assertion-free tests, no tests that only
  check that a mock was called). CI fails below 90% and warns at or below 95%.
- **Design: KISS and DRY.** The simplest structure that does the job, one place for each fact,
  and the established architecture (thin adapters over the registry, one error path, closed value
  sets in `validation.py`). No abstraction for a single use.
- **Readable, maintainable, extendable code.** Small functions (cyclomatic complexity below 8 is
  a gate), names that say what a thing is, comments that give the reason.
- **Remove dead code.** Unused functions, parameters, branches and files go in the same change
  that makes them unused.
- **Documentation stays in step with the code**, in the same change. It is intuitive and layered
  (progressive disclosure): the README answers the first questions briefly and links onward.
  Less is more; nobody reads for hours.
- **Agent instructions are tracked; tool-specific local files are not.** This file is the only
  agent instruction file in version control, and nothing tracked may depend on an untracked file.
  A self-test in `tests/test_docs.py` enforces it.

## Commands

The project is managed with PDM (Python 3.14 or newer); `pdm install` builds `.venv` from
`pdm.lock` with the test and lint tools and the `server` and `index` extras.

```bash
pdm run test                              # whole suite, with the 90% coverage floor
pdm run pytest tests/test_index.py        # one file, no coverage floor
pdm run pytest tests/test_handlers.py -k LookupTest
pdm run lint                              # ruff check + basedpyright, the fast check
pdm run fmt                               # ruff format
pdm run pre-commit run --all-files        # every hook, as the CI quality job runs them
NCI_SI_ACCEPTANCE_SECURITY_SERVER='python ../scripts/permissions_fixture.py' NCI_SI_ACCEPTANCE_PREPARE='nci-si-mcp index-sample $(cat "$NCI_SI_ACCEPTANCE_INDEX_CODES")' pdm run acceptance -n 4 --report=fixture.json  # fixture mode, prepared as in CI
pdm run acceptance-expected check acceptance/fixture.json    # the report against the expected outcomes
pdm run acceptance-expected update acceptance/fixture.json   # rewrite the expected outcomes
pdm run acceptance-status                 # regenerate the README status table
pdm run acceptance-selftest               # the acceptance harness's own tests
pdm run spec-render                       # regenerate docs/specification.md from spec/
pdm run quickstart-tools                  # regenerate the tool table of QUICKSTART.md from spec/
```

- Run the tests through `pdm run` (`pdm run test`, `pdm run pytest ...`), never a bare `pytest`,
  so the project's environment and configuration apply.
- The tests are unittest-style and offline. They import shared doubles with `from fakes import
  ...`; `tests/` is on the path through the pytest configuration.
- Hooks are never skipped (`--no-verify`, `SKIP=`). A failing hook is fixed. Besides Ruff and
  basedpyright (`src`, `scripts` and `acceptance/src`), the hooks run `scripts/validation/check_complexity.py`
  (every function below 8, nested ones and the tests included), `check_test_quality.py` (no test
  without a behaviour assertion; an assertion in a nested function or class that the test never
  uses does not count), vulture, gitleaks, zizmor and `scripts/upstream_requirements.py --check`
  (the upstream requirement packages are the current render of their catalogue). All Python tools run from the PDM
  environment, so `pdm.lock` decides their versions.
- `tests/test_docs.py`, `tests/test_server.py` and `tests/test_release_config.py` compare
  QUICKSTART.md, ARCHITECTURE.md and the title check with the code (settings and defaults, error
  codes, modules, tools, public arguments, resources, commit types). Change the document
  with the code.
- Do not enable PDM's uv mode (`use_uv`): it rewrites `pyproject.toml` during an install, which
  marks every installed version as locally modified.

```bash
pdm run nci-si-mcp release-info      # live EVS
pdm run nci-si-mcp index-sample C3262 C2991 C40704 C153397 C116938   # live EVS
pdm run nci-si-mcp search "kinase inhibition" --mode hybrid           # local only
pdm run nci-si-mcp lookup C3262 --live-only
pdm run nci-si-mcp traverse C4817 --max-depth 3 --edge-type descendant
pdm run nci-si-mcp serve
```

Every command opens the index in the data directory, `.nci-si-mcp/` relative to the working
directory. Set `NCI_SI_DATA_DIR` to a scratch directory for experiments.

## Tooling beyond the server

The repository deliberately holds four things: the server (`src/`), the acceptance suite built
for the two Statements of Work (`acceptance/`), the documentation site hosted alongside the MCP,
and the validation companion with its benchmark and evaluation tooling. `scripts/` serves the
last two and the suite's CI; each family below is operational, and what depends on it says why.

| Family | Scripts | Entry point | Depended on by |
|---|---|---|---|
| Gates | `validation/check_complexity.py`, `validation/check_test_quality.py` | pre-commit hooks | every commit, the CI `quality` job |
| Acceptance in CI | `acceptance_http.py`, `permissions_fixture.py`, `upstream_requirements.py` | `pdm run acceptance-http`; the `acceptance` job's environment; a pre-commit hook | the `acceptance` and `acceptance-http` jobs, `docs/upstream/` |
| Server container | `container_lock.py`, `container_smoke.py`, `image_scan.py`, `image_publish.py`, `Dockerfile` | the CI `image` job, the Release workflow | the published image |
| Documentation site | `docs_site.py`, `docs_links.py`, `docs_stories.py`, `site_assets.py`, `static_server.py`, `companion_context.py`, `companion_lock.py`, `container/Docs.Dockerfile` | `pdm run docs-build`; the `documentation` and `companions` jobs | the hosted site |
| Validation companion | `portal*.py`, `operator_*.py`, `evidence_*.py`, `companion_entry.py`, `companion_relay.py`, `companion_smoke.py`, `container/Admin.Dockerfile`, `container/compose.local.yaml` | `pdm run portal`, `pdm run operator-worker`; the `companions` job | `docs/local-validation.md`, `docs/companion-containers.md` |
| Benchmarks and evaluation | `benchmark*.py`, `http_measurement.py`, `assisted_evaluation.py`, `assisted_scoring.py` | `pdm run benchmark-http`; the companion's workers | `docs/benchmark.md`, `docs/assisted-evaluation.md` |
| README badges | `coverage_badges.py` | the CI `coverage-badges` job | the README |

A script that served an ephemeral purpose and is no longer needed to operate or maintain the
server, the suite or the site is removed with its tests, after verifying that nothing depends on
it.

## Acceptance suite and its CI ratchet

`acceptance/` tests the requirements in `spec/` against the server. The CI job `acceptance
(fixture)` runs it against the recorded fixtures and compares every test's outcome with
`acceptance/expected/fixture.json`. The job stays green while tools are not implemented or fail,
and fails when an outcome differs from the expected one. A change that moves an outcome on purpose
updates that file in the same pull request, written with `pdm run acceptance-expected update` from
a fresh report, and `pdm run acceptance-status` updates the README table that follows from it; the
diff of `expected/fixture.json` is what the review reads. The `Acceptance (live)` workflow runs the
suite against the live services by hand and every Monday; its outcomes are not ratcheted, but
the weekly run fails when a test that passes on the fixtures fails live (upstream drift).
The harness has its own
tests (`pdm run acceptance-selftest`, the `selftest` CI jobs). `acceptance/README.md` has the
detail.

## Pull requests and releases

- Every change reaches `main` through a pull request that is squash-merged. The PR title is the
  commit subject and must be a Conventional Commit (`feat: ...`, `fix(scope): ...`); the squash
  body is blank, so only the title counts.
- A release is cut automatically from the title after CI passes on `main`: `feat` bumps the
  minor version, `fix` and `perf` the patch, `!` the minor while below 1.0; other types release
  nothing. Tags are `vX.Y.Z`. Nothing is written back to `main`.
- The accepted types are `allowed_tags` in `[tool.semantic_release.commit_parser_options]` and the
  regex in `.github/workflows/pr-title.yml`; a test keeps them equal. semantic-release reads no
  other list (`other_allowed_tags` alone leaves a type unparseable).
- If a release is missing although `main` is green: `gh workflow run release.yml`.
- The package version is derived from the nearest tag at install or build time; it is written in
  no file. Run `pdm install` after a new tag to refresh it.

## How work is done: issues into a milestone branch, one reviewed milestone into `main`

The milestones and issues on GitHub are the plan; take the open issues of the current phase in
the order the phase's plan gives, one at a time. Each milestone is built on its own branch,
`milestone/<phase>` (for example `milestone/phase-2`), cut from `main`. Issues are merged into it
after a light check; the milestone reaches `main` in one pull request that gets the full review.
The reviewer is the NCI SI MCP project coordinator, or the reviewer acting for them.

**Each issue:**

1. **Read the issue against `spec/` first.** The specification data is the source of record;
   an issue body written earlier may be stale. Where they differ, follow `spec/` and correct the
   issue body in the same step, saying what changed.
2. **Plan before building.** Post the plan as a comment on the issue: what changes, which
   acceptance tests you expect to move in `acceptance/expected/fixture.json` and why, open
   questions with your recommendation, and the commit subject (a Conventional Commit). Wait for the reviewer's answer on the
   issue before writing code; a correction there is binding.
3. **Build on an issue branch cut from the milestone branch,** test first (the failing test,
   then the change), with tests written for their value (see the standards). Before merging run
   `pdm run test`, `pdm run acceptance-selftest`, `pdm run pre-commit run --all-files`, then the
   fixture run and, where outcomes moved on purpose,
   `pdm run acceptance-expected update acceptance/fixture.json` and `pdm run acceptance-status`,
   so the ratchet file moves in the same pull request.
4. **Merge the issue branch into the milestone branch yourself** once the gates pass and the
   outcomes moved as the plan predicted: `git merge --no-ff issue/<branch>` on the milestone
   branch, then push, and delete the issue branch. There is no pull request per issue, no review
   agent and no reviewer clearance at this step; the issue's commit message says what it does,
   names the issue (`#N`) and anything deferred; the milestone pull request body closes the issue
   (step 6). The reviewer reads each merged issue and says on the
   issue if it is not done.

**The milestone, once all its issues are merged:**

5. **Review the milestone branch in five passes to convergence before opening its pull
   request,** each a separate agent or a fresh pass over the whole diff, split by module where
   the diff is large, with one focus each:
   1. **Code review:** the engineering standards above, the architecture, and the scope.
   2. **Silent failures:** swallowed exceptions, broad `except`, fallbacks that hide an error,
      results that look complete but are not.
   3. **Tests:** apply the outside-in rules below to every changed behavioral claim. Identify
      its caller scenario, owning interface and test, plausible regression, and observed
      assertion failure under the original defect or a restored targeted mutation. Distinguish
      setup failures from sensitivity evidence. Check exact error contracts, boundary cases,
      and independent expected values. Challenge internal spies, duplicate claims and
      development scaffolding; retain component tests with independent contracts. Before any
      removal, show which remaining test preserves each distinct failure mechanism. Record
      each finding's retention, replacement or removal rationale in the review table. Coverage
      percentages and passing tests alone do not establish assertion quality or uniqueness.
   4. **Types and records:** invariants are expressed in the types, and records match `spec/`.
   5. **Comments and documentation:** docstrings, comments and documents say what the code
      does now.

   Fix what is real directly on the milestone branch (commit and push it there), without opening
   issues (except for work deferred to a later milestone, recorded in that milestone's issue), and
   run all five again until a full round finds nothing new. Record each round as a short table:
   finding, pass, fixed or rejected (with the reason). The rounds so far go into the pull
   request body when it is opened (step 6); later rounds are posted as pull request comments.
6. **Open the milestone pull request into `main`.** Its title is the release, a Conventional
   Commit that tells the truth about what a client sees (`feat(evs)!:` where it breaks
   something); its body lists `Closes #N` for every issue of the milestone, so they close when
   it merges, the combined expected-file diff grouped by test function with before and after
   counts, the predicted tests that did not move and why, and the review rounds of step 5.
7. **The reviewer then runs an independent mutation review** on the open pull request and posts
   the surviving mutants; close each real gap with a test that fails without the fix, and say
   which you judged equivalent and why. The fixes are pushed to the milestone branch after the
   pull request exists, and their rounds are posted as comments, as in step 5.
8. **Merge only on the reviewer's clearance,** given as a PR comment that names the head commit,
   with `gh pr merge N --squash --subject "<title>" --body "" --delete-branch --match-head-commit
   <sha>`. A push after the clearance needs a new one. Wait for every workflow the pull request
   triggered to finish and pass before asking; never hand over on "CI is running".
9. **After the merge,** confirm CI, Audit, CodeQL and Release on the merge commit, that the
    issues closed, and that the release was cut. After confirming every milestone issue is
    closed, close the milestone explicitly through the GitHub API; GitHub does not close it
    automatically. Then remove your branches, worktrees, scratch files and any process or
    wait loop you started.

The `milestone branches` ruleset forbids force pushes to `milestone/*` and requires no status
checks, because the milestone pull request into `main` runs the full CI on the milestone head and
`main` accepts nothing else. Issue work reaches the milestone branch by a merge of its issue
branch (steps 3 and 4). Review fixes (steps 5 and 7) are committed on the milestone branch
itself and pushed; run the local gates first, as for an issue. When `main` moves, sync it with a
merge commit (`git merge origin/main` on the milestone branch, then push), never a rebase or a
squash, so `main` stays an ancestor and the next sync does not conflict; `main` itself accepts
squash merges only. Findings made along the
way are fixed on the branch they belong to; only an unrelated problem gets an issue. Do not change
the ruleset, repository settings, `spec/`'s conventions or another issue's scope without the
reviewer's agreement.

Rules learned the hard way:

- A change to what the server returns reaches the acceptance harness: run the whole
  `pdm run acceptance-selftest` and the fixture ratchet, not only the unit tests. Keep
  `NCI_SI_ACCEPTANCE_PREPARE` unset for the self-tests; set it only on the fixture command above.
- An interrupted self-test run can leave the `compliant_server.py` stub running; find it with
  `ps` and stop it by its process id.
- A wait loop ends when the file or process it watches is gone, and never matches its own
  command line (`pgrep -f` on a string in the loop does).
- Name roles, never people, in code, issues and pull requests.
- Licence and attribution text is passed through from what the upstream API returns; the server
  keeps no licence data of its own. A licence key or credential is never logged or committed.

## Constraints that shape the code

- The core package has no dependencies (`dependencies = []`). `mcp` and `sentence_transformers`
  are optional extras, imported lazily inside `create_mcp()` and
  `SentenceTransformersProvider.__init__`. `cli.py` imports `server.py` at module level, so nothing
  at the top level of `server.py` may import `mcp`.
- `mcp` is bounded to `>=2.0,<3` because 2.0 renamed `FastMCP` to `MCPServer` and broke the unbounded
  requirement. Check the migration notes before lifting the bound.
- The package must be installed to be imported: `__version__` reads the installed metadata.

## Architecture in brief

`cli.py` and `server.py` read `registry.SPECS`. Each `ToolSpec` declares its handler, output
union, cache policy and adapter exposure; the handler signature supplies the shared input model,
defaults and choices. Business operations live in `handlers.py` and, for the specification's
content tools, `content.py`, with injectable collaborators in `context.Context`. Closed value
sets live once in `validation.py`. A profile selects the tools, resources and prompts the server
serves (M1.5, M1.6); caller policy can only narrow that surface.

Each ToolSpec classifies its parameters as plain or hashed for audit. Undeclared parameters
default to hashed. `audit.py` emits one JSON completion record per call, including validation
failures, with correlation, result size, truncation and request counts from the HTTP client's
per-attempt instrumentation. MCP and registry scopes share the record; concurrent calls do not.
Free text is SHA-256 hashed to correlate repeated inputs, not to keep guessable text secret.
All application diagnostics are JSON on stderr; exception messages and upstream bodies are
not logged. Diagnostic verbosity does not suppress the required completion record.

### One error path

The error codes are those of the specification's error record (`spec/records.yaml`), closed in
`errors.py` as `ErrorCode`. A failure is a `PlatformError`: its code, a message that names the
caller's next step, and the `details` that `spec/records.yaml` lists for that code (`error.detail_keys`). `errors.serialise` is
the only function that turns one into the result, `{"error": {"code", "message", "details"?,
"correlationId"}}`; nothing builds that dict by hand. The audit boundary opens `errors.correlated()` once
per call (the request's `_meta.correlationId`, else generated) for tools, resources and CLI.
`registry.invoke` shares that scope and enters `invocation.call` for expected failures.
The raisers own their wording (`cadsr_matching` the match-timeout step, `cadsr_content` the
OP-C03 hint on `upstream.MaskedSuccessError` only); `invocation` never branches on a tool name.
A result that embeds an error uses `invocation.error_record`, which logs no `call_failed`.
`invocation.call` converts the expected exception types listed in `invocation._ERROR_CODES` (with the next
step appended to their message and their `details` attribute carried over) and logs a warning; an
exception gets the entry of its nearest listed class. To add a failure mode,
raise a specific exception type and add it to that table, or raise a `PlatformError` where the
message needs data (the releases served). Anything not in the table is a bug and propagates: do not
add broad `except` clauses. An empty result is never an error and an error is never empty.

`upstream.parse_upstream_json` is the one place that classifies a failure masked as a success
(webMethods `apiResponse.type` `E`, FHIR `OperationOutcome` error, HTML where JSON was asked for)
as `upstream_unavailable`; every upstream client parses its bodies through it (in `http_client.HttpClient`).

The reviewer-approved Form-by-ID exception lives in `cadsr._form_absence`: after public-id
validation, only HTTP 200 with an explicit `form: null` and `apiResponse.type: E` is `not_found`.
The recording `recorded/cadsr/form-unknown.json` has no other discriminator, so a genuine failure
in exactly that shape is indistinguishable; the upstream package (`docs/upstream/cadsr.md`,
cadsr-forms) asks caDSR for an explicit absence signal. No message
matching or second request. Every other shape, status and operation keeps common X-15 handling.

`http_client.HttpClient` is the one HTTP client. Its `Upstream*` errors reach the invocation
boundary directly; EVS errors identify only EVS-specific failures. Attempts are counted and reported to
the `on_request` hook. A credential is a header of one client and goes to that client's origin only;
it is redacted from every message built from what the platform said.

`LocalIndex._connect` turns SQLite failures of the database itself (locked, unreadable, not a
database) into `IndexStorageError` naming the file; constraint and usage errors propagate as bugs.
A 404 from EVS means "no such concept" only for `EVSClient.get_concept`. Every other method goes
through `_get_existing`, which converts a 404 to `EVSResponseError` (a wrong base URL).

`server.py` turns an error record into a protocol-level error (`CallToolResult(is_error=True)` for
tools, `ResourceError` for resources). The handler docstrings are the contract sent to MCP clients;
update them when behaviour changes.

### Release pinning

- `release.resolve_evs_release(evs, terminology, channel)` asks EVS for the rows that are `latest`
  and tagged with the channel (`?terminology=…&latest=true&tag=…`) and requires exactly one; any
  other count is `release_not_available`, with no fallback to another channel. EVS sets `latest`
  per channel, so the unfiltered listing can show two `ncit` rows as latest. `resolve_release`
  resolves the channel once per call. NCIt content calls may omit `release`: the shared
  invocation scope resolves it once or reuses the MCP session’s first implicit pin. Explicit
  calls never change that pin; other terminologies require release. No process-wide pin is
  retained. Stateless HTTP and CLI resolve per call. A withdrawn session pin fails closed
  without rediscovery, asking for a new session or explicit release. Completion audit names
  explicit, session-held or freshly-resolved selection.
  A 404 `Terminology not found` is `EVSReleaseNotFoundError`
  (`release_not_available`).
- `release.registry_state` is the pure part of the caDSR registry state: no registry identifier is
  ever made up. The caDSR client reads the exact distribution row in the export folder;
  its local date-time has minute precision and no timezone, which is preserved without an offset.
- Every concept request uses `release.pinned_terminology` (for example `ncit_26.09d`) as the path
  segment, and `evs.verify_content` checks the terminology and version of each full concept at the client
  boundary. Content methods require a `ReleaseContext`; compact descendant entries name the
  release addressed by their request without inventing upstream version fields.
- `lookup` returns `release_mismatch` when the index holds another release, unless `live_only`. It
  falls back to the cache only on `UpstreamUnavailableError`, and marks the result with `fallback`.
- Each index build holds one release. Activation retains the previous build for rollback.

### Index and search

`LocalIndex._connect()` is a context manager that commits or rolls back and then closes; use it for
every database access. `upsert_concepts` reads a consistent snapshot and checks
compatibility before snapshot creation. Embeddings run outside transactions; each
batch is written in a short transaction under a building manifest. Only completed builds activate.
Sample activation checks that another writer has not changed the active build in the meantime.

Schema 6 stores immutable builds keyed by an internal build id. Name, synonym and definition
texts are deduplicated within each concept and embedded separately. Activation retains only
the new build and its predecessor. The next build start removes stale building rows;
a private SQLite lease distinguishes interrupted builds from concurrently running ones.
Legacy raw concepts survive migration, but their search requires an explicit offline
`index-rebuild` and `index-activate`. Full builds reconcile all pinned search pages before writing.

Exact indexed search lazily imports NumPy from the `index` extra. Each concept stores its field
vectors together in a little-endian float32 BLOB, with field-kind bytes; FTS retains individual
fields and their positions. One scan scores bounded matrix chunks, then compact numeric arrays
provide normalization, per-concept maxima and exact page selection. No vector matrix is cached.
Migration from schema 5 preserves builds, activation, FTS and retirement status. Earlier schemas
retain raw concepts but require an explicit rebuild. Search cursors bind the active build id;
even a same-release replacement expires them. Count, page and provenance share one read snapshot.

### Traversal

`traverse_ncit` walks all start codes one depth at a time, so nearer nodes claim the node and edge
limits first whatever the order of start codes and edge types. Each depth is read with batched
`get_concepts_by_codes` requests whose `include` names only the selected relation lists. Walks
that follow inverse roles or inverse associations use batches of 10, because those lists run to
megabytes for hub concepts; a batch that exceeds the response limit is halved, and a single
concept that still exceeds it is kept unexpanded and counted against the `upstream_cap` bound of
the walk's `Truncation` record. The state of a walk (limits, emitted nodes and edges) lives in the
`_Walk` object in `traversal.py`, which also builds the `TraversalProvenance` of each node and edge.
At the depth limit, forward kinds get a batched continuation check; descendant checks use
child lists. A reported global node cut skips the check, and kinds already truncated are
excluded. Selected inverse kinds at any nonempty frontier report depth with `omitted: 0`, `exact: false`
without fetching their expensive lists just to check continuation. All reads share the request
budget, and the first bound remains the one reported.

`descendant` edges are opt-in (`edge_types`) and come from one `get_descendants` call per start
code with `maxLevel = max_depth`. They are bucketed by the `level` EVS assigns and emitted together
with the other edges reaching that depth. That level can be deeper than the shortest path, so a
`child` walk of the same depth can reach more concepts (59 against 56 for C3262 at depth 2). The
traversal handler calls `select_edge_types` before resolving the release, so invalid selections never reach
the network.

## Tests

### Outside in test design and review

Apply the [outside-in testing skill](https://gist.githubusercontent.com/imaurer/ac31f596bcfd7f46afe1c7dceedcba21/raw/faf1bfda7316a647cf0bd5a03d433dae5a7d4fa2/outside-in-tests.md)
at this pinned revision, as summarized here. This tracked guidance is self-contained.

- Describe the caller's scenario as Given/When/Then before implementation; demonstrate
  the intended failure, implement the behavior, then review the tests for retention.
- Prefer MCP, CLI and HTTP boundaries. Test a component directly when its interface owns
  an independent contract, such as parsing, storage or ranking.
- Retain observable behavior checks, boundary/input tables, externally relied-on contracts,
  and demonstrated regressions. Remove development scaffolding that protects no distinct claim.
- Identify each claim's owning test. Additional layers need a different failure mechanism
  or contract, not repetition of the same assertion.
- Before adding a test, identify its behavior, a plausible regression, the existing coverage
  gap, and a real interface through which to exercise it. Avoid test-only production switches.
- Review internal spies, setup-derived assertions and historical test groupings for coupling;
  names should explain behavior. Neither mocking nor a unit-test label alone proves redundancy.
- Use coverage to locate gaps and targeted mutations to evaluate sensitivity and uniqueness.
  Replace a sole behavioral check before removing it; consolidate in small verified batches.
  Correct product defects rather than weakening expectations to obtain green tests.

`tests/fakes.py` holds `FakeEVS`, an in-memory stand-in for `EVSClient` that records calls,
honours `include` and returns batches in the request's order rotated by one (EVS keeps no batch
order), plus `concept()` and `release()` builders. Nothing touches the network.
`test_server.py` drives the real server through an in-process `mcp.client.Client` session.

## EVS facts worth knowing

Verified live on 2026-10-01 against release 26.09d.

- An unknown code returns HTTP 404 with a JSON body whose `message` says so; the batch endpoint
  (`?list=`) silently omits unknown codes instead, and keeps no order: mostly lexicographic, but
  the same request came back in two orders a minute apart (2026-10-02). Never pair by position.
- On 2026-10-02 one machine's IPv6 route to EVS failed while IPv4 worked, and Python's urllib
  waited about 120 s per request. If live calls are that slow, check `curl -4` against `curl -6`
  and force IPv4 in a scratch wrapper; it is not a code problem.
- `/concept/{terminology}/{code}/descendants` returns every level unless `maxLevel` is given
  (15,808 concepts for C3262); each item carries its `level`.
- Responses carry no `Content-Length`, so an oversized response is only detected after reading up
  to the limit.
- The terminology listing holds every served release (33 rows), including older monthly NCIt
  releases with `latest: false`; the current monthly row can carry both `monthly` and `weekly` tags.
- `/descendants` items are ordered by name, not by level.
- A batch of 100 concepts with all six relation lists took 1.3 s and 625 KB for ordinary concepts.
