# Upstream request forms, for the EVS team

Generated from `acceptance/fixtures/manifest.yaml` by `pdm run acceptance-register`; do not edit by hand.

The upstream requests the acceptance suite's fixtures answer, each with the platform operation it
serves (its `OP-` id in the programme's operation inventory, or the requirement where the
operation is missing) and why
it has this form. They are initial versions, from the published API documentation and live checks
where the operation exists and a draft where it does not, furnished for the EVS and caDSR teams to
refine as needed, each change with the approval of the branch chief or a delegate.

Two kinds of request are answered whatever their form: EVS concept requests, by rules over one
recording per concept (below), and requests whose parameters the service is shown to ignore.

## Operations without a pinned form upstream

These are served unpinned today. The verified fallback is approved for the prototype: the tool
calls the unpinned form, compares the release the payload reports with the one requested, and
fails closed on a difference. NCI's approval under EVS SOW v2.1 item 5 is taken from this list. A
mapset that reports a version of its own, not an NCIt release, cannot be verified that way: it is a
content state of its own, named in provenance and not presented as release-verified.

| Operation | Prescribed form (crafted) | Served form (recorded) | Release reported |
|---|---|---|---|
| OP-E06 | `GET evs /api/v1/subset/ncit_26.09d/C177537` | `GET evs /api/v1/subset/ncit/C177537` | 26.09d |
| OP-E23 | — | `GET evs /api/v1/mapset?include=minimal` | 2016_07_31, 2017-12-21, 2026_03_01, 26.09d, February2020, July2023, June2026, November2011, September2026 |
| OP-E24 | — | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC` | 26.09d |
| OP-E25 | — | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC/maps?term=Ewing sarcoma&fromRecord=0&pageSize=10` | 26.09d |
| OP-E25 | — | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC/maps?term=C4817&fromRecord=0&pageSize=10` | 26.09d |
| OP-F05 | `GET evs-fhir /ValueSet/$expand?url=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl?fhir_vs=C85492&system-version=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl\|26.09d` | `GET evs-fhir /ValueSet/$expand?url=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl?fhir_vs=C85492` | 26.09d |

## Requests

| Operation | Request | Expected | Made | Rationale | Fixture |
|---|---|---|---|---|---|
| baseline | `GET evs /api/v1/version` | 200 | recorded | The furnished server's ncit_release_info reads it; no required tool needs it. | `recorded/evs/version.json` |
| OP-E01 | `GET evs /api/v1/metadata/terminologies` | 200 | recorded | The listing behind list_terminologies, and what the furnished server filters today. | `recorded/evs/terminologies.json` |
| OP-E01 | `GET evs /api/v1/metadata/terminologies?terminology=ncit&latest=true&tag=monthly` | 200 | recorded | The one-row release query: latest is channel-scoped, so the channel is given as a tag (E-1). | `recorded/evs/release-monthly.json` |
| OP-E01 | `GET evs /api/v1/metadata/terminologies?terminology=ncit&latest=true&tag=weekly` | 200 | recorded | The same query for the weekly channel. | `recorded/evs/release-weekly.json` |
| OP-E02 | `GET evs /api/v1/metadata/ncit_26.09d/roles` | 200 | recorded | The role catalogue of the pinned release, for polarity by code. | `recorded/evs/roles.json` |
| OP-E03 | `GET evs /api/v1/metadata/ncit_26.09d/associations` | 200 | recorded | The association catalogue of the pinned release. | `recorded/evs/associations.json` |
| OP-E08 | `GET evs /api/v1/concept/ncit_26.09d/search?term=ewing sarcoma&type=contains&include=minimal,highlights&fromRecord=0&pageSize=10` | 200 | recorded | Lexical search: type=contains, minimal concepts with highlights for matchedOn, the page given in full. | `recorded/evs/search-contains.json` |
| OP-E08 | `GET evs /api/v1/concept/ncit_26.09d/search?term=ewing sarcoma&type=contains&include=minimal,highlights&fromRecord=10&pageSize=10` | 200 | recorded | The second page of the same search. | `recorded/evs/search-contains-page-2.json` |
| OP-E08 | `GET evs /api/v1/concept/ncit_26.09d/search?term=ewing&type=startsWith&include=minimal,highlights&fromRecord=0&pageSize=10` | 200 | recorded | Typeahead: type=startsWith. | `recorded/evs/search-starts-with.json` |
| OP-E08 | `GET evs /api/v1/concept/ncit_26.09d/search?term=qqxyzzyqq&type=contains&include=minimal,highlights&fromRecord=0&pageSize=10` | 200 | recorded | A search matching nothing: empty is not an error. | `recorded/evs/search-no-match.json` |
| OP-E13 | `GET evs /api/v1/concept/ncit_26.09d/C4817/pathsToRoot?include=minimal` | 200 | recorded | The paths from the concept to the root of the hierarchy. | `recorded/evs/paths-to-root.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/C4817/replacements` | 200 | recorded | An active code's replacements, one code. | `recorded/evs/replacement-active.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/replacements?list=C4817` | 200 | recorded | The ?list= batch form, which fails the whole batch on one unknown code. | `recorded/evs/replacements-active.json` |
| OP-E06 | `GET evs /api/v1/subset/ncit/C177537` | 200 | recorded | The subset by its unpinned path: the pinned path answers 404 today; the payload names its release. | `recorded/evs/subset-gdc-unpinned.json` |
| OP-E06 | `GET evs /api/v1/subset/ncit_26.09d/C177537` | 200 | crafted for OP-E06: every content path accepts {terminology}_{release}. EVS answered this pinned path with 404 "Subset not found" on 2 October 2026 | The pinned subset path a requirement prescribes, carrying the unpinned answer. | `crafted/OP-E06/subset-gdc.json` |
| OP-E19 | `GET evs /api/v1/concept/ncit_26.09d/subsetMembers/C177537?fromRecord=0&pageSize=10&include=minimal` | 200 | recorded | The first page of a subset's members, keyed by the subset's code. | `recorded/evs/subset-gdc-members.json` |
| OP-E23 | `GET evs /api/v1/mapset?include=minimal` | 200 | recorded | Mapsets as first-class objects, each with its own version. | `recorded/evs/mapsets.json` |
| OP-E24 | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC` | 200 | recorded | The GDC mapset, versioned by the NCIt release. | `recorded/evs/mapset-gdc.json` |
| OP-E25 | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC/maps?term=Ewing sarcoma&fromRecord=0&pageSize=10` | 200 | recorded | GDC maps found by a GDC value (resolve_stored_value); term matches as a prefix. | `recorded/evs/mapset-gdc-maps-value.json` |
| OP-E25 | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC/maps?term=C4817&fromRecord=0&pageSize=10` | 200 | recorded | GDC maps found by an NCIt code. | `recorded/evs/mapset-gdc-maps-code.json` |
| OP-F05 | `GET evs-fhir /ValueSet/$expand?url=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl?fhir_vs=C85492` | 200; ignores activeOnly, count, offset | recorded | The unpinned expansion: system-version answers 400 today; count, offset and activeOnly are ignored. | `recorded/evs-fhir/expand-c85492.json` |
| OP-F05 | `GET evs-fhir /ValueSet/$expand?url=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl?fhir_vs=C85492&system-version=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl\|26.09d` | 200; ignores activeOnly, count, offset | crafted for OP-F05: $expand pinned by system-version. EVS answered "Input parameter 'system-version' is not supported" (400) on 2 October 2026 | $expand pinned by system-version, as the operation inventory prescribes (OP-F05), carrying the unpinned answer. | `crafted/OP-F05/expand-c85492.json` |

## Concept requests answered by rule

One recording per concept answers each projection (`include`) and relation list of that concept
it covers, and a batch is composed from the recordings of the concepts it names
(`acceptance/src/nci_si_acceptance/concepts.py`).

| Operations | Form |
|---|---|
| OP-E05 | `GET /api/v1/concept/{terminology}_{release}/{code}?include=…` |
| OP-E07 | `GET /api/v1/concept/{terminology}_{release}?list=…&include=…`, ≤ 1,000 codes |
| OP-E10, E11, E14 to E17, E20 | `GET /api/v1/concept/{terminology}_{release}/{code}/{relation}` |

## Scenarios

Each scenario provokes one case; its fixtures answer before the ordinary
ones while a test selects it. A recorded fixture is what the service answers today. A crafted one
stands in for a case the service does not produce on demand, under the requirement it names, and
answers the ordinary forms above.

### `release/unknown`

Every content path of `ncit_99.99z` answers 404. Recorded.

Each request: An unknown release answers 404 on every content path: the server fails closed.

| Operation | Request | Expected | Fixture |
|---|---|---|---|
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817?include=summary` | 404; every parameter ignored | `scenarios/release/unknown/concept.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z?list=C4817&include=summary` | 404; every parameter ignored | `scenarios/release/unknown/concepts.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/parents` | 404; every parameter ignored | `scenarios/release/unknown/parents.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/children` | 404; every parameter ignored | `scenarios/release/unknown/children.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/roles` | 404; every parameter ignored | `scenarios/release/unknown/roles.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/inverseRoles` | 404; every parameter ignored | `scenarios/release/unknown/inverse-roles.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/associations` | 404; every parameter ignored | `scenarios/release/unknown/associations.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/inverseAssociations` | 404; every parameter ignored | `scenarios/release/unknown/inverse-associations.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/maps` | 404; every parameter ignored | `scenarios/release/unknown/maps.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/search?term=ewing sarcoma&type=contains&include=minimal,highlights&fromRecord=0&pageSize=10` | 404; every parameter ignored | `scenarios/release/unknown/search.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/pathsToRoot?include=minimal` | 404; every parameter ignored | `scenarios/release/unknown/paths-to-root.json` |
| OP-E06 | `GET evs /api/v1/metadata/ncit_99.99z/roles` | 404; every parameter ignored | `scenarios/release/unknown/role-catalogue.json` |
| OP-E06 | `GET evs /api/v1/metadata/ncit_99.99z/associations` | 404; every parameter ignored | `scenarios/release/unknown/association-catalogue.json` |
| OP-E06 | `GET evs /api/v1/history/ncit_99.99z/C154421/replacements` | 404; every parameter ignored | `scenarios/release/unknown/replacement.json` |
| OP-E06 | `GET evs /api/v1/history/ncit_99.99z/replacements?list=C154421` | 404; every parameter ignored | `scenarios/release/unknown/replacements.json` |
| OP-E06 | `GET evs /api/v1/subset/ncit_99.99z/C177537` | 404; every parameter ignored | `scenarios/release/unknown/subset.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/subsetMembers/C177537?fromRecord=0&pageSize=10&include=minimal` | 404; every parameter ignored | `scenarios/release/unknown/subset-members.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/descendants?maxLevel=4` | 404; every parameter ignored | `scenarios/release/unknown/descendants.json` |
| OP-E06 | `GET evs /api/v1/metadata/ncit_99.99z/properties` | 404; every parameter ignored | `scenarios/release/unknown/property-catalogue.json` |

### `release/mismatch`

Every payload that reports the pinned release names 26.08e instead, the unpinned forms included; release discovery is left as recorded. Crafted, 106 fixtures, for A3.4: the content served names another release than the one requested.

### `release/two-latest`

A weekly and a monthly row are both `latest`, the weekly first in every list; each form of the release query gives a channel the same release. Crafted, 4 fixtures, for A3.6.1-A3.6.3: two releases carry latest at once, one per channel.

### `release/duplicate-tag`

Two releases are both latest with the monthly tag; the monthly query and the listing name both. Crafted, 2 fixtures, for A3.6.3: a release with duplicate tags fails closed.

### `batch/silent-drop`

A code EVS does not know is left out of a batch. Recorded.

| Operation | Request | Expected | Rationale | Fixture |
|---|---|---|---|---|
| OP-E07 | `GET evs /api/v1/concept/ncit_26.09d/CBOGUS999999?include=full` | 404 | A code EVS does not know: a batch leaves it out without a signal (E-5). | `scenarios/batch/silent-drop/concepts/CBOGUS999999.json` |

### `retired/with-replacement`

C154421 is retired with a replacement, and one bad code fails a batch. Recorded.

| Operation | Request | Expected | Rationale | Fixture |
|---|---|---|---|---|
| OP-E05 | `GET evs /api/v1/concept/ncit_26.09d/C154421?include=full` | 200 | A retired concept (A8). | `scenarios/retired/with-replacement/concepts/C154421.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/C154421/replacements` | 200 | A retired code with its replacement. | `scenarios/retired/with-replacement/replacement.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/replacements?list=C154421` | 200 | The batch form for the retired code. | `scenarios/retired/with-replacement/replacements.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/replacements?list=C154421,C4817` | 200 | A batch of a retired and an active code, answered per code. | `scenarios/retired/with-replacement/replacements-with-active.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/replacements?list=C154421,CBOGUS999999` | 404 | One unknown code fails the whole batch (404). | `scenarios/retired/with-replacement/replacements-with-unknown.json` |

### `traversal/deep-fanout`

One root has 1,001 children, and a chain runs deeper than depth 4 from its first child, C99000001 (the node maximum is reached at depth 1 from the root). Crafted, 1,007 fixtures, for A5.1-A5.4: descendants beyond the depth and node bounds.

### `traversal/exclusions`

Exclusion roles are named like positive ones, and two the other way. Crafted, 2 fixtures, for A5.6, A5.7, E-4: polarity by relationship code, not by name.

### `traversal/starvation`

One concept has 300 roles and 2 associations. Crafted, 303 fixtures, for A5.5: a budget per relationship kind; no kind starved.

### `upstream/unavailable`

Every EVS request, whatever its path, gets a closed connection, then 503, then no answer within the timeout. Crafted, 2 fixtures, for A2.5, A5.3: bounded retries, counted, then a structured error.

### `upstream/rate-limited`

429 with `Retry-After` on the release query, then the answer. Crafted, 1 fixture, for E-7, P-1: back-off honoured and counted.

### `license/restricted`

403 without the licence key; invented content with it. Recorded. Crafted, 1 fixture, for E-7, A7.5: the licence key sent from configuration.

| Operation | Request | Expected | Rationale | Fixture |
|---|---|---|---|---|
| A7.5 | `GET evs /api/v1/concept/mdr_29_0/10000000?include=summary` | 403; every parameter ignored | EVS refuses mdr without the X-EVSRESTAPI-License-Key header (403). | `scenarios/license/restricted/refused.json` |
