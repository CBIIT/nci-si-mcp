# AGENTS.md

Guidance for coding agents and contributors working in this repository. It is the one set of
project instructions; the README, QUICKSTART, CONTRIBUTING and ARCHITECTURE documents hold the
detail.

## What this is

An EVS-first MCP server prototype: NCIt search, lookup and graph traversal over the NCI EVS REST
API, served over stdio with `mcp` 2.x. caDSR is a stub by design: `cadsr.py` reports `reuse_pending`
and must never return fabricated CDE data. The goal it grows toward is the shared NCI Semantic
Infrastructure MCP platform; the milestones and issues on GitHub (Phase 0 to 5) are the plan.

Documentation, from short to detailed: `README.md` (what the repository is, who it is for, the
status per tool group), `QUICKSTART.md` (install and run; settings, tools, resources, error
codes), `CONTRIBUTING.md` (how to work on it: commands, gates, standards, releases),
`ARCHITECTURE.md` (components, the four main flows, the SQLite schema).

The specification of the required tools and their behaviour is owned by this repository. Its
source of record is the data in `spec/` (conventions, records, tools, requirements);
`docs/specification.md` is generated from it with `pdm run spec-render` and is never edited by
hand. `docs/SPEC.md` is the implementation plan, and `acceptance/` holds the acceptance suite that
tests the requirements (its README says how). The programme's other documents (the Statements of
Work, which frame and bound the scope and are not a specification, and the Platform API
Specification) live outside this repository; do not rely on them being present.

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
  and the established architecture (thin adapters over the service, one error path, closed value
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
`pdm.lock` with the test and lint tools and the `server` extra.

```bash
pdm run test                              # whole suite, with the 90% coverage floor
pdm run pytest tests/test_index.py        # one file, no coverage floor
pdm run pytest tests/test_service.py -k LookupTest
pdm run lint                              # ruff check + basedpyright, the fast check
pdm run fmt                               # ruff format
pdm run pre-commit run --all-files        # every hook, as the CI quality job runs them
NCI_SI_ACCEPTANCE_PREPARE='nci-si-mcp index-sample $(cat "$NCI_SI_ACCEPTANCE_INDEX_CODES")' pdm run acceptance -n 4 --report=fixture.json  # fixture mode, prepared as in CI
pdm run acceptance-expected check acceptance/fixture.json    # the report against the expected outcomes
pdm run acceptance-expected update acceptance/fixture.json   # rewrite the expected outcomes
pdm run acceptance-status                 # regenerate the README status table
pdm run acceptance-selftest               # the acceptance harness's own tests
pdm run spec-render                       # regenerate docs/specification.md from spec/
```

- Run the tests through `pdm run` (`pdm run test`, `pdm run pytest ...`), never a bare `pytest`,
  so the project's environment and configuration apply.
- The tests are unittest-style and offline. They import shared doubles with `from fakes import
  ...`; `tests/` is on the path through the pytest configuration.
- Hooks are never skipped (`--no-verify`, `SKIP=`). A failing hook is fixed. Besides Ruff and
  basedpyright (`src` and `scripts` only), the hooks run `scripts/validation/check_complexity.py`
  (every function below 8, nested ones and the tests included), `check_test_quality.py` (no test
  without a behaviour assertion; an assertion in a nested function or class that the test never
  uses does not count), vulture, gitleaks and zizmor. All Python tools run from the PDM
  environment, so `pdm.lock` decides their versions.
- `tests/test_docs.py`, `tests/test_server.py` and `tests/test_release_config.py` compare
  QUICKSTART.md, ARCHITECTURE.md and the title check with the code (settings and defaults, error
  codes, modules, tools, `ncit_traverse` arguments, resources, commit types). Change the document
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

## Acceptance suite and its CI ratchet

`acceptance/` tests the requirements in `spec/` against the server. The CI job `acceptance
(fixture)` runs it against the recorded fixtures and compares every test's outcome with
`acceptance/expected/fixture.json`. The job stays green while tools are not implemented or fail,
and fails when an outcome differs from the expected one. A change that moves an outcome on purpose
updates that file in the same pull request, written with `pdm run acceptance-expected update` from
a fresh report, and `pdm run acceptance-status` updates the README table that follows from it; the
diff of `expected/fixture.json` is what the review reads. The `Acceptance (live)` workflow runs the
suite against the live services by hand; its outcomes are not ratcheted. The harness has its own
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

## How work is done: one issue, one reviewed pull request

The milestones and issues on GitHub are the plan; take the open issues of the current phase in
the order the phase's plan gives, one at a time. Every change is reviewed by the reviewer (the
NCI SI MCP project coordinator, or the reviewer acting for them) before it reaches `main`.

1. **Read the issue against `spec/` first.** The specification data is the source of record;
   an issue body written earlier may be stale. Where they differ, follow `spec/` and correct the
   issue body in the same step, saying what changed.
2. **Plan before building.** Post the plan as a comment on the issue: what changes, which
   acceptance tests you expect to move in `acceptance/expected/fixture.json` and why, open
   questions with your recommendation, and the PR title. Wait for the reviewer's answer on the
   issue before writing code; a correction there is binding.
3. **Build on a branch,** never on `main`, with tests written for their value (see the
   standards). Before opening the pull request run `pdm run test`, `pdm run acceptance-selftest`,
   `pdm run pre-commit run --all-files`, then the fixture run and, where outcomes moved on
   purpose, `pdm run acceptance-expected update acceptance/fixture.json` and
   `pdm run acceptance-status`, so the ratchet file moves in the same change.
4. **Open the pull request** with a Conventional Commit title that tells the truth about what a
   client sees (`feat(scope)!:` where it breaks something). Its body gives what it does, the
   expected-file diff grouped by test function with before and after counts, the predicted tests
   that did not move and why, anything deferred and where it is recorded, and judgement calls
   for the reviewer.
5. **Wait for CI to finish** and report the real result; never hand over on "CI is running".
6. **Merge only on the reviewer's clearance,** given as a PR comment that names the head commit,
   with `gh pr merge N --squash --subject "<title>" --body "" --delete-branch --match-head-commit
   <sha>`. A push after the clearance needs a new one.
7. **After the merge,** confirm CI, Audit, CodeQL and Release on the merge commit, that the
   issue closed, and that the release (if any) was cut; then remove your branches, worktrees,
   scratch files and any process or wait loop you started.

Findings made along the way are fixed on the same branch when they belong to the work; only an
unrelated problem gets an issue. Do not change the ruleset, repository settings, `spec/`'s
conventions or another issue's scope without the reviewer's agreement.

Before you mark a pull request ready, review it yourself in five passes, each a separate agent
or a separate fresh pass over the whole diff, with one focus each:

1. **Code review:** the engineering standards above, the architecture, and the issue's scope.
2. **Silent failures:** swallowed exceptions, broad `except`, fallbacks that hide an error,
   results that look complete but are not.
3. **Tests:** every behaviour the change adds is asserted and could fail on a regression;
   edge cases and error paths are covered.
4. **Types and records:** invariants are expressed in the types, and records match `spec/`.
5. **Comments and documentation:** docstrings, comments and documents say what the code does
   now.

Fix what is real on the branch and run all five again, until a full round finds nothing new
that is real. Post each round in a PR comment as a short table: finding, pass, fixed or rejected
(with the reason). Then mark the PR ready.

The reviewer runs an independent mutation review of each pull request and posts the surviving
mutants; close each real gap with a test that fails without the fix, and say which you judged
equivalent and why.

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

`cli.py` and `server.py` are thin adapters over `service.NCISIService`. A new capability goes into
the service and is exposed in both adapters; the closed value sets (search modes, directions, edge
types) live once in `validation.py` and feed the MCP schema and the argparse choices.

### One error path

The error codes are the ten of the specification's error record (`spec/records.yaml`), closed in
`errors.py` as `ErrorCode`. A failure is a `PlatformError`: its code, a message that names the
caller's next step, and the `details` that code lists in `docs/SPEC.md` §3.1. `errors.serialise` is
the only function that turns one into the result, `{"error": {"code", "message", "details"?,
"correlationId"}}`; nothing builds that dict by hand. The adapters open `errors.correlated()` once
per call (the request's `_meta.correlationId`, else generated). Service methods are wrapped by
`_enveloped`, which converts the expected exception types listed in `_ERROR_CODES` (with the next
step appended to their message and their `details` attribute carried over) and logs a warning; an
exception gets the entry of its nearest listed class. To add a failure mode,
raise a specific exception type and add it to that table, or raise a `PlatformError` where the
message needs data (the releases served). Anything not in the table is a bug and propagates: do not
add broad `except` clauses. An empty result is never an error and an error is never empty.

`upstream.parse_upstream_json` is the one place that classifies a failure masked as a success
(webMethods `apiResponse.type` `E`, FHIR `OperationOutcome` error, HTML where JSON was asked for)
as `upstream_unavailable`; every upstream client parses its bodies through it (in `http_client.HttpClient`).

`http_client.HttpClient` is the one HTTP client. It raises the `Upstream*` errors, which
`EVSClient` turns into the `EVS*` ones in `_evs_error`; its attempts are all counted and reported to
the `on_request` hook. A credential is a header of one client and goes to that client's origin only;
it is redacted from every message built from what the platform said.

`LocalIndex._connect` turns SQLite failures of the database itself (locked, unreadable, not a
database) into `IndexStorageError` naming the file; constraint and usage errors propagate as bugs.
A 404 from EVS means "no such concept" only for `EVSClient.get_concept`. Every other method goes
through `_get_existing`, which converts a 404 to `EVSResponseError` (a wrong base URL).

`server.py` turns an error record into a protocol-level error (`CallToolResult(is_error=True)` for
tools, `ResourceError` for resources). The tool docstrings are the contract sent to MCP clients;
update them when behaviour changes.

### Release pinning

- `release.resolve_evs_release(evs, terminology, channel)` asks EVS for the rows that are `latest`
  and tagged with the channel (`?terminology=…&latest=true&tag=…`) and requires exactly one; any
  other count is `release_not_available`, with no fallback to another channel. EVS sets `latest`
  per channel, so the unfiltered listing can show two `ncit` rows as latest. The service resolves
  once per call with `Settings.release_channel` and threads the `ReleaseContext` through that call;
  nothing keeps it between calls. A 404 `Terminology not found` is `EVSReleaseNotFoundError`
  (`release_not_available`).
- `release.registry_state` is the pure part of the caDSR registry state: no registry identifier is
  ever made up. The `Last-Modified` HEAD request belongs to the caDSR client.
- Every concept request uses `release.pinned_terminology` (for example `ncit_26.09d`) as the path
  segment, and `evs.verify_release` checks the `version` of each returned concept.
- `lookup` returns `release_mismatch` when the index holds another release, unless `live_only`. It
  falls back to the cache only on `EVSUnavailableError`, and marks the result with `fallback`.
- The index holds one release. Indexing a concept of another release replaces everything.

### Index and search

`LocalIndex._connect()` is a context manager that commits or rolls back and then closes; use it for
every database access. `upsert_concepts` takes the write lock first (`BEGIN IMMEDIATE`), checks
compatibility, then writes all tables in that one transaction.

Vector search scans every stored vector up to `EXACT_VECTOR_SCAN_LIMIT` (20,000 concepts). Above
that it scores only LSH and BM25 candidates, and vector-only recall is poor (measured 17 of 100
nearest neighbours found on a 3,000-concept synthetic index). The LSH constants and
`_projection_sign` are part of the stored format: changing them needs a `SCHEMA_VERSION` bump with
a migration that rebuilds `vector_lsh`.

### Traversal

`traverse_ncit` walks all start codes one depth at a time, so nearer nodes claim the node and edge
limits first whatever the order of start codes and edge types. Each depth is read with batched
`get_concepts_by_codes` requests whose `include` names only the selected relation lists. Walks
that follow inverse roles or inverse associations use batches of 10, because those lists run to
megabytes for hub concepts; a batch that exceeds the response limit is halved, and a single
concept that still exceeds it is kept unexpanded and counted against the `upstream_cap` bound of
the walk's `Truncation` record. The state of a walk (limits, emitted nodes and edges) lives in the
`_Walk` object in `traversal.py`, which also builds the `TraversalProvenance` of each node and edge.

`descendant` edges are opt-in (`edge_types`) and come from one `get_descendants` call per start
code with `maxLevel = max_depth`. They are bucketed by the `level` EVS assigns and emitted together
with the other edges reaching that depth. That level can be deeper than the shortest path, so a
`child` walk of the same depth can reach more concepts (59 against 56 for C3262 at depth 2). The
service calls `select_edge_types` before resolving the release, so invalid selections never reach
the network.

## Tests

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
