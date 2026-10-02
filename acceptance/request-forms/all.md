# Upstream request forms, for both teams

Generated from `acceptance/fixtures/manifest.yaml` by `pdm run acceptance-register`; do not edit by hand.

The upstream requests the acceptance suite's fixtures answer, each with the platform operation it
serves (*MCP API Specification* §10, or the requirement where the operation is missing) and why
it has this form. They are initial forms furnished by the government: from the published API
documentation and live checks where the operation exists, a draft where it does not. They are
open to refinement by the service teams and to proposals from the contractors.

Two kinds of request are answered whatever their form: EVS concept requests, by rules over one
recording per concept (below), and requests whose parameters the service is shown to ignore.

## Requests

| Operation | Request | Expected | Rationale | Fixture |
|---|---|---|---|---|
| baseline | `GET evs /api/v1/version` | 200 | The furnished server's ncit_release_info reads it; no required tool needs it. | `recorded/evs/version.json` |
| OP-E01 | `GET evs /api/v1/metadata/terminologies` | 200 | The listing behind list_terminologies, and what the furnished server filters today. | `recorded/evs/terminologies.json` |
| OP-E01 | `GET evs /api/v1/metadata/terminologies?terminology=ncit&latest=true&tag=monthly` | 200 | The one-row release query: latest is channel-scoped, so the channel is given as a tag (E-1). | `recorded/evs/release-monthly.json` |
| OP-E01 | `GET evs /api/v1/metadata/terminologies?terminology=ncit&latest=true&tag=weekly` | 200 | The same query for the weekly channel. | `recorded/evs/release-weekly.json` |
| OP-E02 | `GET evs /api/v1/metadata/ncit_26.09d/roles` | 200 | The role catalogue of the pinned release, for polarity by code. | `recorded/evs/roles.json` |
| OP-E03 | `GET evs /api/v1/metadata/ncit_26.09d/associations` | 200 | The association catalogue of the pinned release. | `recorded/evs/associations.json` |
| OP-E08 | `GET evs /api/v1/concept/ncit_26.09d/search?term=ewing sarcoma&type=contains&include=minimal,highlights&fromRecord=0&pageSize=10` | 200 | Lexical search: type=contains, minimal concepts with highlights for matchedOn, the page given in full. | `recorded/evs/search-contains.json` |
| OP-E08 | `GET evs /api/v1/concept/ncit_26.09d/search?term=ewing sarcoma&type=contains&include=minimal,highlights&fromRecord=10&pageSize=10` | 200 | The second page of the same search. | `recorded/evs/search-contains-page-2.json` |
| OP-E08 | `GET evs /api/v1/concept/ncit_26.09d/search?term=ewing&type=startsWith&include=minimal,highlights&fromRecord=0&pageSize=10` | 200 | Typeahead: type=startsWith. | `recorded/evs/search-starts-with.json` |
| OP-E08 | `GET evs /api/v1/concept/ncit_26.09d/search?term=qqxyzzyqq&type=contains&include=minimal,highlights&fromRecord=0&pageSize=10` | 200 | A search matching nothing: empty is not an error. | `recorded/evs/search-no-match.json` |
| OP-E13 | `GET evs /api/v1/concept/ncit_26.09d/C4817/pathsToRoot?include=minimal` | 200 | EVS serves pathsToRoot; §10 spells it paths-to-root, which EVS answers 404. | `recorded/evs/paths-to-root.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/C4817/replacements` | 200 | An active code's replacements, one code. | `recorded/evs/replacement-active.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/replacements?list=C4817` | 200 | The ?list= batch form, which fails the whole batch on one unknown code. | `recorded/evs/replacements-active.json` |
| OP-E06 | `GET evs /api/v1/subset/ncit/C177537` | 200 | The subset by its unpinned path: the pinned path answers 404 today; the payload names its release. | `recorded/evs/subset-gdc-unpinned.json` |
| OP-E19 | `GET evs /api/v1/subset/ncit_26.09d/C177537/members?fromRecord=0&pageSize=10&include=minimal` | 200 | The members of a subset; EVS keys them by the subset's code, unlike §10's form. | `recorded/evs/subset-gdc-members.json` |
| Acceptance Suite §5.1 | `GET evs /api/v1/mapset` | 200 | Mapsets as first-class objects; not in §10. Names and metadata only for licensed ones. | `recorded/evs/mapsets.json` |
| Acceptance Suite §5.1 | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC` | 200 | The GDC mapset, versioned by the NCIt release. | `recorded/evs/mapset-gdc.json` |
| Acceptance Suite §5.1 | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC/maps?term=Ewing sarcoma&fromRecord=0&pageSize=10` | 200 | GDC maps found by a GDC value (resolve_stored_value); term matches as a prefix. | `recorded/evs/mapset-gdc-maps-value.json` |
| Acceptance Suite §5.1 | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC/maps?term=C4817&fromRecord=0&pageSize=10` | 200 | GDC maps found by an NCIt code. | `recorded/evs/mapset-gdc-maps-code.json` |
| OP-F05 | `GET evs-fhir /ValueSet/$expand?url=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl?fhir_vs=C85492` | 200; ignores activeOnly, count, offset | The unpinned expansion: system-version answers 400 today; count, offset and activeOnly are ignored. | `recorded/evs-fhir/expand-c85492.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817?include=summary` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/concept.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z?list=C4817&include=summary` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/concepts.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/parents` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/parents.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/children` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/children.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/roles` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/roles.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/inverseRoles` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/inverse-roles.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/associations` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/associations.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/inverseAssociations` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/inverse-associations.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/maps` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/maps.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/search?term=ewing sarcoma&type=contains&include=minimal,highlights&fromRecord=0&pageSize=10` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/search.json` |
| OP-E06 | `GET evs /api/v1/concept/ncit_99.99z/C4817/pathsToRoot?include=minimal` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/paths-to-root.json` |
| OP-E06 | `GET evs /api/v1/metadata/ncit_99.99z/roles` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/role-catalogue.json` |
| OP-E06 | `GET evs /api/v1/metadata/ncit_99.99z/associations` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/association-catalogue.json` |
| OP-E06 | `GET evs /api/v1/history/ncit_99.99z/C154421/replacements` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/replacement.json` |
| OP-E06 | `GET evs /api/v1/history/ncit_99.99z/replacements?list=C154421` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/replacements.json` |
| OP-E06 | `GET evs /api/v1/subset/ncit_99.99z/C177537` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/subset.json` |
| OP-E06 | `GET evs /api/v1/subset/ncit_99.99z/C177537/members?fromRecord=0&pageSize=10&include=minimal` | 404; every parameter ignored | An unknown release answers 404 on every content path: the server fails closed. | `scenarios/release/unknown/subset-members.json` |
| OP-E07 | `GET evs /api/v1/concept/ncit_26.09d/CBOGUS999999?include=full` | 404 | A code EVS does not know: a batch leaves it out without a signal (E-5). | `scenarios/batch/silent-drop/concepts/CBOGUS999999.json` |
| OP-E05 | `GET evs /api/v1/concept/ncit_26.09d/C154421?include=full` | 200 | A retired concept (A8). | `scenarios/retired/with-replacement/concepts/C154421.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/C154421/replacements` | 200 | A retired code with its replacement. | `scenarios/retired/with-replacement/replacement.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/replacements?list=C154421` | 200 | The batch form for the retired code. | `scenarios/retired/with-replacement/replacements.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/replacements?list=C154421,C4817` | 200 | A batch of a retired and an active code, answered per code. | `scenarios/retired/with-replacement/replacements-with-active.json` |
| OP-E21 | `GET evs /api/v1/history/ncit_26.09d/replacements?list=C154421,CBOGUS999999` | 404 | One unknown code fails the whole batch (404). | `scenarios/retired/with-replacement/replacements-with-unknown.json` |
| SOW v2 §6-§7 | `GET evs /api/v1/concept/mdr_29_0/10000000?include=summary` | 403; every parameter ignored | EVS refuses mdr without the X-EVSRESTAPI-License-Key header (403). | `scenarios/license/restricted/refused.json` |

## Concept requests answered by rule

One recording per concept answers each of these in every projection (`include`), batch and
relation list the recordings cover (`acceptance/src/nci_si_acceptance/concepts.py`).

| Operations | Form |
|---|---|
| OP-E05 | `GET /api/v1/concept/{terminology}_{release}/{code}?include=…` |
| OP-E07 | `GET /api/v1/concept/{terminology}_{release}?list=…&include=…`, ≤ 1,000 codes |
| OP-E10, E11, E14 to E17, E20 | `GET /api/v1/concept/{terminology}_{release}/{code}/{relation}` |

## Operations without a pinned form upstream

These are served unpinned today. The verified fallback is approved for the prototype: the tool
calls the unpinned form, compares the release the payload reports with the one requested, and
fails closed on a difference. NCI's approval under EVS SOW v2.1 item 5 is taken from this list. A
mapset that reports a version of its own, not an NCIt release, cannot be verified that way: it is a
content state of its own, named in provenance and not presented as release-verified.

| Operation | Prescribed form (crafted) | Served form (recorded) | Release reported |
|---|---|---|---|
| OP-E06 | `GET evs /api/v1/subset/ncit_26.09d/C177537` | `GET evs /api/v1/subset/ncit/C177537` | 26.09d |
| Acceptance Suite §5.1 | — | `GET evs /api/v1/mapset` | 2016_07_31, 2017-12-21, 2026_03_01, 26.09d, February2020, July2023, June2026, November2011, September2026 |
| Acceptance Suite §5.1 | — | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC` | 26.09d |
| Acceptance Suite §5.1 | — | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC/maps?term=Ewing sarcoma&fromRecord=0&pageSize=10` | 26.09d |
| Acceptance Suite §5.1 | — | `GET evs /api/v1/mapset/NCIt_Maps_To_GDC/maps?term=C4817&fromRecord=0&pageSize=10` | 26.09d |
| OP-F05 | `GET evs-fhir /ValueSet/$expand?url=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl?fhir_vs=C85492&system-version=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl\|26.09d` | `GET evs-fhir /ValueSet/$expand?url=http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl?fhir_vs=C85492` | 26.09d |
