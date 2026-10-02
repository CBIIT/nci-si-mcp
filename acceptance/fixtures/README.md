# Fixtures

The upstream responses the suite runs against in fixture mode (MCP Behavioral Acceptance Suite §2).
Each is a JSON file in the format `src/nci_si_acceptance/fixture_server.py` describes, and states
whether it was **recorded** from a live service (and when) or **crafted** for a case the live
service does not produce on demand (and which requirement it stands in for).

- `recorded/<surface>/` — captured from live by `record.py`, against the release `manifest.yaml` pins.
- `crafted/<requirement>/` — hand-written.
- `scenarios/<name>/` — the scenario fixtures that provoke specific behaviour.
