# Fixtures

The upstream responses the suite runs against in fixture mode (MCP Behavioral Acceptance Suite §2).
Each is a JSON file in the format `src/nci_si_acceptance/fixture_server.py` describes, and states
whether it was **recorded** from a live service (and when) or **crafted** for a case the live
service does not produce on demand (and which requirement it stands in for).

- `manifest.yaml`: the pinned release (NCIt `ncit_26.09d`), the concept rules, the licensing
  lists, and every request `pdm run acceptance-record` records, with its operation and rationale.
- `recorded/<surface>/`: captured from the live service by `acceptance-record`; concepts in
  `recorded/evs/concepts/`.
- `crafted/<requirement>/`: the form a requirement prescribes where EVS does not answer it yet,
  carrying a recording's answer (`acceptance-record`, the manifest's `derived`).
- `scenarios/<group>/<name>/`: the fixtures of one scenario, which answer before the ordinary
  ones while a test selects it (`@pytest.mark.scenario("<group>/<name>")`). A `settings.json`
  there holds the `NCI_SI_*` settings the scenario's server process starts with.
- `baseline_toolmap.yaml`: the stand-ins for required tools the server lacks.

## Scenarios

The scenario fixtures of *Acceptance Suite* §2.3, recorded from live where EVS produces the case
on demand (`acceptance-record`), crafted otherwise (`pdm run acceptance-craft`, from the recorded
set where they change a real answer):

| Scenario | Made | What it provokes |
|---|---|---|
| `release/unknown` | recorded | every content path of `ncit_99.99z` answers 404 |
| `release/mismatch` | crafted | content, including the unpinned forms, names release 26.08e |
| `release/two-latest` | crafted | a monthly and a weekly row both `latest` |
| `batch/silent-drop` | recorded | a code EVS does not know, left out of a batch |
| `retired/with-replacement` | recorded | C154421 retired with a replacement; one bad code fails a batch |
| `traversal/deep-fanout` | crafted | 1,001 children of one root, and a chain deeper than depth 4 |
| `traversal/exclusions` | crafted | exclusion roles named as positive ones, and two the other way |
| `traversal/starvation` | crafted | 300 roles and 2 associations on one concept |
| `upstream/unavailable` | crafted | closed connection, 503, then no answer within the timeout |
| `upstream/rate-limited` | crafted | 429 with `Retry-After` on the release query, then the answer |
| `license/restricted` | both | 403 without the licence key (recorded); invented content with it |

## Recording

After `pdm run acceptance-record`, run `pdm run acceptance-craft` and `pdm run
acceptance-register`: the self-tests check that crafted scenarios and the register are current.
`acceptance-record` re-records the set from live and writes nothing unless every check
holds: the live monthly release is still the pinned one; every request answers with the status
its entry expects; no request names licensed content and no licensed or undecided terminology is
left in a payload; every concept recording is one the fixture server accepts and the composed
concept answers equal real ones; every derived fixture comes from a recording of the pinned
release; and no file under `recorded/`, and no derived fixture, is left over. The set grows with
the tests: a test that needs another upstream answer adds its request to the manifest and
re-records. Re-pinning the release is a re-recording under change control.

## Request forms

The register of request forms, generated from the manifest, lists every request with the platform
operation it serves (*MCP API Specification* §10) and the reason for its form, in a view for the
EVS team, one for the caDSR team and one for both: [`../request-forms/`](../request-forms/). It
also lists the operations without a pinned form upstream, with the release each payload reports.
Where EVS does not yet answer the form a requirement prescribes, the ordinary fixture is crafted
to the requirement and names it, and the live run shows the gap (*Acceptance Suite* §2.1).

Two kinds of request are answered whatever their form:

- **Concepts.** One recording per concept answers every projection (`include`), every batch
  (`?list=`) and every relation list the recordings cover, by the rules of
  `src/nci_si_acceptance/concepts.py`. A batch comes back in no particular order: EVS keeps none.
- **Ignored parameters.** A parameter the live service is shown to ignore is declared with the
  evidence and left out of the match (FHIR `$expand` ignores `count`, `offset` and `activeOnly`).

A request in any other form finds no fixture, and the test that sent it reports NO FIXTURE.

## Content and terms

The recorded EVS content is excerpted from NCI Thesaurus™ release 26.09d, retrieved from the EVS
REST API (`api-evsrest.nci.nih.gov`) on 2 October 2026. The NCI Thesaurus is produced by the
Enterprise Vocabulary Services group of the Center for Biomedical Informatics and Information
Technology, National Cancer Institute, and released under the Creative Commons Attribution 4.0
International licence ([terms of use](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/ThesaurusTermsofUse.htm)).
These fixtures are modified excerpts, not the NCI Thesaurus: licensed items are removed and each
removal is listed in the fixture's `redacted`. These terms apply to the fixture content; the
code of the suite is under the Apache License 2.0.

**Licensed content is never recorded** (EVS SOW v2.1 item 2). The manifest's `licensing` section
names the terminologies whose EVS metadata requires a licence beyond EVS (MedDRA, SNOMED CT,
ICD-10, the NCI Metathesaurus), the mapsets that carry them, and the terminologies allowed. A
request naming a licensed one is not recorded unless EVS refused it, and an item of a payload
that comes from or maps to one is removed. ICD-O-3 (WHO) is withheld as well until its terms are
settled. Codes NCIt carries as its own properties (`ICD-O-3_Code`, `UMLS_CUI`, `NCI_META_CUI`)
are NCIt content and stay; only items that come from or map to a licensed terminology go. The
check fails closed: a terminology a payload names that is on neither list stops the recording
until someone decides it, and `selftests/test_fixture_set.py` fails on any licensed or
undecided name left in the set. A crafted fixture may answer for a licensed terminology only with
invented content it declares under `placeholder` (`license/restricted`).

Redaction makes a recording differ from live on purpose. A live-capable test therefore asserts
nothing about what redaction touches: not the count or the members of a concept's `maps`, nor
of any list an item was removed from.
