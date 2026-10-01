# Contributing

How to work on this repository. What the server does is in [README.md](README.md); how it is
built is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Set up

You need Python 3.13 or newer and [PDM](https://pdm-project.org).

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
| basedpyright | Types, over `src` and `scripts` |
| Complexity | Every function below cyclomatic complexity 8, nested ones and the tests included (`scripts/validation/check_complexity.py`) |
| Test quality | No test without an assertion, or with only mock or `callable` assertions (`scripts/validation/check_test_quality.py`) |
| Dead code | No unused functions, classes or variables (vulture) |
| gitleaks, zizmor | No secrets; safe GitHub Actions workflows |
| Tests and coverage | The suite passes; CI fails below the coverage minimum of standard 3 and warns when the aim is missed |

A finding is fixed in the code. Where a rule does not fit, it is suppressed as narrowly as
possible (a line, then a file or a directory, then the project, in `pyproject.toml`), with the
reason beside the suppression, or once in the module when it repeats.

## Pull requests and releases

Every change reaches `main` through a pull request that is squash-merged. The pull request
title becomes the commit subject, and the next version is computed from it, so the title is a
[Conventional Commit](https://www.conventionalcommits.org) subject. A check on the pull request
fails otherwise.

| Title | Release (while the version is below 1.0) |
| --- | --- |
| `feat: …` | Minor: 0.3.1 → 0.4.0 |
| `fix: …`, `perf: …` | Patch: 0.3.1 → 0.3.2 |
| `!` after the type, as in `feat!: …` | Minor |
| `docs`, `style`, `refactor`, `test`, `chore`, `build`, `ci`, `revert`, `security`, `deprecate` | None |

A scope is optional: `fix(index): …`. Only the title counts: the squash commit has no body, so
a `BREAKING CHANGE:` footer is never seen. Choose the type with care, because it decides the
version.

When CI passes on `main`, the release workflow tags that commit `vX.Y.Z` and creates a GitHub
release. Nothing else is needed: no file holds the version, and the workflow never commits to
`main`. If a release is missing although `main` is green, start the Release workflow by hand
(`gh workflow run release.yml`). Version 1.0.0 is tagged by hand.
