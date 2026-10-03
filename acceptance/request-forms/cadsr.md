# Upstream request forms, for the caDSR team

Generated from `acceptance/fixtures/manifest.yaml` by `pdm run acceptance-register`; do not edit by hand.

The upstream requests the acceptance suite's fixtures answer, each with the platform operation it
serves (its `OP-` id in the programme's operation inventory, or the requirement where the
operation is missing) and why
it has this form. They are initial versions, from the published API documentation and live checks
where the operation exists and a draft where it does not, furnished for the EVS, caDSR and Shared SI
teams to refine as needed, each change with the approval of the branch chief or a delegate.

Two kinds of request are answered whatever their form: EVS concept requests, by rules over one
recording per concept (below), and requests whose parameters the service is shown to ignore.

## caDSR: no registry release, and JSON only when asked for

caDSR publishes no registry release (C-1, A3.8.1): every caDSR form below is served without one,
the path the inventory names for it (OP-C08) answers 404, and the export's date is the registry's
only content state (A3.8.2). The forms the inventory names with `registryRelease` are crafted in
`cadsr/with-registry-release`. Every caDSR request names `Accept: application/json`, which the
contracts prescribe (M3.2): without it the API answers HTTP 200 with HTML, recorded for two paths,
and the fixture naming the most headers a request carries answers it. A refusal of the arguments
and an unknown data element both come back as HTTP 200, with `apiResponse` saying so (X-15).

## Requests

| Operation | Request | Expected | Made | Rationale | Fixture |
|---|---|---|---|---|---|
| OP-C01 | `GET cadsr /NCIAPI/1.0/api/DataElement/2200604` with `Accept: application/json` | 200 | recorded | A data element, its latest version; its permissible values are in ValueDomain.PermissibleValues, the only form of OP-C07 today (its own path answers 404). The inventory's form is /DataElement?publicId=&version=&registryRelease=. | `recorded/cadsr/data-element-2200604.json` |
| OP-C01 | `GET cadsr /NCIAPI/1.0/api/DataElement/2200604?version=1` with `Accept: application/json` | 200 | recorded | The same data element at version 1, which carries other names. | `recorded/cadsr/data-element-2200604-version-1.json` |
| OP-C01 | `GET cadsr /NCIAPI/1.0/api/DataElement/99999999` with `Accept: application/json` | 200 | recorded | An unknown public id: HTTP 200 with DataElement null and apiResponse type I, "No data returned for the input criteria" (X-15, not_found). | `recorded/cadsr/data-element-unknown.json` |
| OP-C01 | `GET cadsr /NCIAPI/1.0/api/DataElement/notanumber` with `Accept: application/json` | 200 | recorded | A public id that is no number: HTTP 200 with DataElement null and apiResponse type E, "Parameter 'publicId' must be a number." (X-15, invalid_request). | `recorded/cadsr/data-element-refused.json` |
| OP-C07 | `GET cadsr /NCIAPI/1.0/api/DataElement/2200604/permissibleValues` with `Accept: application/json` | 404 | recorded | The inventory's path for a data element's permissible values answers 404; they come inside the data element. | `recorded/cadsr/permissible-values.json` |
| OP-C01 | `GET cadsr /NCIAPI/1.0/api/DataElements/ReferenceDocument?documentText=Sex of a Person&documentType=Preferred Question Text&headerOnly=true` with `Accept: application/json` | 200 | recorded | A data element by its preferred question text, header fields only; the platform matches the text as contained, and this one names 2200604 alone. | `recorded/cadsr/question-text-sex-of-a-person.json` |
| OP-C01 | `GET cadsr /NCIAPI/1.0/api/DataElements/ReferenceDocument?documentText=Date of birth&documentType=Preferred Question Text&headerOnly=true` with `Accept: application/json` | 200 | recorded | A question text 18 data elements have, as the platform matches it. | `recorded/cadsr/question-text-date-of-birth.json` |
| OP-C01 | `GET cadsr /NCIAPI/1.0/api/DataElements/ReferenceDocument?documentText=qqxyzzyqq&documentType=Preferred Question Text&headerOnly=true` with `Accept: application/json` | 200 | recorded | A question text no data element has, which the platform answers with an empty list. | `recorded/cadsr/question-text-unmatched.json` |
| OP-C01 | `GET cadsr /NCIAPI/1.0/api/DataElement/2200604` with no header | 200 | recorded | The same request without Accept: HTTP 200 with HTML. The fixture naming Accept answers a request that carries it; this one any other (M3.2, X-15). | `recorded/cadsr/data-element-2200604-html.json` |
| OP-C06 | `GET cadsr /NCIAPI/1.0/api/DataElements/getCRDCList` with `Accept: application/json` | 200 | recorded | The CRDC crosswalk, unparameterised. | `recorded/cadsr/crdc-list.json` |
| OP-C09 | `GET cadsr /NCIAPI/1.0/api/DataElements/Concept?conceptCode=C17357&headerOnly=true` with `Accept: application/json` | 200 | recorded | Data elements by concept code, header fields only: the contract caps every query at 1,000 results, "To bring back all results, utilize the headerOnly field". | `recorded/cadsr/concept-c17357.json` |
| OP-C09 | `GET cadsr /NCIAPI/1.0/api/DataElements/Concept?conceptCode=C17357&headerOnly=true` with no header | 200 | recorded | The same request without Accept, answered with HTML (M3.2, X-15). | `recorded/cadsr/concept-c17357-html.json` |
| C-11 | `GET cadsr /NCIAPI/1.0/api/DataElements/Classification?classificationSchemePublicId=3685569&classificationSchemeVersion=1&headerOnly=true` with `Accept: application/json` | 200 | recorded | The data elements of one classification scheme, header fields only. OP-C13, the list of schemes, has no REST form: schemes come only nested in a data element and as this filter (C-11). The scheme is named by classificationSchemePublicId (publicId answers an empty list). 3685569 (Newborn Examination, NICHD) holds 40, 2200604 among them; 2008601, another of 2200604's schemes, answered 4 MB in 149 s. | `recorded/cadsr/classification-3685569.json` |
| OP-C13 | `GET cadsr /NCILovAPI/1.0/api/getContextNames` with `Accept: application/json` | 401 | recorded | The context list on the lists-of-values API refuses an anonymous caller (401); cadsr/credentialed holds the answer to the contract. | `recorded/cadsr/context-names-refused.json` |
| OP-C12 | `GET cadsr /NCIFormAPI.v2_0:NciFormApiRad/Form/5406471` with `Accept: application/json` | 200 | recorded | A form by public id on the Form 2.0 API, the contract's own example; the Form 1.0 API refuses the same form anonymously (401). The form is RETIRED ARCHIVED (context ONC, retired 8 April 2025), so it serves get_form's status surfaced (A8.1); it is the only form, since no released one could be found anonymously: Form/query takes a public or protocol id only, and the caDSR portal (OneData) needs a login. | `recorded/cadsr/form-5406471.json` |
| OP-C12 | `GET cadsr /NCIFormAPI.v2_0:NciFormApiRad/Form/99999999` with `Accept: application/json` | 200 | recorded | An unknown form id: HTTP 200 with form null and apiResponse type E, "No data found for input criteria." (X-15, not_found). | `recorded/cadsr/form-unknown.json` |
| OP-C10 | `GET cadsr /NCIAPI/1.0/api/permissibleValues/9192925` with `Accept: application/json` | 404 | recorded | A permissible value by the id caDSR REST publishes for it (9192925, "Unknown" of 2200604): the inventory's path answers 404. | `recorded/cadsr/permissible-value-9192925.json` |
| OP-M02 | `POST cadsr /vmMatch/v1/vmMatch` with `Accept: application/json`, `Content-Type: application/json`, `matchType: Restricted`, `function: match`, body `[{"name": "Male"}]` | 200 | recorded | Value meanings matched to one value, restricted; answered anonymously. | `recorded/cadsr/vm-match-male.json` |
| OP-M01 | `POST cadsr /NCIAPI.v2_0.cdeMatch.api:cdeMatch_rad/cdeMatch` with `Accept: application/json`, `Content-Type: application/json`, body `[{"entity": "Patient Gender"}]` | 401 | recorded | CDE Match refuses an anonymous caller (401) since 3 October 2026 at the latest; it answered one on 10 September. cadsr/credentialed holds the answer to the contract. | `recorded/cadsr/cde-match-refused.json` |
| OP-M01 | `POST cadsr /NCIAPI.v2_0.cdeMatch.api:cdeMatch_rad/cdeMatch` with `Accept: application/json`, `Content-Type: application/json`, body `{"entity": "Patient Gender"}` | 401 | recorded | CDE Match asked with the contract's body, one apiinput object, refused alike (401). Which form the service takes, the contract's object or the array it answered on 10 September, is asked in #42; the fixtures answer both. | `recorded/cadsr/cde-match-refused-object.json` |
| OP-C08 | `GET cadsr /NCIAPI/1.0/api/registry/releases` with `Accept: application/json` | 404 | recorded | No registry release is published: the path the inventory names (/registry/releases, placed here under the data element API) answers 404 (C-1). cadsr/with-registry-release crafts one. | `recorded/cadsr/registry-releases.json` |
| A3.8.2 | `GET cadsr-ftp /CDE/XML/` with no header | 200 | recorded | The folder of the XML export, which dates releasedCDEsXML-OD.zip 2026-07-01 22:19 in the server's time (Last-Modified: Thu, 02 Jul 2026 02:19:40 GMT); its README says it is updated daily. The inventory's timestamped xml_cde_YYYYMMDDHHMM.zip names are gone. | `recorded/cadsr-ftp/cde-xml-listing.json` |
| A3.8.2 | `GET cadsr-ftp /CDE/XML/README` with no header | 200 | recorded | What the export holds and how often it is updated. | `recorded/cadsr-ftp/cde-xml-readme.json` |

## Scenarios

Each scenario provokes one case; its fixtures answer before the ordinary
ones while a test selects it. A recorded fixture is what the service answers today. A crafted one
stands in for a case the service does not produce on demand, under the requirement it names, and
answers the ordinary forms above.

### `upstream/unavailable`

Every upstream request, whatever its surface and path, gets a closed connection, then 503, then no answer within the timeout. Crafted, 7 fixtures, for A2.5, A5.3: bounded retries, counted, then a structured error.

### `upstream/rate-limited`

429 with `Retry-After` on the release query, then the answer. Crafted, 2 fixtures, for E-7, P-1: back-off honoured and counted.

### `cadsr/with-registry-release`

caDSR publishes a registry release: /registry/releases names one, and a data element asked with it is answered with the release echoed (C-1). Crafted, 3 fixtures, for C-1: a published registry release, named in every answer and accepted on every content call.

### `cadsr/credentialed`

The server holds caDSR credentials: contexts and CDE Match answer to the contracts, where the API refuses an anonymous caller (401). Crafted, 1 fixture, for OP-M01, A9.3: CDE Match to its 2.0 contract, which refuses an anonymous caller since 3 October 2026 at the latest (401, recorded/cadsr/cde-match-refused*.json); the array the call of 10 September sent. Crafted, 1 fixture, for OP-M01, A9.3: CDE Match to its 2.0 contract for a second entity; the array the call of 10 September sent. Crafted, 1 fixture, for OP-M01, A9.3: CDE Match to its 2.0 contract for a second entity. Crafted, 1 fixture, for OP-M01, A9.3: CDE Match to its 2.0 contract, which refuses an anonymous caller since 3 October 2026 at the latest (401, recorded/cadsr/cde-match-refused*.json). Crafted, 1 fixture, for OP-C13, A9.3: the context list to the lists-of-values contract, which refuses an anonymous caller (401, recorded/cadsr/context-names-refused.json).

### `cadsr/match-timeout`

vmMatch answers later than the match timeout the scenario sets. Crafted, 1 fixture, for A2.5: matching slower than its declared timeout is a timeout error; the array the call of 10 September sent. Crafted, 2 fixtures, for A2.5: matching slower than its declared timeout is a timeout error.

### `cadsr/html-for-json`

A data element asked for with Accept: application/json is answered with the HTML caDSR sends to a request without it. Crafted, 1 fixture, for X-15: HTML where JSON was asked for is an upstream error.

### `upstream/masked-error`

Every request to the Shared SI façade and to the caDSR API is answered with HTTP 200 and an apiResponse of type E, the failure each sends inside a success. Crafted, 2 fixtures, for X-15: an error envelope in an HTTP 200 is an upstream error.
