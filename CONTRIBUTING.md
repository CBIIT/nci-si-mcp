# Contributing

How to work on this repository. What it is, and its status, is in [README.md](README.md); how to
install and run the server is in [QUICKSTART.md](QUICKSTART.md); how it is built is in
[ARCHITECTURE.md](ARCHITECTURE.md). Update the documents and diagrams with each behaviour change:
the [documentation website](docs/documentation-site.md) is built from these sources (pinned build,
local preview, reviewed page allowlist, source and version labels).
Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).

## Set up

The [platforms and prerequisites](QUICKSTART.md#platforms-and-prerequisites) come first: Python 3.14 or newer and PDM.

```bash
pdm install                    # .venv from pdm.lock, with the test and lint tools and the server and index extras
pdm run pre-commit install     # install the commit gates and the full-suite push gate
```

Run the install command again in a clone whose hooks were installed before the push gate was
added. A commit runs the file and project gates. A push runs `pdm run test` and therefore takes
about as long as that command.

## Everyday commands

| Command | What it does |
| --- | --- |
| `pdm run test` | The whole suite, with the coverage minimum |
| `pdm run pytest tests/test_index.py -k name` | A selection of tests, without the minimum |
| `pdm run lint` | Ruff and basedpyright: the fast check while you work |
| `pdm run mutation-gate` | Kill every changed-site Python mutant before a PR; `--paths src/package/module.py` checks a whole module; review survivors with the printed AST keys in `scripts/validation/mutation-allowlist.yaml` |

CI runs the complete mutation catalogue as three stable-key hash partitions, each with two
workers and a 300-minute timeout: `--shard 1/3 -n 2`, `--shard 2/3 -n 2` and
`--shard 3/3 -n 2`. All three must pass; a job timeout fails. Shard JSON lines report
full/selected counts and a final outcome/kill-kind summary with elapsed seconds. An empty
partition reports `EMPTY`; allowlist freshness is checked before partitioning. The measured
144-mutant Phase 33k diff took 73m22s unsharded; projected shard times were 31–37 minutes
(including baseline overhead, excluding contention), targeting under 60 minutes for a typical
milestone. The gate's nested self-tests require the larger timeout.

Before launching mutants, every nonempty shard requires clean unit, harness self-test and
prepared fixture baselines. Any baseline failure names its domain and fails the gate, even
when the acceptance ratchet expects that failure. Only unit and self-test baselines collect
coverage contexts for test selection; the fixture baseline checks readiness without coverage.
The shard summary's `baseline_domains` records each domain's outcome and measured seconds.
| `pdm run fmt` | Format with Ruff |
| `pdm run lock` | Refresh every Python group to the newest version admitted by the five-day release-age policy |
| `pdm run github-setup` | Create missing workflow credential names with safe placeholders; never overwrite existing names |
| `pdm run pre-commit run --all-files` | Every commit-stage hook, as the CI `quality` job runs them |
| `pdm run acceptance` | The acceptance suite against the server ([acceptance/README.md](acceptance/README.md)) |
| `pdm run acceptance-http` | Real HTTP fixture suite, managed restarts and explicit remote-contract skips ([transport details](docs/transport.md)) |
| `pdm run python -m scripts.benchmark --output tmp/benchmark.json` | Representative MCP measurements with actual outbound counts ([method and evidence](docs/benchmark.md)) |
| `pdm run python scripts/upstream_requirements.py` | Regenerate the [upstream requirements packages](docs/upstream/README.md) from the catalogue and measured reports; `--check` verifies them without writing |
| `pdm run acceptance -n 4 --dist worksteal --report=fixture.json` | The acceptance suite on four workers, with the report CI compares ([the ratchet](acceptance/README.md#ci-the-ratchet-on-expected-outcomes)) |
| `pdm run acceptance-expected check acceptance/fixture.json` | The report against `acceptance/expected/fixture.json`, test by test |
| `pdm run acceptance-status` | Regenerate the README status from fixture expected outcomes and stored scheduled-live evidence |
| `pdm run acceptance-selftest` | The acceptance harness's own tests, on four workers |
| `pdm run acceptance-index-codes` | Print the sample concept codes the suite indexes before a run |
| `pdm run acceptance-record` | Re-record `acceptance/fixtures/recorded/` from the live services, under change control |
| `pdm run acceptance-craft` | Record the digest of the crafted fixtures in the manifest; `--out DIR` writes them to read |
| `pdm run acceptance-register` | Regenerate the request-forms register (`acceptance/request-forms/`) |
| `pdm run acceptance-stories` | Regenerate [docs/behavioural-tests.md](docs/behavioural-tests.md) from the suite; `--check` verifies it |
| `pdm run spec-render` | Regenerate [docs/specification.md](docs/specification.md) from `spec/`, after any change there |
| `pdm run quickstart-tools` | Regenerate the table of tools in [QUICKSTART.md](QUICKSTART.md) from `spec/tools.yaml`; `--check` verifies it |
| `pandoc -f gfm docs/specification.md -o specification.docx` | The Word copy of the specification, a build product: never committed or edited |

Beyond the server and the suite, the repository holds the documentation site, the validation
companion and the benchmark tooling; these are their entry points.

| Command | What it does |
| --- | --- |
| `pdm run docs-build` | Build the documentation website into `tmp/docs-site` ([documentation site](docs/documentation-site.md)) |
| `pdm run portal` | Serve the local validation dashboard on loopback ([local validation](docs/local-validation.md)) |
| `pdm run operator-worker` | Run one validation job (acceptance or benchmark) the way the dashboard does, from the command line |
| `pdm run benchmark-http` | HTTP transport measurements of the served tools ([method and evidence](docs/benchmark.md)) |

The server tests are `unittest.TestCase` classes, run by pytest. They are offline:
`tests/fakes.py` supplies shared doubles, and client integration tests use local HTTP fixtures.
The acceptance suite and its self-tests use pytest directly.
The unit tests ignore the developer's `NCI_SI_*` variables (`tests/conftest.py` removes them); a
test that needs a setting sets it with `monkeypatch` or `patch.dict`.

## Test levels

The [behavioural story catalogue](docs/behavioural-tests.md) documents every MCP acceptance
case for domain reviewers. Keep its authored narratives and function assignments in
`acceptance/stories.yaml` aligned with test changes, then run `pdm run acceptance-stories`.
`pdm run acceptance-stories --check` verifies completeness and freshness; the harness
self-tests enforce the same check in CI.

Give each behavior an owning test at the interface its caller uses. Keep component tests
for independent contracts and broader tests for distinct integration failures; do not repeat
the same claim at every layer. Describe Given/When/Then before implementation, observe the
intended failure, and remove redundant development scaffolding with evidence before landing.
The outside-in review rules are in [AGENTS.md](AGENTS.md#outside-in-test-design-and-review).
This preserves the
[test pyramid](https://testing.googleblog.com/2015/04/just-say-no-to-more-end-to-end-tests.html);
parameterized contract cases do not need an arbitrary ratio of test counts.

Mutation JSON rows record monotonic `elapsed_seconds` for the whole mutant (including scratch
setup) and a `domains` list of measured test-run durations and outcomes. The scheduler stores
each source once, span/replacement edits and preselected test routes; it does not duplicate
the baseline coverage map per worker. Multiple unittest subtest reports become one owning
call record; any non-assertion subtest failure keeps that call an infrastructure failure.
The probe consumes its launch-only evidence destination at configuration; only declared
xdist workers receive it, so nested pytest programs cannot append to the parent's records.

| Level | What exercises it here |
| --- | --- |
| Unit | Parsers, validation, ranking, truncation and report verdicts in `tests/` and `acceptance/selftests/` |
| Integration | Real SQLite migrations/builds/rollback, HTTP retries against local servers, and owned subprocess cleanup |
| API | The real MCP client in `tests/test_server.py`; HTTP authentication, sessions and health in `tests/test_transport.py` |
| End to end | Prepared fixture acceptance through the real stdio server; `pdm run acceptance-http` through a listening HTTP server; the CI container smoke through the built image, external model/index, readiness and graceful shutdown |

The companion follows the same pyramid: unit tests verify evidence projection, historical
story mapping, proposals and rendering; real SQLite/filesystem/subprocess tests verify import,
atomic history and bounded worker lifecycle; loopback HTTP tests verify controls and error
responses. The `companion containers (amd64)` CI job builds both images and exercises a real
fixture benchmark, result display, network/mount isolation and shutdown independence.
Automated Chromium journeys cover responsive search, requirement links, result filtering and
native run/cancel forms. The worker is controlled in the browser tests; separate integration
tests exercise real worker termination. See the
[assurance record](docs/government-site-assurance.md#automated-browser-regression-journeys)
for setup commands, manual checks and limitations.

Coverage includes lines, branches and Python subprocesses for the server and harness suites.
It identifies execution gaps, not missing assertions: use a targeted mutation when an
assertion's sensitivity is uncertain. Image-only checks run in the container CI job. The
fixtures verify contracts, including crafted caDSR responses; they do not establish live
upstream support. Some caDSR endpoints permit anonymous access; protected endpoints require
issued credentials. The separate live workflow checks only cases marked `live_capable` and
does not treat skipped cases as drift. It covers protocol/discovery, selected local validation
and unavailable-capability contracts, and bounded EVS, anonymous CDE and cross-domain content
checks; [the suite README](acceptance/README.md#writing-a-test) maintains the exact live scope.
Validation refusals are not evidence of working upstream retrieval; credentials alone do not
enable the fixture-only cases. This remains limited live coverage, not full content conformance.

On pull requests, `scripts/ci_images.py` skips an image job only when every changed path
is a known noninput for that image. Unknown paths, an empty or unavailable diff, and all
pushes to `main` select both image jobs. The event's full base/head change set includes
deleted paths and both sides of renames; changes confined to `tests/` can avoid both image
builds. Acceptance self-tests are packaged in Admin, and public documentation and checked
source links feed the companion site. The root README also feeds server wheel metadata.
Other CI gates are unchanged.

## Coverage badges

CI exports separate JSON coverage reports for the server and the complete, combined harness
self-tests. A read-only job renders the three badge JSON files; a separate writer downloads
that run's data without checking out or executing repository code. After every CI gate
passes on a push to `main`, the writer publishes measured
line-plus-branch percentages and their source counts, commit and run URL to the dedicated
`coverage-badges` branch. It never commits to `main`, publishes from a PR, or replaces data
from a newer main commit with an older run. Failed runs leave the previous measurements
visible; use the adjacent CI badge to see main's current status.

The README uses [Shields endpoint badges](https://shields.io/badges/endpoint-badge), so no
coverage-service account, extra credential or manual percentage update is needed. GitHub and
Shields cache images, so a successful publication may take a few minutes to appear. Before
the first successful publication, the endpoints do not exist. Do not delete `coverage-badges`
during feature-branch cleanup; it is generated publication data.

## Standards

1. **Stay on the goal.** A change does what its task asks.
2. **Tests are written for their value.** Each one checks behaviour that a user or a caller can
   observe, and can fail when that behaviour breaks.
3. **Coverage: 95% is the checked minimum, above 97% is the aim.** The gap is deliberate: it
   keeps anyone from writing tests for the number. Cover untested behaviour whenever you can.
   Never add a test that asserts nothing, or only that a mock was called.
   The figure covers `src/` and every script in `scripts/` except those `omit`ted in
   `pyproject.toml`, each with its reason there; omit nothing to raise the number.
4. **Keep it simple, and say each thing once.** Follow the structure in ARCHITECTURE.md: thin
   adapters over the service, one error path, closed value sets in `validation.py`. No
   abstraction for a single use.
5. **Write code to be read.** Small functions, names that say what a thing is, comments that
   give the reason.
6. **Remove dead code** in the change that makes it dead.
7. **Keep the documentation in step, and short.** Update it in the same change. The README
   answers the first questions and links onward; details belong in the deeper documents.

## The gates

The commit hook runs every gate except the last three on the files of a commit, and CI runs the
same hooks on all files. The push hook runs the whole suite before commits leave the checkout.
The tests also run in CI and with `pdm run test`. A gate that fails is fixed, not skipped
(`--no-verify` and `SKIP=` are not used).

Quality, test and dependency-audit jobs run independently for faster feedback. Within the
quality job, pre-commit runs its hooks sequentially. Each gate must pass before merge; scheduling does not weaken
that requirement. Unit tests and each harness shard retain JUnit XML artifacts and publish
counts plus failed/error test names in the run summary, even after test failure. Cancelled runs
do not add report errors. Diagnostic bodies are excluded from the summary; XML artifacts retain
pytest's original report. Local report files live under ignored `tmp/` directories.

| Gate | What it enforces |
| --- | --- |
| Ruff format and lint | Style, imports, likely bugs, security patterns, a bare `print` outside the gate scripts, a broad `except` that neither passes the exception on nor logs the traceback |
| basedpyright | Types, over `src`, `scripts` and `acceptance/src` |
| Complexity | Every function below cyclomatic complexity 8, nested ones and the tests included (`scripts/validation/check_complexity.py`) |
| Test quality | No test without an assertion, or with only mock or `callable` assertions (`scripts/validation/check_test_quality.py`) |
| Dead code | No unused functions, classes or variables (vulture) |
| gitleaks, zizmor | No secrets; safe GitHub Actions workflows |
| Upstream requirement packages | `docs/upstream/*.md` are the current render of `docs/upstream/catalogue.yaml` (`scripts/upstream_requirements.py --check`) |
| Acceptance ratchet (CI) | Every test of the acceptance suite, in fixture mode, has the outcome in `acceptance/expected/fixture.json`. A change that moves an outcome on purpose updates that file in the same pull request, and the diff of the file is what the review reads: `pdm run acceptance-expected update acceptance/fixture.json` writes it from a fresh report, and `pdm run acceptance-status` the README table that follows |
| Tests and coverage | The suite passes before each push and in CI; CI fails below the coverage minimum of standard 3 and warns when the aim is missed |
| Local run (CI only) | The documented setup, walked literally on `macos-latest` and `ubuntu-24.04` (`.github/workflows/local-run.yml`, on pushes to `main`, weekly, by hand, and on pull requests that touch the install paths). Every command is copied from the document that gives it; the test of the workflow compares the two, so change a documented command and its workflow line together. Not a required check: it uses live EVS |
| Dependency audit (CI only) | Every Python and npm package in the committed locks is at least five days old; no runtime dependency has a known vulnerability (pip-audit over the server and embeddings extras, on every change and weekly); no pull request brings one in at high severity (dependency review). Dependabot groups weekly updates for Actions, both Dockerfile directories and the documentation npm tree after a seven-day cooldown. Dependabot cannot update `pdm.lock`, so `pdm run lock` is the only Python lock command. [SECURITY.md](SECURITY.md) says how to report a vulnerability |

A dependency security fix inside the five-day window is an exception only when the owner records
its exact version and GHSA, CVE or PYSEC advisory in `[tool.nci-si.dependency-age.exceptions]`.
The lock and audit read that one list. The audit fails with “remove this exception” once the fixed
release is five days old; remove the entry then. An exception never admits another version.
The lock entry point reads the excepted version's upload time from the index at run time and
uses it as that package's cutoff; the timestamp is never committed. A lock run refreshes every
package to its newest admissible version. Treat a re-lock as a separate reviewed dependency
refresh with an old → new version table in its PR, never as a side effect of feature work.

Direct-URL wheels are checked by their index's upload time, matching the filename and locked
SHA-256; a missing or unverifiable index record fails the audit.

A finding is fixed in the code. Where a rule does not fit, it is suppressed as narrowly as
possible (a line, then a file or a directory, then the project, in `pyproject.toml`), with the
reason beside the suppression, or once in the module when it repeats.

## Workflow credentials

Only protected live checks need issued credentials. Anonymous live checks and fixture CI need
none. A maintainer may run `pdm run github-setup` after authenticating `gh`; it creates missing
names with `NCI_SI_CONFIGURE_ME`, never overwrites an existing name, and never reads secret values.
The live workflow treats the placeholder as absent and lists absent names in its summary.
Existing names may still contain dummy values: setup cannot inspect them. No caDSR credential
has been issued. The owner deleted the dummy on 24 October; setup can create its placeholder.

| Secret | Purpose | Workflow |
| --- | --- | --- |
| `NCI_SI_CADSR_CREDENTIAL` | Issued caDSR user:password for protected live checks | Acceptance (live) |
| `NCI_SI_EVS_LICENSE_KEY` | Issued EVS licence key for restricted live checks | Acceptance (live) |

Maintainer: run gh secret set NCI_SI_CADSR_CREDENTIAL --repo CBIIT/nci-si-mcp in your own terminal; it prompts for the value.

Maintainer: run gh secret set NCI_SI_EVS_LICENSE_KEY --repo CBIIT/nci-si-mcp in your own terminal; it prompts for the value.

`GITHUB_TOKEN` is automatic and is never created by setup. No repository Actions variables are
required. INT, UAT and Production environments remain deferred to the AWS phases; setup creates
no environments. Never put a credential in command arguments or shell history.

## Pull requests and releases

The government sponsor approves changes; the NCI SI MCP project coordinator relays that approval,
and the reviewer acts for the coordinator.

Merge clearance is a reviewer PR comment naming the head commit; a subsequent push needs new
clearance, after all triggered workflows pass. See [AGENTS.md](AGENTS.md#how-work-is-done-issues-into-a-milestone-branch-one-reviewed-milestone-into-main)
for the milestone process and [operations](docs/operations.md) for available signals, ATO
evidence inputs and open hosting responsibilities.

Every change reaches `main` through a pull request that is squash-merged. The pull request
title becomes the commit subject, and the next version is computed from it, so the title is a
[Conventional Commit](https://www.conventionalcommits.org) subject. A check on the pull request
fails otherwise. Changes to `spec/` and `acceptance/` follow the change control in
[acceptance/README.md](acceptance/README.md#change-control): the NCI SI MCP project coordinator is
their code owner.

| Title | Release (while the version is below 1.0) |
| --- | --- |
| `feat: …` | Minor: 0.3.1 → 0.4.0 |
| `fix: …`, `perf: …` | Patch: 0.3.1 → 0.3.2 |
| `!` after the type, as in `feat!: …` | Minor |
| `docs`, `style`, `refactor`, `test`, `chore`, `build`, `ci`, `revert`, `security`, `deprecate` | None |

A scope is optional, in lower case: `fix(index): …`. Choose the type with care, because it
decides the version. Only the title counts: the squash commit has no body, so mark a breaking
change with `!`, and do not edit the commit message in the merge dialog.

When CI passes on `main`, the release workflow tags that commit `vX.Y.Z` and creates a GitHub
release using semantic-release from the hash-locked `release` dependency group, not a third-party
action. The release job installs only that group, without the project. No file holds the
version, and the workflow never commits to `main`. If a release is missing although `main`
is green, start the Release workflow by hand
(`gh workflow run release.yml`). Version 1.0.0 is tagged by hand.

### Complete fixture ratchet

For the complete fixture ratchet, select the registry checks this server supports and supply
the furnished secured adapter and index preparation explicitly, as CI does:

```bash
NCI_SI_ACCEPTANCE_REGISTRY_RELEASE=required NCI_SI_ACCEPTANCE_SECURITY_SERVER='python ../scripts/permissions_fixture.py' NCI_SI_ACCEPTANCE_PREPARE='nci-si-mcp index-sample $(cat "$NCI_SI_ACCEPTANCE_INDEX_CODES")' pdm run acceptance -n 4 --dist worksteal --report=fixture.json
```

The harness default remains `optional`. Successors must supply their own secured adapter;
without it the secured cases report NOT RUN, not a security pass.
