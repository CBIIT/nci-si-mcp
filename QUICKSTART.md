# Quickstart

How to install and run the prototype server, what it serves, and how it fails. What this repository
is, and the status of each tool group, is in [README.md](README.md).

## Install

The project needs Python 3.14 or newer and is managed with [PDM](https://pdm-project.org).

```bash
pdm install
pdm run nci-si-mcp release-info
pdm run nci-si-mcp serve
```

`pdm install` creates `.venv` from `pdm.lock` and installs the package in editable mode with
the test and lint tools and the `server` extra (the `mcp` package, which the `serve` command
and the server tests need). The commands below are written as `python -m nci_si_mcp.cli ...`:
run them inside the environment (`eval $(pdm venv activate)`) or prefix them with `pdm run`.

To run a released version without a checkout, install it from its tag; the
[releases page](https://github.com/hniedner/nci-si-mcp/releases) lists the versions:

```bash
pip install "nci-si-mcp[server] @ git+https://github.com/hniedner/nci-si-mcp@vX.Y.Z"
nci-si-mcp serve
```

The package version is not written in any file. It is derived from the nearest `vX.Y.Z` git
tag when the package is installed or built; a commit after the tag gets a development version
such as `0.1.1.dev1+g<commit>`. Install from a git clone that has its tags: a clone without
tags silently gets `0.1.devN`, and a source archive without git metadata gets `0.0.0`. Run
`pdm install` again after a new tag to refresh the version.

For real embeddings, set both variables:

```bash
pdm install -G embeddings
export NCI_SI_EMBEDDING_PROVIDER=sentence-transformers
export NCI_SI_EMBEDDING_MODEL=cambridgeltl/SapBERT-from-PubMedBERT-fulltext
```

## Connect a client

An MCP client starts the server as a command. In a client that reads an `mcpServers`
configuration (Claude Desktop, for one), with absolute paths:

```json
{
  "mcpServers": {
    "nci-si": {
      "command": "/path/to/nci-si-mcp/.venv/bin/nci-si-mcp",
      "args": ["serve"],
      "env": {"NCI_SI_DATA_DIR": "/path/to/data"}
    }
  }
}
```

Resolve a release with `resolve_release`, then pass its version and terminology to
content tools such as `get_concept`. The examples below show the complete calls.

## Settings

The local data directory defaults to `.nci-si-mcp/`, relative to the working directory of the
process. An MCP client chooses that directory when it launches the server, so give the server an
absolute path:

```bash
export NCI_SI_DATA_DIR=/path/to/data
```

A leading `~` is expanded, and an empty value is rejected. The other settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `NCI_SI_PROFILE` | `unified` | `evs`, `cadsr` or `unified`. Selects twelve EVS tools, no caDSR tools yet, or the same twelve EVS tools, respectively; CLI maintenance commands and resources remain available |
| `NCI_SI_UPSTREAM_MODE` | `live` | `live` or `fixture`; selects the six base URLs below as a set (next paragraph) |
| `NCI_SI_EVS_BASE_URL` | `https://api-evsrest.nci.nih.gov` | EVS REST endpoint (`http` or `https`) |
| `NCI_SI_EVS_FHIR_BASE_URL` | `https://api-evsrest.nci.nih.gov/fhir/r4` | EVS FHIR endpoint |
| `NCI_SI_CADSR_BASE_URL` | `https://cadsrapi.cancer.gov/rad` | caDSR REST endpoint |
| `NCI_SI_CADSR_FTP_URL` | `https://cadsr.nci.nih.gov/ftp/caDSR_Downloads` | caDSR export (FTP) endpoint |
| `NCI_SI_SSIS_FACADE_URL` | `https://cadsrapi.cancer.gov` | Shared Semantic Infrastructure façade |
| `NCI_SI_SSIS_SPARQL_URL` | `https://shared.semantics.cancer.gov` | Shared Semantic Infrastructure SPARQL endpoint |
| `NCI_SI_RELEASE_CHANNEL` | `monthly` | The default channel for discovery and CLI diagnostics: `monthly` or `weekly`. The release is the one EVS row that is latest and tagged with the channel; with none or several the call fails with a release-not-available error. MCP content tools use the release the caller supplies |
| `NCI_SI_EXCLUSION_ROLE_CODES` | `R135,R136,R137,R138,R139,R140,R141,R142` | NCIt exclusion roles, a comma-separated list of codes (`R` and digits); checked against the requested release catalogue on each relationship listing or neighborhood call |
| `NCI_SI_EVS_LICENSE_KEY` | unset | EVS licence key, sent as the `X-EVSRESTAPI-License-Key` header on EVS requests and to no other host. A credential: never logged, in no error message or string form |
| `NCI_SI_CADSR_CREDENTIAL` | unset | caDSR credential as `user:password`; handled like the licence key |
| `NCI_SI_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| `NCI_SI_MATCH_TIMEOUT_SECONDS` | `45` | Timeout of a caDSR match request |
| `NCI_SI_EVS_MAX_ATTEMPTS` | `3` | Request attempts, 1 to 10. Only a 5xx, a 429 and a connection failure are retried; a 429 waits for its `Retry-After` (a platform that asks for more than 60 seconds is not asked again) |
| `NCI_SI_EVS_RETRY_BACKOFF_SECONDS` | `0.25` | Initial exponential backoff, jittered between half and all of it; a single wait is capped at 60 seconds |
| `NCI_SI_EVS_MAX_RESPONSE_BYTES` | `10485760` | Maximum accepted EVS REST or FHIR response, up to 1 GiB |
| `NCI_SI_INDEX_BATCH_SIZE` | `100` | Codes per EVS indexing request |
| `NCI_SI_LOG_LEVEL` | `INFO` | Stderr diagnostic level; per-call audit records remain enabled at every level |

The six base URLs are one set. In `live` mode a base URL that is not given takes its production
default, and one that is given replaces that default. In `fixture` mode every one of the six must be given, so a fixture
server cannot reach a production host by accident; a missing one stops startup, naming it. The
server adds the platform's own paths to each base URL. A setting set to an empty value is
rejected: unset it instead.

## Build a small local index

The full NCIt concept universe is large. Start with a known sample:

```bash
python -m nci_si_mcp.cli index-sample C3262 C2991 C40704 C153397 C116938
python -m nci_si_mcp.cli search "kinase inhibition"
python -m nci_si_mcp.cli evaluate
```

`index-sample` adds concepts to the index while the configured channel's release stays the
same. After a new release, the next `index-sample` activates a snapshot
with the concepts it names. The previous build remains available for rollback.
`search` only sees what has been indexed; no MCP
tool builds the index.

For a full release, `index-build` downloads and verifies all pinned NCIt search pages,
then returns an inactive build id. `index-builds` lists completed builds. Activate one
with `index-activate BUILD_ID`; activating the previous id rolls back. Every activation
keeps only the newly active build and the build it replaced. These are operator CLI commands.
The public manifest contains `terminology`, `version`, `concepts`, `embedding`
(`provider`, `model`, `dimensions`), `builtAt` and `provenance`.

Until the index is rebuilt after a new release, CLI `search` keeps serving
the old release (named in the `provenance.release` of each hit), and `lookup` fails with
`release_mismatch` for every code unless `--live-only` is given.
MCP `search_concepts` requires the caller's release to match the index; `get_concept`
reads the caller's pinned release directly from EVS.

No MCP result carries the full EVS `raw` payload, to keep MCP context compact. The
CLI adds it to `search` and `lookup` with `--include-raw`, for debugging:

```bash
python -m nci_si_mcp.cli lookup C3262 --include-raw
```

Traversal can be tested from the terminal before using MCP:

```bash
python -m nci_si_mcp.cli traverse C3262 \
  --max-depth 1 \
  --max-edges 100 \
  --edge-type role \
  --relationship-name Disease_Has_Abnormal_Cell
```

The index records its embedding provider, model, and dimensions. A runtime with
different embedding settings cannot search it or add to it. To rebuild with new
settings, use `index-rebuild BUILD_ID` to rebuild stored raw concepts offline, then
`index-activate NEW_BUILD_ID`. Schema migration preserves legacy concepts for cached lookup;
their concatenated vectors require this explicit rebuild before search is available
(`capability_unavailable`). Opening the index never downloads a model or rebuilds it.

## Usage examples

These results were captured from live EVS on 2026-10-05, pinned to release 26.09d.
The search uses the five-concept index built above. Resolve a release again before using
these calls against a later upstream release.

### Look up a concept

User prompt: "What is the NCI Thesaurus concept C4817?"

Call:

```json
{"tool": "get_concept", "arguments": {"terminology": "ncit", "release": "26.09d", "code": "C4817", "include": ["definitions", "semanticType"]}}
```

Result:

```json
{
  "code": "C4817",
  "terminology": "ncit",
  "name": "Ewing Sarcoma",
  "active": true,
  "provenance": {
    "release": {
      "terminology": "ncit",
      "identifier": "26.09d"
    },
    "source": "evs_rest",
    "servedBy": "live",
    "retrievedAt": "2026-10-05T11:59:15.999443Z",
    "correlationId": "be6137dd1cc142cbadb3bca24d3cde16",
    "sourceUri": "https://api-evsrest.nci.nih.gov/api/v1/concept/ncit_26.09d/C4817",
    "upstream": {
      "terminology": "ncit",
      "version": "26.09d"
    }
  },
  "status": "DEFAULT",
  "definitions": [
    {
      "definition": "A malignant neoplasm of the bone, or the soft tissue adjacent to bone, that is comprised of primitive neuroectodermal cells.",
      "code": "P325",
      "type": "ALT_DEFINITION",
      "source": "NICHD"
    },
    {
      "definition": "A small round cell tumor that lacks morphologic, immunohistochemical, and electron microscopic evidence of neuroectodermal differentiation. It represents one of the two ends of the spectrum called Ewing sarcoma/peripheral neuroectodermal tumor. It affects mostly males under age 20, and it can occur in soft tissue or bone. Pain and the presence of a mass are the most common clinical symptoms.",
      "code": "P97",
      "type": "DEFINITION",
      "source": "NCI"
    },
    {
      "definition": "A type of cancer that forms in bone or soft tissue.",
      "code": "P325",
      "type": "ALT_DEFINITION",
      "source": "NCI-GLOSS"
    }
  ],
  "semanticType": [
    "Neoplastic Process"
  ]
}
```

### Search the local index

User prompt: "Which indexed concept best matches kinase inhibition?"

Call:

```json
{"tool": "search_concepts", "arguments": {"terminology": "ncit", "release": "26.09d", "query": "kinase inhibition", "mode": "hybrid", "limit": 1}}
```

Result:

```json
{
  "results": [
    {
      "concept": {
        "code": "C40704",
        "terminology": "ncit",
        "name": "Receptor Tyrosine Kinase Inhibition",
        "active": true,
        "provenance": {
          "release": {
            "terminology": "ncit",
            "identifier": "26.09d",
            "date": "2026-09-28"
          },
          "source": "evs_index",
          "servedBy": "index",
          "retrievedAt": "2026-10-05T11:59:13.792416Z",
          "correlationId": "bce00f09f12d456bad1ab270f7f3b908",
          "sourceUri": "https://api-evsrest.nci.nih.gov/api/v1/concept/ncit_26.09d/C40704",
          "upstream": {
            "terminology": "ncit",
            "version": "26.09d"
          }
        },
        "status": "DEFAULT"
      },
      "score": 0.969251231258575
    }
  ],
  "truncation": {
    "occurred": true,
    "bound": "results",
    "limit": 1,
    "reached": 1,
    "omitted": 4,
    "exact": true
  }
}
```

### List the subtypes of a concept

User prompt: "Which concepts are the direct subtypes of Neoplasm?"

Call:

```json
{"tool": "get_concept_neighborhood", "arguments": {"terminology": "ncit", "release": "26.09d", "code": "C3262", "depth": 1, "kinds": ["child"]}}
```

Result:

```json
{
  "nodes": [
    {
      "code": "C3262",
      "terminology": "ncit",
      "name": "Neoplasm",
      "active": true,
      "provenance": {
        "release": {
          "terminology": "ncit",
          "identifier": "26.09d"
        },
        "source": "evs_rest",
        "servedBy": "live",
        "retrievedAt": "2026-10-05T11:59:16.016561Z",
        "correlationId": "7dc2d742beb84b8084b1ede2628a9579",
        "sourceUri": "https://api-evsrest.nci.nih.gov/api/v1/concept/ncit_26.09d/C3262",
        "upstream": {
          "terminology": "ncit",
          "version": "26.09d"
        },
        "depth": 0
      },
      "status": "DEFAULT"
    },
    {
      "code": "C4741",
      "terminology": "ncit",
      "name": "Neoplasm by Morphology",
      "active": true,
      "provenance": {
        "release": {
          "terminology": "ncit",
          "identifier": "26.09d"
        },
        "source": "evs_rest",
        "servedBy": "live",
        "retrievedAt": "2026-10-05T11:59:16.016561Z",
        "correlationId": "7dc2d742beb84b8084b1ede2628a9579",
        "sourceUri": "https://api-evsrest.nci.nih.gov/api/v1/concept/ncit_26.09d/C4741",
        "depth": 1,
        "relationship": {
          "kind": "child"
        },
        "direction": "in",
        "polarity": "positive",
        "upstream": {
          "terminology": "ncit",
          "version": "26.09d"
        }
      },
      "status": "Header_Concept"
    },
    {
      "code": "C3263",
      "terminology": "ncit",
      "name": "Neoplasm by Site",
      "active": true,
      "provenance": {
        "release": {
          "terminology": "ncit",
          "identifier": "26.09d"
        },
        "source": "evs_rest",
        "servedBy": "live",
        "retrievedAt": "2026-10-05T11:59:16.016561Z",
        "correlationId": "7dc2d742beb84b8084b1ede2628a9579",
        "sourceUri": "https://api-evsrest.nci.nih.gov/api/v1/concept/ncit_26.09d/C3263",
        "depth": 1,
        "relationship": {
          "kind": "child"
        },
        "direction": "in",
        "polarity": "positive",
        "upstream": {
          "terminology": "ncit",
          "version": "26.09d"
        }
      },
      "status": "Header_Concept"
    },
    {
      "code": "C7062",
      "terminology": "ncit",
      "name": "Neoplasm by Special Category",
      "active": true,
      "provenance": {
        "release": {
          "terminology": "ncit",
          "identifier": "26.09d"
        },
        "source": "evs_rest",
        "servedBy": "live",
        "retrievedAt": "2026-10-05T11:59:16.016561Z",
        "correlationId": "7dc2d742beb84b8084b1ede2628a9579",
        "sourceUri": "https://api-evsrest.nci.nih.gov/api/v1/concept/ncit_26.09d/C7062",
        "depth": 1,
        "relationship": {
          "kind": "child"
        },
        "direction": "in",
        "polarity": "positive",
        "upstream": {
          "terminology": "ncit",
          "version": "26.09d"
        }
      },
      "status": "Header_Concept"
    }
  ],
  "edges": [
    {
      "sourceCode": "C4741",
      "sourceTerminology": "ncit",
      "targetCode": "C3262",
      "targetTerminology": "ncit",
      "provenance": {
        "release": {
          "terminology": "ncit",
          "identifier": "26.09d"
        },
        "source": "evs_rest",
        "servedBy": "live",
        "retrievedAt": "2026-10-05T11:59:16.016561Z",
        "correlationId": "7dc2d742beb84b8084b1ede2628a9579",
        "sourceUri": "https://api-evsrest.nci.nih.gov/api/v1/concept/ncit_26.09d/C3262",
        "depth": 1,
        "relationship": {
          "kind": "child"
        },
        "direction": "in",
        "polarity": "positive"
      }
    },
    {
      "sourceCode": "C3263",
      "sourceTerminology": "ncit",
      "targetCode": "C3262",
      "targetTerminology": "ncit",
      "provenance": {
        "release": {
          "terminology": "ncit",
          "identifier": "26.09d"
        },
        "source": "evs_rest",
        "servedBy": "live",
        "retrievedAt": "2026-10-05T11:59:16.016561Z",
        "correlationId": "7dc2d742beb84b8084b1ede2628a9579",
        "sourceUri": "https://api-evsrest.nci.nih.gov/api/v1/concept/ncit_26.09d/C3262",
        "depth": 1,
        "relationship": {
          "kind": "child"
        },
        "direction": "in",
        "polarity": "positive"
      }
    },
    {
      "sourceCode": "C7062",
      "sourceTerminology": "ncit",
      "targetCode": "C3262",
      "targetTerminology": "ncit",
      "provenance": {
        "release": {
          "terminology": "ncit",
          "identifier": "26.09d"
        },
        "source": "evs_rest",
        "servedBy": "live",
        "retrievedAt": "2026-10-05T11:59:16.016561Z",
        "correlationId": "7dc2d742beb84b8084b1ede2628a9579",
        "sourceUri": "https://api-evsrest.nci.nih.gov/api/v1/concept/ncit_26.09d/C3262",
        "depth": 1,
        "relationship": {
          "kind": "child"
        },
        "direction": "in",
        "polarity": "positive"
      }
    }
  ],
  "truncation": {
    "occurred": true,
    "bound": "depth",
    "limit": 1,
    "reached": 1,
    "omitted": 56,
    "exact": false
  }
}
```

## Provenance and truncation

Every item a tool returns carries a `provenance` record (the specification's
`provenance` record, in camelCase). Items are a looked-up concept, each hit's concept of a
search, each node and edge of a traversal, the release report and the index manifest (which
`index-sample` and the release report's `active_index` also print, with the same record). A result
with no item (a search that finds nothing) carries the record itself; a result with items does
not. The same fields everywhere:

| Field | Value |
| --- | --- |
| `release` | `{terminology, identifier, date}` of the NCIt release the item was read from; `date` is left out when EVS gave none |
| `source` | `evs_rest` for live EVS, `evs_index` for the local index |
| `servedBy` | `live` or `index` |
| `retrievedAt` | When the item was retrieved; for an indexed concept, when it was indexed |
| `sourceUri` | The EVS URL of the resource that holds the item, without query: the concept, or for a descendant edge its start code's `descendants`. Left out of a search that found nothing, which no upstream URL produced |
| `correlationId` | The call's `_meta.correlationId`, or one the server generated; the same in every item of the call and in the error record |
| `upstream` | What EVS said of the item's origin, unchanged: its `terminology` and `version`. Present for a concept and for a traversal start code, whose payload was read in full; left out where EVS said nothing (a node named by a relation list, an edge) |
| `graphs`, `registry`, `attribution` | Never supplied: they belong to the Shared SI Service, to caDSR content and to answers that carry licence text |

An item reached by traversal adds `depth` (an edge has that of the node it reaches; the start
codes have 0); and, for any item but a start code, `relationship` (`kind`; for a role or
association also its `code` and `name`; a hierarchy link has only its kind: `parent`, `child` or
`descendant`), `direction` (`out` or `in`, the way the edge type is followed) and `polarity`
(`negative` for configured NCIt exclusion roles, R135 to R142 by default, otherwise
`positive`). Polarity follows the relationship code. Other terminologies have no exclusion
set today. A node carries the provenance of the edge that first reached it.

A tool that bounds its result returns `truncation`. It is `{"occurred": false}` when nothing
was cut. Otherwise it holds `bound` (`results`, `depth`, `nodes`, `edges`, `kind_budget`, `requests` or
`upstream_cap`), `limit`,
`reached`, `omitted` (always a number) and `exact` (false where `omitted` is a lower bound). A
traversal reports the first bound that dropped something; it counts the concepts or edges it
dropped, not those beyond them, so `exact` is false. `upstream_cap` is a concept whose relations
or descendants exceeded `NCI_SI_EVS_MAX_RESPONSE_BYTES`: `omitted` counts such concepts, and the
log names them. A search reports `results` when `limit` left scored concepts out; `exact` is true
where every candidate was scored.
`kind_budget` counts new nodes omitted by the first exhausted kind. `requests` counts
unread work as a lower bound; when the number of relations left out for a kind is
unknown, its record gives `omitted: 0` and `exact: false`.

```json
{"occurred": true, "bound": "nodes", "limit": 3, "reached": 3, "omitted": 2, "exact": false}
```

## MCP Tools

- `get_concept`: fetch a caller-pinned EVS concept with required `terminology`, `release` and `code`. Optional `include` selects synonyms, definitions, properties or semanticType; status is passed through from EVS.
- `resolve_retired_code`: fetch a required `terminology`, `release` and `code`, returning upstream `active` and optional `status`, with `replacements` always present. Active concepts return an empty list without a history request. Retired concepts use the pinned single-code history endpoint; each named replacement carries its code, name, terminology and provenance. A history 404 is an upstream error, not an empty result.
- `get_concept_subsets`: read the required `terminology`, `release` and `code`, returning its `Concept_In_Subset` associations as subset code, terminology, name and provenance, in platform order.
- `expand_value_set`: expand an NCIt subset with required `terminology`, `release` and exactly one of `valueSet` or `code`. `count` defaults to 200 and clamps to 1000; `offset` defaults to 0. `activeOnly` defaults to false; when true, inactive members are removed before paging and `total` counts those kept. Members retain platform order and FHIR provenance, with `inactive` only when true. Pages, including clamped and empty pages, are not truncation. EVS's unpinned expansion must report exactly the requested release or the call fails with `release_mismatch`; historical expansion is not guaranteed. Other terminologies return `capability_unavailable` without a request.
- `get_concept_mappings`: read the required `terminology`, `release` and `code`, returning its maps in platform order. Optional `targetTerminology` matches the platform label exactly, including case. Record values are unchanged; optional target version and term type are omitted when absent, null or empty. Both tools use one pinned concept read, carry provenance on empty results and fail on malformed content.
- `list_relationships`: list the pinned release’s roles and associations with code, terminology, name, kind, polarity and provenance. Reads each catalogue once per call; no cross-call cache. Missing configured exclusion codes make this tool and `get_concept_neighborhood` fail with `internal_error`, naming the absent codes in `details.missingCodes`. No network access is needed at startup.
- `get_concepts`: fetch a caller-pinned batch with required `terminology`, `release` and `codes`. Returns `concepts` and `missing` in input order, preserving duplicate occurrences. Optional `include` works as in `get_concept`. Empty input makes no request. At most 650 supplied codes and a 7000-byte encoded request target are allowed; larger inputs are `invalid_request`. An oversized response is `bound_exceeded` (`NCI_SI_EVS_MAX_RESPONSE_BYTES`), never partial results.
- `search_concepts`: search the interim NCIt index with required `terminology`, `release` and `query`. `semantic` and `hybrid` modes are supported, with `limit` default 10, maximum 1000; the index must hold the requested release. Default `lexical`, `typeahead`, cursors and `retired: only` return `capability_unavailable` pending #27.
- `get_concept_hierarchy`: caller-pinned parents or children, excluding the seed. Required `direction`; `depth` defaults to 1, maximum 4; `limit` defaults to 200, maximum 1000. `nextCursor` continues with the same applied arguments. Paging replays within 200 requests: roughly 9,900 nodes for ordinary depth-one fanout at 50 per batch, fewer with retries or oversized responses. Narrow the starting concept or depth if replay returns `bound_exceeded`. Historical releases continue while served; withdrawal returns `cursor_expired`. `pathsToRoot` returns every platform path and unique reached nodes; depth, limit and cursor do not apply to it.
- `get_concept_neighborhood`: caller-pinned graph including the seed. `depth` defaults to 2, maximum 4; `maxNodes` 200/1000; `maxEdges` 1000/5000; optional `budgetPerKind` maximum 1000. `kinds` selects among the six relation kinds. Negative assertions and targets are returned marked; their targets expand only with `includeNegative: true` or a positive route. A relationship without an upstream code remains positive, with its name and qualifiers preserved. Both graph tools share 200 requests per call, including a batched final-frontier check for depth truncation.

These content entries require an explicit release. Live concept and graph reads support EVS
terminologies; NCIt codes follow their stated C-number form, and other codes are encoded as
one path segment. Semantic/hybrid search remains NCIt-only; another terminology is
`invalid_request`. Bounds above their maxima clamp. Invalid arguments return `invalid_request`.

- `resolve_release`: resolve a terminology's current monthly or weekly release, with the other served version identifiers in `alternatives`. `terminology` is required; `channel` defaults to `NCI_SI_RELEASE_CHANNEL`. Returns a flat release record with provenance, or a top-level error. CLI: `resolve-release ncit --channel monthly`.
- `list_terminologies`: list each EVS terminology and its current release with provenance. NCIt uses the configured channel; other terminologies use their sole latest row. CLI: `list-terminologies`. Both discovery tools are resolved afresh and carry `ttlMs: 0`, `cacheScope: public`; failures are private.
An empty unfiltered EVS terminology listing is unusable metadata and returns
`upstream_unavailable` with its actual HTTP status and attempt count. Content queries
with no matches still return empty successes; missing current releases retain their
`release_not_available` behavior.

Each tool description, as sent to MCP clients, states the contract in full. The former
`ncit_*` tools and `cadsr_status` are removed; use the twelve tools above. The caDSR profile
currently exposes no tools. CLI diagnostics retain `search`, `lookup`, `traverse` and
`release-info`, including CLI-only options such as `--live-only` and `--include-raw`.
The release report's `selected_release` field names the configured channel's release.

### Graph bounds

The walk proceeds one depth at a time from the seed, so nearer nodes
claim the limits first. The result's `truncation` is `{"occurred": false}`, or
says which bound dropped something and how much (see Provenance and truncation).
Hierarchy excludes the seed from its page limit and has no edge cap; neighborhood
counts the seed against its global node limit. Within each depth, relationship kinds
take turns across the whole frontier; each kind spends its allowance only on
new nodes. Edges to existing nodes do not spend that allowance. Mixed-kind walks
report `perKind` truncation records when anything is dropped.

Each traversal can make at most 200 HTTP attempts, including retries, split batches and status hydration. Exhaustion before any graph is available returns
`bound_exceeded`; otherwise the partial graph reports the first bound that dropped
anything, using `requests` if no earlier bound was reached. Unread kinds carry
their own truncation record with `omitted: 0` and `exact: false` when the omitted
relation count is unknown. An explicit kind allowance uses `kind_budget`
truncation. These budgets are independent for concurrent calls.
At `depth`, the walk checks the selected relation lists of the final frontier
within the same request budget. Unseen targets produce a `depth` cut: `omitted`
counts distinct targets one level further, with `exact: false` because further
continuation is unknown. Leaves and cycles to returned nodes are complete.
An earlier bound still wins; oversized final lists report `upstream_cap`.
Once a global node cut is reported, the final check is skipped. Otherwise it
reads only kinds that have no truncation of their own. Status-only reads for
returned nodes may still be needed; they use the same request budget.
Inverse lists are never fetched solely to check continuation. At any nonempty final
frontier, each selected inverse kind without a prior cut reports `depth`, `omitted: 0`,
`exact: false`: an unknown continuation, without claiming a leaf. Forward kinds
beside it are still checked. Descendant checks read final child lists only.

## MCP Resources

- `nci-si://concept/ncit/{code}`: the CLI lookup result with default options, including indexed fallback during an upstream outage.
- `nci-si://release/ncit/{version}`: `current` and `latest` return the full CLI `release-info` report for the configured channel, including weekly; the current version returns its `{terminology, channel, version, date}` record. The old `monthly` and `monthly-latest` aliases are rejected. Resource-template replacement remains #30.
- `nci-si://index/ncit/{version}/manifest`: `active`, or the release the local index holds, returns its manifest; without an index the result is `{"active_index": null}`.

## MCP output schemas

Every tool declares an `outputSchema` covering its success object and the shared error
record. Successful `structuredContent` is validated by the MCP SDK and has the same fields
as the JSON text content; optional fields remain omitted, and there is no extra `result`
wrapper. The schema includes the closed error-code set, provenance and recursive truncation
records. The tool listing stays the same across release channels and upstream availability.

## MCP caching hints

Tool results carry `ttlMs` and `cacheScope` in protocol `_meta`, separate from their JSON
content. Lookup, search (including an empty result) and traversal use 86,400,000 ms and
`public`. Discovery tools use 0 and `public`; tool errors use 0 and
`private`. The hints describe freshness and sharing; they do not add a server-side cache.

The four list methods and `server/discover` carry 86,400,000 ms and `public` as result
fields. Resource reads carry the same fields on the read result: concept content and
version-addressed release/index content use 86,400,000 ms and `public`. Moving release-report
aliases, the `active` index alias, and an absent-index report use 0 and `public`.

## Audit records

Every tool call writes one JSON `call_completed` record to stderr, including invalid requests
and failed calls. CLI commands and resource reads use their operation names. Stdout remains
the MCP transport or CLI result. Diagnostic records use the same JSON format.

The completion record includes the correlation identifier, timestamp, tool, safe supplied
parameters, target, requested/resolved releases, status and response code, outbound request
count including retries, result size, elapsed milliseconds and full truncation record. Result
size is the UTF-8 byte length of compact JSON content, excluding MCP framing; no structured
result means null. The result's content is not logged.

Parameter audit classes are declared with the tool: identifiers, closed values and limits
may be recorded; free text and unknown parameters are SHA-256 hashed. Hashes let operators
correlate repeated inputs. They do **not** keep short, guessable terminology queries secret.
Credentials and echoed credentials are redacted, even in correlation metadata. Exception
messages and raw upstream bodies are excluded; external diagnostic messages are hashed.
The diagnostic log level does not disable audit completion records. The platform remains the
authority for audit, quotas and authorisation; this prototype adds no audit database.

## Errors

Every failure the service handles is one error record, and the process exit
code of the CLI is 1. The record is the whole result, so it cannot be mistaken
for an empty one:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "Search query must not be blank. Correct the argument and call again.",
    "details": {"parameter": "query", "reason": "Search query must not be blank"},
    "correlationId": "5c1f0c1b8e9a4c3d9d2a6f3b7e1a0c42"
  }
}
```

`details` is present where the failure has data for the caller's next step; the
keys of each code are those of the error record in [the specification](docs/specification.md).
`correlationId` is the `correlationId` in the `_meta` of the `tools/call`
request, or one generated for the call (for a resource read or a CLI command,
one generated for it). MCP tool results carrying the record are also flagged as
errors at the protocol level, with the record as their structured content, and
a failed resource read is a protocol error whose message is the record.
Invalid MCP arguments, including wrong types, unknown choices and missing required
fields, use the same error record. The CLI argument parser reports syntax failures
in its own format. An
unexpected exception is a bug and is not converted into a record.

Each message ends with the caller's next step. A query that matches nothing is
not an error: it returns its normal shape with an empty list. An answer that
EVS wraps in a success status but that is an error envelope, an error
`OperationOutcome` or an HTML page is `upstream_unavailable`.

| Code | Meaning | `details` |
| --- | --- | --- |
| `invalid_request` | An argument is missing, malformed, out of range, or contradicts another; CLI only: an environment variable is invalid | `parameter`, `reason` |
| `not_found` | The requested release has no concept with that code, or `index-sample` named codes the release does not contain (nothing was indexed) | `identifiers` |
| `release_not_available` | EVS did not name exactly one latest NCIt release for the channel (`requested` names the requested channel; optional `found` lists versions when several rows were returned), EVS no longer serves the pinned release, or a resource names a release that is not current (or not the one the index holds) | `requested`, `source`, `found` |
| `release_mismatch` | The local index holds a different release than the requested one, or EVS served a concept of another release than the one requested | `requested`, `served` (a list of releases), `source` |
| `upstream_unavailable` | EVS could not be reached or kept failing after the retries, rejected the request, or returned something unusable: a malformed, HTML or masked-error body, or a 404 from any request other than a single-concept lookup (check `NCI_SI_EVS_BASE_URL`) | `surface`, `status`, `attempts`, `retryAfter` (`status` and `retryAfter` where known) |
| `timeout` | Every attempt at an EVS request timed out (`NCI_SI_TIMEOUT_SECONDS`) | `surface`, `seconds`, `attempts` |
| `bound_exceeded` | An EVS response exceeds `NCI_SI_EVS_MAX_RESPONSE_BYTES`, or the request budget is exhausted before a graph is available | `bound`, `limit`, `reached` (for response size, the limit plus one when EVS declared no length) |
| `capability_unavailable` | The requested terminology or operation is not supported yet; the MCP tool descriptions name the interim limits | `capability` |
| `cursor_expired` | EVS no longer serves the hierarchy cursor’s release; restart with the current release |  `cursorRelease`, `currentRelease` |
| `internal_error` | `search` or `evaluate` was called before an index was built, the index was built with other embedding settings than the runtime uses, SQLite could not open, read or write the index file named in the message, (CLI only) the index, the embedding model or the MCP package could not be loaded at startup, or the selected relationship catalogue lacks configured exclusion codes | `missingCodes` for missing exclusions only; absent for other causes |

The CLI `release-info` command and its moving resource aliases succeed during an EVS outage:
the `evs_api` and `selected_release` fields then hold an error record
next to the local index manifest.
