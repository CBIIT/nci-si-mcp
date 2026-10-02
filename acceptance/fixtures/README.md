# Fixtures

The upstream responses the suite runs against in fixture mode (MCP Behavioral Acceptance Suite §2).
Each is a JSON file in the format `src/nci_si_acceptance/fixture_server.py` describes, and states
whether it was **recorded** from a live service (and when) or **crafted** for a case the live
service does not produce on demand (and which requirement it stands in for).

- `manifest.yaml`: the pinned release (NCIt `ncit_26.09d`), the concept rules, the licence deny
  list, and every request `pdm run acceptance-record` records.
- `recorded/<surface>/`: captured from the live service by `acceptance-record`; concepts in
  `recorded/evs/concepts/`.
- `crafted/<requirement>/`: hand-written.
- `scenarios/<group>/<name>/`: the fixtures of one scenario, which answer before the ordinary
  ones while a test selects it (`@pytest.mark.scenario("<group>/<name>")`). A `settings.json`
  there holds the `NCI_SI_*` settings the scenario's server process starts with.
- `baseline_toolmap.yaml`: the stand-ins for required tools the server lacks.

## Recording

`pdm run acceptance-record` re-records the set from live and writes nothing unless the live
monthly release is still the pinned one, no request names licensed content, the composed concept
answers equal real ones, and no file under `recorded/` is left over. The set grows with the
tests: a test that needs another upstream answer adds its request to the manifest and re-records.
Re-pinning the release is a re-recording under change control.

## Request forms

The fixture server answers a request exactly as recorded, with two kinds of exception:

- **Concepts.** `GET /api/v1/concept/{terminology}/{code}` and the batch
  `GET /api/v1/concept/{terminology}?list=` are answered in every projection (`include`) and
  every batch the recordings cover, by the rules of `src/nci_si_acceptance/concepts.py`. A batch
  comes back in no particular order: EVS keeps none either.
- **Ignored parameters.** A parameter the live service is shown to ignore is declared with the
  evidence and left out of the match (FHIR `$expand` ignores `count`, `offset` and `activeOnly`).

Everywhere else the form in the manifest is the canonical one: the release-pinned path
(`ncit_26.09d`), the one-row release query (`terminology=ncit&latest=true&tag={channel}`), search
with `term`, `type`, `include=minimal,highlights`, `fromRecord` and `pageSize` all given. A
request in another form finds no fixture, and the test that sent it reports NO FIXTURE.

## Content and terms

The recorded EVS content is excerpted from NCI Thesaurus™ release 26.09d, retrieved from the EVS
REST API (`api-evsrest.nci.nih.gov`) on 2 October 2026. The NCI Thesaurus is produced by the
Enterprise Vocabulary Services group of the Center for Biomedical Informatics and Information
Technology, National Cancer Institute, and released under the Creative Commons Attribution 4.0
International licence ([terms of use](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/ThesaurusTermsofUse.htm)).
These fixtures are modified excerpts, not the NCI Thesaurus: licensed items are removed and each
removal is listed in the fixture's `redacted`. These terms apply to the fixture content; the
code of the suite is under the Apache License 2.0.

**Licensed content is never recorded** (EVS SOW v2.1 item 2). The manifest's deny list names the
terminologies whose EVS metadata requires a licence beyond EVS (MedDRA, SNOMED CT, ICD-10, the
NCI Metathesaurus) and the mapsets that carry them. A request naming one is not recorded unless
EVS refused it, and an item of a payload that comes from or maps to one is removed. ICD-O-3
(WHO) is withheld as well until its terms are settled. `selftests/test_fixture_set.py` fails on
any licensed item left in the set.
