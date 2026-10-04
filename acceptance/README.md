# NCI SI MCP acceptance suite

The behavioural acceptance suite for the NCI SI MCP tools, which tests the requirements of the
[specification](../docs/specification.md), and the upstream fixture server it runs against. It tests the MCP
tool surface of a server it starts as a command; it knows nothing of the server's code.

From the repository root:

```bash
pdm run acceptance --report=fixture.json             # fixture mode; writes acceptance/fixture.json
NCI_SI_ACCEPTANCE_MODE=live pdm run acceptance --report=live.json  # live-capable tests
NCI_SI_ACCEPTANCE_SERVER="..." pdm run acceptance    # another server (default: nci-si-mcp serve)
NCI_SI_ACCEPTANCE_PROFILE=evs NCI_SI_ACCEPTANCE_SERVER="..." pdm run acceptance  # a server of one profile (default: unified)
pdm run python -m nci_si_acceptance.report acceptance/fixture.json --live acceptance/live.json
pdm run acceptance-selftest                          # the harness's own tests
SELFTEST_SHARD=1/2 pdm run acceptance-selftest       # one of two shards, as CI runs them
pdm run acceptance-record                            # re-record fixtures/recorded/ from live
pdm run acceptance-craft                             # rebuild the crafted scenarios
pdm run acceptance-register                          # regenerate request-forms/ from the manifest
pdm run spec-render                                  # regenerate docs/specification.md from spec/
```

## Writing a test

A test calls a required tool by its name, `tools.call("get_concept", {...})`, and names the tool
it is for with `@pytest.mark.tool("get_concept")`; a protocol gate is marked `gate`. A gate that cannot
run leaves the module unaccepted, since acceptance needs every gate to pass. When the
server lacks the tool, the baseline tool map (`fixtures/baseline_toolmap.yaml`) may name a tool
that stands in for it; otherwise the test is skipped as NOT IMPLEMENTED. Each test asserts only
what the specification says.

Each test also cites the requirements it enforces, `@pytest.mark.requirement("X-2")`, by their
ids in [`../spec/requirements.yaml`](../spec/requirements.yaml), the project's statement of the
behaviour the suite tests and the server implements. Every requirement is cited by a test or
planned in an issue; `selftests/test_requirements.py` fails otherwise, and on a test that cites
nothing or an unknown id. A citation may cover part of a requirement: `planned` stays until the
citing tests cover all of it. A test that never runs (skip, a true skipif, xfail) covers nothing.

The cross-cutting tests (`tests/test_crosscutting.py`) run against every tool that has a call
in `tests/calls.yaml`, and find a result's items where the tool's `items` in
[`../spec/tools.yaml`](../spec/tools.yaml) say. A tool joins them with those two entries.

`@pytest.mark.scenario("release/unknown")` serves the scenario's fixtures before the ordinary
ones, to a server process of its own started with the scenario's settings. A test that must
see the server ask upstream is marked `own_server`, for a server process of its own that no
earlier call can have filled a cache of. `tools.process` keeps what a test may need of the
server process: its standard error and data directory (`written()`, everything it wrote), and the
upstream requests it made while it started. A test marked
`live_capable` also runs in live mode, unless it selects a scenario; every other test runs
against fixtures only.

A test that passes against fixtures and fails live means a fixture is wrong (corrected by
re-recording, under change control) or the live service has changed: both are findings. One
that passes live and fails against fixtures means the server behaves differently against
different upstreams: a defect.

A run may begin with one operator-supplied prepare command, `NCI_SI_ACCEPTANCE_PREPARE`, a
shell command line run once, before the first test that starts a server, in the server's
environment against the ordinary fixtures (in live mode, the live services): the same settings
the server gets, its upstream base URLs and a fresh `NCI_SI_DATA_DIR`. The file that
`NCI_SI_ACCEPTANCE_INDEX_CODES` names holds the index set, one code per line: every concept the
fixture set records at an include that holds its summary. Every server then
starts from a copy of that data directory. A test marked `prepared` needs it and is NOT RUN
without the command; a command that fails, or whose requests find no fixture, ends the run.
For this server it builds the interim NCIt index, in under a second and under a MB with the
default hashing embedder:

    NCI_SI_ACCEPTANCE_PREPARE='nci-si-mcp index-sample $(cat "$NCI_SI_ACCEPTANCE_INDEX_CODES")'

Run it only through the suite: the base URLs it is given name each surface of the fixture
server (`NCI_SI_EVS_BASE_URL` ends in `/evs`, to which the server adds `/api/v1/…` as it does to
the production host), and a command pointed at the fixture server's bare address finds no
fixture. Every setting the suite gives a server, and its format, is in the specification's §5
([`spec/acceptance.md`](../spec/acceptance.md)).

In fixture mode a test fails when one of its upstream requests found no fixture, and so does a
server whose requests while it starts found none: the server may treat the refusal as an outage
and still answer plausibly. A test that provokes such requests on purpose is marked
`unmatched_upstream`.

## The report

`--report` writes one JSON report per run; `nci_si_acceptance.report` renders it as the per-tool
table of the specification's §5. A tool is PASS, FAIL (a failed gate fails every tool), NO FIXTURE (a request lacked
a fixture: a question for the fixture set), INCOMPLETE (the tests that ran passed but some could
not run: a hardening candidate), NOT IMPLEMENTED, NOT RUN or NO TESTS; the module docstring
defines each, and each row counts the tests passed, failed, without a fixture and not run.
The rendered report opens with its title and the identity of the suite: the suite version, the
fixture-set version (the pinned release and the date of the latest recording) and the suite
digest. The title is ACCEPTANCE REPORT only when that digest is in `approved.yaml`; otherwise it
is MODIFIED, which is every report until the furnished tag. The JSON report carries the same
identity. Below the table it names the gates that failed and those that did not run, and gives the size
of the tools/list result in bytes, which every client session reads.
Combined with a live report, a tool that
passes against fixtures but fails live is PASS (fixture only) only when every failing live test
has a documented upstream limitation (`--limitations`, YAML of test id to requirement).

## Layout

`src/nci_si_acceptance/` holds the harness: `client.py` starts the server, `tools.py` calls the
required tools, `fixture_server.py` serves the fixtures (its docstring documents the format),
`report.py` writes and renders the per-tool report, `suite_identity.py` digests the suite and
decides whether a report is an acceptance report, `spec.py` reads the specification in
`../spec/` (the required tools among it), `requirements.py` checks the tests' citations of its
requirements, `document.py` renders it as `../docs/specification.md`, and `suite.py` holds the
rules of a run. `concepts.py` composes EVS concept answers from one recording per
concept, `record.py` records the set from live,
`craft.py` crafts the scenarios EVS does not produce on demand, and `register.py` writes the
register of request forms (`request-forms/`).
`fixtures/` holds the fixtures ([fixtures/README.md](fixtures/README.md)), `tests/` the suite,
`selftests/` the tests of the harness itself.

The suite's version is the repository's nearest `vX.Y.Z` tag, derived at install time as the server's
is; the fixture set is versioned apart, by its pinned release and recording dates.

## Change control

The NCI SI MCP project coordinator is the code owner of `spec/` and `acceptance/`. From the
furnished tag, a change to `spec/` or to the suite needs the written approval of the branch chief
or a delegate ([spec/acceptance.md](../spec/acceptance.md)); a report from a changed suite renders
MODIFIED, because only the digests of approved releases, listed in
[approved.yaml](approved.yaml), make an acceptance report; and [CHANGELOG.md](CHANGELOG.md)
records each approved change with the requirement it serves.

The digest covers `tests/`, `fixtures/` with the manifest, `request-forms/`, `src/`, `pyproject.toml`
(the pytest configuration and the dependency pins decide what runs) and `../spec/`; it leaves out
the self-tests, `README.md`, `CHANGELOG.md` (a record that carries release digests), caches, editor
and system litter, `approved.yaml` and the server under test, so one approved suite attests any
server (`src/nci_si_acceptance/suite_identity.py` defines the set). A symbolic link among the
digested files, or a root that lacks any of them, is an error, never a partial digest.

What the check is and is not:

- It is a tripwire, not tamper-proofing. `approved.yaml` changes by review by the NCI SI MCP
  project coordinator, and the report's identity block is self-stated.
- Approval is attested on a clean checkout, since untracked files inside the digested paths count.
- Reports are written outside the digested paths (as `--report=fixture.json` in `acceptance/`
  already is); the digest is taken when the run starts, from the checkout the run is in, and a run
  against a harness installed from another checkout is refused.
- The suite version in the report comes from install metadata (the nearest tag), so run
  `pdm install` after a tag. The digest alone decides approval.
