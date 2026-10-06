# Contributing

How to work on this repository. What it is, and its status, is in [README.md](README.md); how to
install and run the server is in [QUICKSTART.md](QUICKSTART.md); how it is built is in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Set up

You need Python 3.14 or newer and [PDM](https://pdm-project.org).

```bash
pdm install                    # .venv from pdm.lock, with the test and lint tools
pdm run pre-commit install     # run the gates on every commit
```

## Everyday commands

| Command | What it does |
| --- | --- |
| `pdm run test` | The whole suite, with the coverage minimum |
| `pdm run pytest tests/test_index.py -k name` | A selection of tests, without the minimum |
| `pdm run lint` | Ruff and basedpyright: the fast check while you work |
| `pdm run fmt` | Format with Ruff |
| `pdm run pre-commit run --all-files` | Every hook, as the CI `quality` job runs them |
| `pdm run acceptance` | The acceptance suite against the server ([acceptance/README.md](acceptance/README.md)) |
| `pdm run acceptance-http` | Real HTTP fixture suite, managed restarts and explicit remote-contract skips ([transport details](docs/transport.md)) |
| `pdm run python scripts/benchmark.py --output tmp/benchmark.json` | Representative MCP measurements with actual outbound counts ([method and evidence](docs/benchmark.md)) |
| `pdm run python scripts/upstream_requirements.py` | Regenerate the [upstream requirements packages](docs/upstream/README.md) from the catalogue and measured reports; `--check` verifies them without writing |
| `pdm run acceptance -n 4 --report=fixture.json` | The acceptance suite on four workers, with the report CI compares ([the ratchet](acceptance/README.md#ci-the-ratchet-on-expected-outcomes)) |
| `pdm run acceptance-expected check acceptance/fixture.json` | The report against `acceptance/expected/fixture.json`, test by test |
| `pdm run acceptance-status` | Regenerate the status table of the README from the expected outcomes |
| `pdm run spec-render` | Regenerate [docs/specification.md](docs/specification.md) from `spec/`, after any change there |
| `pandoc -f gfm docs/specification.md -o specification.docx` | The Word copy of the specification, a build product: never committed or edited |

The tests are `unittest.TestCase` classes, run by pytest. None contacts EVS: `FakeEVS` in
`tests/fakes.py` stands in for the client, and the client's own tests replace `urlopen`.

## Standards

1. **Stay on the goal.** A change does what its task asks.
2. **Tests are written for their value.** Each one checks behaviour that a user or a caller can
   observe, and can fail when that behaviour breaks.
3. **Coverage: 90% is the checked minimum, above 95% is the aim.** The gap is deliberate: it
   keeps anyone from writing tests for the number. Cover untested behaviour whenever you can.
   Never add a test that asserts nothing, or only that a mock was called.
4. **Keep it simple, and say each thing once.** Follow the structure in ARCHITECTURE.md: thin
   adapters over the service, one error path, closed value sets in `validation.py`. No
   abstraction for a single use.
5. **Write code to be read.** Small functions, names that say what a thing is, comments that
   give the reason.
6. **Remove dead code** in the change that makes it dead.
7. **Keep the documentation in step, and short.** Update it in the same change. The README
   answers the first questions and links onward; details belong in the deeper documents.

## The gates

Pre-commit runs every gate except the last on the files of a commit, and CI runs the same hooks
on all files. The tests run in CI and with `pdm run test`. A gate that fails is fixed, not
skipped (`--no-verify` and `SKIP=` are not used).

| Gate | What it enforces |
| --- | --- |
| Ruff format and lint | Style, imports, likely bugs, security patterns, a bare `print` outside the gate scripts, a broad `except` that neither passes the exception on nor logs the traceback |
| basedpyright | Types, over `src`, `scripts` and `acceptance/src` |
| Complexity | Every function below cyclomatic complexity 8, nested ones and the tests included (`scripts/validation/check_complexity.py`) |
| Test quality | No test without an assertion, or with only mock or `callable` assertions (`scripts/validation/check_test_quality.py`) |
| Dead code | No unused functions, classes or variables (vulture) |
| gitleaks, zizmor | No secrets; safe GitHub Actions workflows |
| Acceptance ratchet (CI) | Every test of the acceptance suite, in fixture mode, has the outcome in `acceptance/expected/fixture.json`. A change that moves an outcome on purpose updates that file in the same pull request, and the diff of the file is what the review reads: `pdm run acceptance-expected update acceptance/fixture.json` writes it from a fresh report, and `pdm run acceptance-status` the README table that follows |
| Tests and coverage | The suite passes; CI fails below the coverage minimum of standard 3 and warns when the aim is missed |
| Dependency audit (CI only) | No runtime dependency with a known vulnerability (pip-audit over the server and embeddings extras, on every change and weekly); no pull request bringing one in at high severity (dependency review). Dependabot proposes updates of the workflows' actions; SECURITY.md says how to report a vulnerability |

A finding is fixed in the code. Where a rule does not fit, it is suppressed as narrowly as
possible (a line, then a file or a directory, then the project, in `pyproject.toml`), with the
reason beside the suppression, or once in the module when it repeats.

## Pull requests and releases

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
release. Nothing else is needed: no file holds the version, and the workflow never commits to
`main`. If a release is missing although `main` is green, start the Release workflow by hand
(`gh workflow run release.yml`). Version 1.0.0 is tagged by hand.
