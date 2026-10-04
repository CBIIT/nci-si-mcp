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
pdm run acceptance -n 4 --report=fixture.json                # the acceptance suite, fixture mode
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

Service methods are wrapped by `_enveloped`, which maps the expected exception types listed in
`_ERROR_CODES` to an error envelope and logs a warning; an exception gets the code of its nearest
listed class. To add a failure mode, raise a specific exception type and add it to that table.
Anything not in the table is a bug and propagates: do not add broad `except` clauses.

`LocalIndex._connect` turns SQLite failures of the database itself (locked, unreadable, not a
database) into `IndexStorageError` naming the file; constraint and usage errors propagate as bugs.
A 404 from EVS means "no such concept" only for `EVSClient.get_concept`. Every other method goes
through `_get_existing`, which converts a 404 to `EVSResponseError` (a wrong base URL).

`server.py` turns an envelope into a protocol-level error (`CallToolResult(is_error=True)` for
tools, `ResourceError` for resources). The tool docstrings are the contract sent to MCP clients;
update them when behaviour changes.

### Release pinning

- `select_monthly_ncit_release` requires exactly one latest monthly NCIt row and never falls back to
  weekly. EVS sets `latest` per channel, so two `ncit` rows can carry it at once.
- Every concept request uses `release.pinned_terminology` (for example `ncit_26.09d`) as the path
  segment, and `evs.verify_release` checks the `version` of each returned concept.
- `lookup` returns `version_mismatch` when the index holds another release, unless `live_only`. It
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
concept that still exceeds it is kept unexpanded (`unexpanded_codes`) with `truncated` set. The
state of a walk (limits, emitted nodes and edges) lives in the `_Walk` object in `traversal.py`.

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
