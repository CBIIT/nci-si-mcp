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
| `pdm run pre-commit run --all-files` | Every gate, as CI runs them |

No test contacts EVS: `tests/fakes.py` stands in for it.

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

Every gate runs on commit through pre-commit, and CI runs the same hooks on all files. A gate
that fails is fixed, not skipped (`--no-verify` and `SKIP=` are not used).

| Gate | What it enforces |
| --- | --- |
| Ruff format and lint | Style, imports, likely bugs, security patterns, `print` outside the CLI, a broad `except` that neither logs nor re-raises |
| basedpyright | Types, over the whole project |
| Complexity | Every function below cyclomatic complexity 8 (`scripts/validation/check_complexity.py`) |
| Test quality | No test without an assertion, or with only mock or `callable` assertions (`scripts/validation/check_test_quality.py`) |
| Dead code | No unused functions, classes or variables (vulture) |
| gitleaks, zizmor | No secrets; safe GitHub Actions workflows |
| Coverage | CI fails below 90% and warns at or below 95% |

A finding is fixed in the code. A suppression is per line, with the reason next to it.
