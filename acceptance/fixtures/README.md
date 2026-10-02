# Fixtures

The upstream responses the suite runs against in fixture mode (MCP Behavioral Acceptance Suite §2).
Each is a JSON file in the format `src/nci_si_acceptance/fixture_server.py` describes, and states
whether it was **recorded** from a live service (and when) or **crafted** for a case the live
service does not produce on demand (and which requirement it stands in for).

- `recorded/<surface>/`: captured from the live service.
- `crafted/<requirement>/`: hand-written.
- `scenarios/<group>/<name>/`: the fixtures of one scenario, which answer before the ordinary
  ones while a test selects it (`@pytest.mark.scenario("<group>/<name>")`). A `settings.json`
  there holds the `NCI_SI_*` settings the scenario's server process starts with.
- `baseline_toolmap.yaml`: the stand-ins for required tools the server lacks.
