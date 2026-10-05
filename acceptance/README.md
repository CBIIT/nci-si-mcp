# NCI SI MCP acceptance suite

The behavioural acceptance suite for the NCI SI MCP tools, which tests the requirements of the
[specification](../docs/specification.md), and the upstream fixture server it runs against. It tests the MCP
tool surface of a server it starts as a command or of a remote one it connects to; it knows
nothing of the server's code.

From the repository root:

```bash
pdm run acceptance --report=fixture.json             # fixture mode; writes acceptance/fixture.json
NCI_SI_ACCEPTANCE_MODE=live pdm run acceptance --report=live.json  # live-capable tests
NCI_SI_ACCEPTANCE_SERVER="..." pdm run acceptance    # another server (default: nci-si-mcp serve)
NCI_SI_ACCEPTANCE_URL=https://... pdm run acceptance # a remote server over streamable HTTP (below)
NCI_SI_ACCEPTANCE_PROFILE=evs NCI_SI_ACCEPTANCE_SERVER="..." pdm run acceptance  # a server of one profile (default: unified)
pdm run python -m nci_si_acceptance.report acceptance/fixture.json --live acceptance/live.json
pdm run acceptance-index-codes                       # the index set, one code per line (remote server)
pdm run acceptance -n 4 --report=fixture.json        # the same on four workers; the report is identical
pdm run acceptance-expected check acceptance/fixture.json   # a report against the expected outcomes
pdm run acceptance-expected update acceptance/fixture.json  # rewrite the expected outcomes from a report
pdm run acceptance-status                            # regenerate the README's status table
pdm run acceptance-selftest                          # the harness's own tests
SELFTEST_SHARD=1/3 pdm run acceptance-selftest       # one of three shards, as CI runs them
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

## Remote server

The platform's server is remote: `NCI_SI_ACCEPTANCE_URL` names its streamable-HTTP endpoint in
place of `NCI_SI_ACCEPTANCE_SERVER` (naming both stops the run). The harness starts nothing and
cannot set the environment of a process it did not start, so the operator does three things the
harness otherwise does: sets the server's upstream, changes its state for the tests that need a
server of their own, and prepares its index. Run it in one process: xdist workers together with
`NCI_SI_ACCEPTANCE_URL` are a usage error. The report records each run's transport, `stdio` or
`streamable-http`, in the JSON and in the rendered report.

| Setting | Meaning |
|---|---|
| `NCI_SI_ACCEPTANCE_URL` | The endpoint, in place of `NCI_SI_ACCEPTANCE_SERVER` |
| `NCI_SI_ACCEPTANCE_AUTHORIZATION` | Sent as the `Authorization` header of every request. A credential: never logged, never in the report or an error message, and withheld, with the token after its scheme, from the output of a failing test; it is not given to the state hook |
| `NCI_SI_ACCEPTANCE_FIXTURE_BIND` | `HOST` or `HOST:PORT` the fixture server listens on (default `127.0.0.1`, any port) |
| `NCI_SI_ACCEPTANCE_FIXTURE_URL` | The base URL the server reaches the fixture server by, where it is not the bind address (set it when the bind is `0.0.0.0`: the harness warns otherwise) |
| `NCI_SI_ACCEPTANCE_STATE_HOOK` | The operator's state-change hook (below) |
| `NCI_SI_ACCEPTANCE_STATE_HOOK_TIMEOUT` | Seconds the hook may take to return, and again the endpoint to answer after it (default 60) |
| `NCI_SI_ACCEPTANCE_PREPARED` | `1`: the operator has prepared the server's index (below) |

**Fixture mode: what the operator sets on the server.** At the start of the run the harness prints
the settings, the per-surface base URLs (`NCI_SI_EVS_BASE_URL`, …, each the fixture server's URL
and the surface's name) and `NCI_SI_UPSTREAM_MODE=fixture`. Set them on the server before the run,
with `NCI_SI_ACCEPTANCE_FIXTURE_BIND` on a fixed port so that they do not change. Before the first
test the harness calls `resolve_release` and requires the fixture server's log to show the request;
otherwise it stops with "the server under test does not reach the fixture server". A server that
answers from a cache filled before the run asks nothing and fails the probe the same way: change
its state first, or give the state hook, which the harness then runs before the probe; what the
server asks while the hook runs counts as reaching the fixture server. In live mode
there is no fixture server; the probe only requires that `resolve_release` answers ("the server
under test does not answer" otherwise). A profile without `resolve_release` is probed by
`tools/list` alone.

**The state hook.** A shell command line that is called with the name of a scenario set and its
settings, and whose contract is: apply these settings and forget every upstream answer cached so
far. A restart is the simplest implementation and satisfies the contract, as a process per
scenario gives over stdio; an operator whose server can flush its cache and reread its settings
may do that instead. The harness runs it with its own environment, less the credential and less
any `NCI_SI_*` setting that is not an `NCI_SI_ACCEPTANCE_*` one, plus the fixture settings that
the announcement tells the operator, `NCI_SI_ACCEPTANCE_SCENARIOS` (the scenario set's names,
separated by commas, empty for none) and the set's settings (none for an `own_server` test). It
waits for the endpoint to answer, and then runs the tests: before the first test, once for each
scenario set's tests, before each `own_server` test, and at the end, without settings, to leave
the server as it was found. The hook must return once the old state is gone (for a restart: once
the old instance has stopped), with the output of any process it leaves running redirected. The
harness then waits for the endpoint, up to the timeout, and stops at once on an HTTP 401 or 403.
The tests on the server as the operator started it run first, then the `own_server` tests, then
each scenario set's. Without the hook those tests are skipped as "needs a server of its own
(NCI_SI_ACCEPTANCE_STATE_HOOK)"; they count as not run, so a tool whose scenario tests did not run
is never PASS (NOT RUN, or INCOMPLETE where others passed). A test that needs a server without
the index (`unprepared`) is always skipped: a remote server cannot be made one.

**The index.** The harness never runs `NCI_SI_ACCEPTANCE_PREPARE` against a remote server (naming
it is a usage error). The operator prepares the server's index, before the run or inside the
state hook, and declares it with `NCI_SI_ACCEPTANCE_PREPARED=1`; tests marked `prepared`
are NOT RUN until then. `pdm run acceptance-index-codes` prints the index set, one code per line,
for the operator to index. A server declared prepared must hold exactly that set: the semantic
tests assert `totalKnown` equal to its size.

The harness cannot read a remote server's standard error or data directory, so the checks that a
secret is in neither (X-12) cover what the server returns.

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

## CI: the ratchet on expected outcomes

The `acceptance` job of `.github/workflows/ci.yml` runs the suite in fixture mode against the
server built from the checkout (`pdm run acceptance -n 4 --report=fixture.json` with the prepare
step above), beside the `test` and `selftest` jobs and depending on none of them. Pytest's exit
status 0 and 1 are both a run; any other status, or a missing report, fails the job. The verdict
is the comparison with [`expected/fixture.json`](expected/fixture.json), which maps the id of
every test to its outcome (`passed`, `failed`, `no_fixture`, `skipped`, `not_implemented`,
`not_live`: the report's own vocabulary) and holds nothing else, so a reworded failure is not a
change. The job fails on any test with another outcome, any test the report lacks and any test
the expected outcomes lack, and writes the differences to its summary as a table of test,
expected and actual outcome. It stays green while tools are NOT IMPLEMENTED or FAIL, and fails
when a test of a tool that still fails stops passing, or starts to pass without anyone saying so.

A change that moves outcomes on purpose (a tool implemented, a test added or corrected) updates
the expected outcomes in the same pull request: run the suite as the job does, then
`pdm run acceptance-expected update acceptance/fixture.json`, and `pdm run acceptance-status` for
the README's table, which is generated from the expected outcomes and kept current by a
self-test. The diff of `expected/fixture.json` is what the review reads.

The job's summary holds the run's duration (a warning at 7 minutes of the 10 allowed: shard the
run before it grows into the limit) and the per-tool report; the artifact `acceptance-fixture`
keeps `fixture.json` and the rendered report for 30 days. A run on workers writes the report a
serial run writes: the controller process collects each test's tool, gate and outcome from the
reports its workers forward.

## The live workflow

`.github/workflows/acceptance-live.yml` is started by hand (`workflow_dispatch` only). It runs the
fixture suite and the live suite (`NCI_SI_ACCEPTANCE_MODE=live`, both with `-n 4`; the live
outcomes are not ratcheted), renders the combined report into the job summary and uploads
`fixture.json`, `live.json`, the rendered report and `network.md` as the artifact
`acceptance-live`. `network.md` records where the run came from: the runner's environment,
operating system, architecture and name, and whether EVS answers over IPv4 and over IPv6
(`curl -4` and `curl -6`, 10 seconds each; a failure is recorded and does not fail the job). The
repository secrets `NCI_SI_EVS_LICENSE_KEY` and `NCI_SI_CADSR_CREDENTIAL` are optional and reach
only the step that runs the live suite, and through it only the server under test: the harness
gives a server no other `NCI_SI_*` setting, and the credentials only in live mode. A secret that
is not set is unset in that step, not passed empty. A credential appears in no log, error or
result (requirement A7.5); `network.md` names which credentials were given, never a value.

## Layout

`src/nci_si_acceptance/` holds the harness: `client.py` starts the server or connects to it,
`remote.py` probes a remote server and runs the operator's state hook, `tools.py` calls the
required tools, `fixture_server.py` serves the fixtures (its docstring documents the format),
`report.py` writes and renders the per-tool report, `suite_identity.py` digests the suite and
decides whether a report is an acceptance report, `spec.py` reads the specification in
`../spec/` (the required tools among it), `requirements.py` checks the tests' citations of its
requirements, `document.py` renders it as `../docs/specification.md`, and `suite.py` holds the
rules of a run. `concepts.py` composes EVS concept answers from one recording per
concept, `record.py` records the set from live,
`craft.py` crafts the scenarios EVS does not produce on demand, `register.py` writes the
register of request forms (`request-forms/`), `expected.py` compares a report with the expected
outcomes (`expected/fixture.json`) and rewrites them, and `status.py` writes the README's status
table from them.
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
