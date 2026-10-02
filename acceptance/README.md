# NCI SI MCP acceptance suite

The behavioural acceptance suite for the NCI SI MCP tools, specified by the programme's *MCP
Behavioral Acceptance Suite*, and the upstream fixture server it runs against. It tests the MCP
tool surface of a server it starts as a command; it knows nothing of the server's code.

From the repository root:

```bash
pdm run acceptance --report=fixture.json             # fixture mode; writes acceptance/fixture.json
NCI_SI_ACCEPTANCE_MODE=live pdm run acceptance --report=live.json  # live-capable tests
NCI_SI_ACCEPTANCE_SERVER="..." pdm run acceptance    # another server (default: nci-si-mcp serve)
pdm run python -m nci_si_acceptance.report acceptance/fixture.json --live acceptance/live.json
pdm run acceptance-selftest                          # the harness's own tests
pdm run acceptance-record                            # re-record fixtures/recorded/ from live
pdm run acceptance-craft                             # rebuild the crafted scenarios
pdm run acceptance-register                          # regenerate request-forms/ from the manifest
```

## Writing a test

A test calls a required tool by its name, `tools.call("get_concept", {...})`, and names the tool
it is for with `@pytest.mark.tool("get_concept")`; a protocol gate is marked `gate`. When the
server lacks the tool, the baseline tool map (`fixtures/baseline_toolmap.yaml`) may name a tool
that stands in for it; otherwise the test is skipped as NOT IMPLEMENTED. Each test cites the
section of the specification it asserts, and asserts only what that section says.

`@pytest.mark.scenario("release/unknown")` serves the scenario's fixtures before the ordinary
ones, to a server process of its own started with the scenario's settings. A test marked
`live_capable` also runs in live mode, unless it selects a scenario; every other test runs
against fixtures only.

In fixture mode a test fails when one of its upstream requests found no fixture, and so does a
server whose requests while it starts found none: the server may treat the refusal as an outage
and still answer plausibly. A test that provokes such requests on purpose is marked
`unmatched_upstream`.

## The report

`--report` writes one JSON report per run; `nci_si_acceptance.report` renders it as the per-tool
table of §6. A tool is PASS, FAIL (a failed gate fails every tool), NO FIXTURE (a request lacked
a fixture: a question for the fixture set), INCOMPLETE (the tests that ran passed but some could
not run: a hardening candidate), NOT IMPLEMENTED, NOT RUN or NO TESTS; the module docstring
defines each, and each row counts the tests passed, failed, without a fixture and not run.
Combined with a live report, a tool that
passes against fixtures but fails live is PASS (fixture only) only when every failing live test
has a documented upstream limitation (`--limitations`, YAML of test id to requirement).

## Layout

`src/nci_si_acceptance/` holds the harness: `client.py` starts the server, `tools.py` calls the
required tools, `fixture_server.py` serves the fixtures (its docstring documents the format),
`report.py` writes and renders the per-tool report, `inventory.py` lists the required tools and
`suite.py` the rules of a run. `concepts.py` composes EVS concept answers from one recording per
concept, `record.py` records the set from live,
`craft.py` crafts the scenarios EVS does not produce on demand, and `register.py` writes the
register of request forms (`request-forms/`).
`fixtures/` holds the fixtures ([fixtures/README.md](fixtures/README.md)), `tests/` the suite,
`selftests/` the tests of the harness itself.

The suite is versioned on its own (`pyproject.toml` here), independently of the server.
