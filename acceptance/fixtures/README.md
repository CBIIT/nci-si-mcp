# Fixtures

The upstream responses the suite runs against in fixture mode.
Each is a JSON file in the format `src/nci_si_acceptance/fixture_server.py` describes, and states
whether it was **recorded** from a live service (and when) or **crafted** for a case the live
service does not produce on demand (and which requirement it stands in for).

- `manifest.yaml`: the pinned release (NCIt `ncit_26.09d`), the concept rules, what each
  scenario provokes, and every request `pdm run acceptance-record` records, with its operation and rationale.
- `recorded/<surface>/`: captured from the live service by `acceptance-record`; concepts in
  `recorded/evs/concepts/`.
- `crafted/<requirement>/`: the form a requirement prescribes where EVS does not answer it yet,
  carrying a recording's answer (`acceptance-record`, the manifest's `derived`).
- `scenarios/<group>/<name>/`: the fixtures of one scenario, which answer before the ordinary
  ones while a test selects it (`@pytest.mark.scenario("<group>/<name>")`). A `settings.json`
  there holds the `NCI_SI_*` settings the scenario's server process starts with.
- `baseline_toolmap.yaml`: the stand-ins for required tools the server lacks.

## Scenarios

The scenario fixtures are recorded from live where EVS produces the
case on demand (`acceptance-record`) and crafted otherwise (`pdm run acceptance-craft`, from the
recorded set where they change a real answer). The manifest's `scenarios` section says what each
provokes; the register below lists them with how each is made and its requests.

## Recording

After `pdm run acceptance-record`, run `pdm run acceptance-craft` and `pdm run
acceptance-register`: the self-tests check that crafted scenarios and the register are current.
`acceptance-record` re-records the set from live and writes nothing unless every check
holds: the live monthly release is still the pinned one; every request answers with the status
its entry expects; every concept recording is one the fixture server accepts and the composed
concept answers equal real ones; every derived fixture comes from a recording of the pinned
release; and no file under `recorded/`, and no derived fixture, is left over. The set grows with
the tests: a test that needs another upstream answer adds its request to the manifest and
re-records. Re-pinning the release is a re-recording under change control. The fixture set is
versioned on its own: a re-recording or a re-pinning makes a new fixture-set version,
independent of the suite's.

## Request forms

The fixture set constrains the form of an upstream request only where the specification
prescribes it: the release-pinned path, the batch endpoint in place of one request per concept,
the one-row release query, `Accept: application/json`, and the licence key. Elsewhere it answers
any form a fixture records.

The register of request forms, generated from the manifest, is written for the service teams, in
a view for the EVS team, one for the caDSR team and one for both:
[`../request-forms/`](../request-forms/). It opens with the operations without a pinned form
upstream, with the release each payload reports; then lists every ordinary request with the
platform operation it serves (its `OP-` id, from `operations.yaml` of the programme's platform
conformance suite), whether it is recorded or crafted
(and for which requirement), and the reason for its form; then each scenario.
Where EVS does not yet answer the form a requirement prescribes, the ordinary fixture is crafted
to the requirement and names it, and the live run shows the gap.

Two kinds of request are answered whatever their form:

- **Concepts.** One recording per concept answers every projection (`include`) of that concept
  and each of its relation lists the recording covers, and every batch (`?list=`) is composed
  from the recordings of the concepts it names, by the rules of `src/nci_si_acceptance/concepts.py`. A batch comes back in no particular order: EVS keeps none.
- **Ignored parameters.** A parameter the live service is shown to ignore is declared with the
  evidence and left out of the match (FHIR `$expand` ignores `count`, `offset` and `activeOnly`).

A request in any other form finds no fixture, and the test that sent it reports NO FIXTURE.
Two more things decide which fixture answers (`fixture_server.py` has the rules): a fixture may
name request headers it requires (a licence key), and while a test selects a scenario, the
scenario's fixtures answer before any ordinary one, `"*"` under `ignored` making one
answer whatever parameters are asked, and a scenario fixture whose path is `"*"` answering
every request of its surface that the scenario's other fixtures do not
(`upstream/unavailable`). A request the concept rules answer has no ordinary exact fixture:
the loader refuses one, since it would answer before a scenario's recording.

## Content and terms

The recorded content is as the EVS REST API (`api-evsrest.nci.nih.gov`) serves it publicly,
without a licence key, retrieved on 2 October 2026 from NCI Thesaurus™ release 26.09d, which NCI
releases under the Creative Commons Attribution 4.0 International licence
([terms of use](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/ThesaurusTermsofUse.htm)). The code of
the suite is under the Apache License 2.0.
