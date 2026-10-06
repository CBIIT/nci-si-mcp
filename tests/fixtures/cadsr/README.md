# caDSR client fixture sources

`tests/test_cadsr_client.py` crafts minimal protocol envelopes from these published JSON
contracts, inspected on 6 October 2026. Identifiers, names and empty matches in those tests
are synthetic, not registry records. They are only served by the offline test server.

The JSON documents are available from
`https://cadsrapi.cancer.gov/invoke/pub.swagger:getSwaggerJsonDoc?radName=` with these names:

| Surface | `radName` | Used shape |
|---|---|---|
| Data Elements | `NCIAPI.v1_0:NciApiRad` | DataElement, DataElements, CRDCDataElements wrappers |
| Forms | `NCIFormAPI.v2_0:NciFormApiRad` | form wrapper and version query |
| Contexts | `NCILovAPI.v1_0:LovRad` | contextNames string array |
| Models | `NCIModelAPI.v1_0:NciModelApiRad` | modelQueryResults and crosswalk data arrays |
| CDE Match | `NCIAPI.v2_0.cdeMatch.api:cdeMatch_rad` | one apiinput object, endpoint headers, matchResults object |
| VM Match | `NCIAPI.v1_0:vmMatchRad` | entities array, match headers, matchResults array |

The CDE Match and LOV contracts are also recorded in
`acceptance/fixtures/recorded/cadsr-contracts/`. API response properties are passed through;
these tests validate the envelope and wire request, not the whole later tool projection.

Keyword search is **requested OP-C03**, absent from the published contract. Its test envelope
follows `acceptance/fixtures/crafted/OP-C03/search-no-match.json`; it is not evidence of a live
search capability. A live type-E response stays an upstream error, with no alternate route.
The published-registry fixture is likewise a future capability from
`acceptance/fixtures/scenarios/cadsr/with-registry-release/registry-releases.json` (C-1).

The listing parser is tested against the existing recorded export-folder fixture, plus
crafted reordered columns, ambiguous links and invalid dates. It preserves the local date-time
without inventing a timezone. Credentials are generated test sentinels, never real credentials.
