# NCI SI MCP acceptance suite

The behavioural acceptance suite for the NCI SI MCP tools, specified by the programme's *MCP
Behavioral Acceptance Suite*, and the upstream fixture server it runs against. It tests the MCP
tool surface of a server it starts as a command; it knows nothing of the server's code.

From the repository root:

```bash
pdm run acceptance                                   # the suite, fixture mode
NCI_SI_ACCEPTANCE_MODE=live pdm run acceptance       # the live-capable tests against production
NCI_SI_ACCEPTANCE_SERVER="..." pdm run acceptance    # another server command (default: nci-si-mcp serve)
pdm run acceptance-selftest                          # the harness's own tests
```

`src/nci_si_acceptance/` holds the harness (`client.py`), the fixture server (`fixture_server.py`,
which documents the fixture format) and the rules of a run (`suite.py`); `fixtures/` the fixtures
([fixtures/README.md](fixtures/README.md)); `tests/` the suite; `selftests/` the tests of the
harness itself.

In fixture mode a test fails when one of its upstream requests found no fixture: the server may
treat the refusal as an outage and still answer plausibly. A test that provokes such requests on
purpose is marked `unmatched_upstream`.

The suite is versioned on its own (`pyproject.toml` here), independently of the server.
