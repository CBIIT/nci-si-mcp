# Quickstart

How to install and run the prototype server, what it serves, and how it fails. What this repository
is, and the status of each tool group, is in [README.md](README.md).

See also: the same instructions and diagrams as a searchable website
([local documentation preview](docs/documentation-site.md#build-and-preview)); the documentation
and validation dashboard in [local companion containers](docs/companion-containers.md); the
[validation guide](docs/local-validation.md) for `pdm run portal serve`, runs, results and
configuration proposals, including startup-settings snapshots
(`nci-si-mcp serve --configuration-snapshot PATH`,
[configuration evidence](docs/local-validation.md#configuration-evidence-and-proposals)).

## Install

The project needs Python 3.14 or newer and is managed with [PDM](https://pdm-project.org).

```bash
pdm install
pdm run nci-si-mcp release-info
pdm run nci-si-mcp serve
```

`pdm install` creates `.venv` from `pdm.lock` and installs the package in editable mode with
the test and lint tools, the `server` extra (MCP), and the `index` extra (NumPy for exact
indexed search). The commands below are written as `python -m nci_si_mcp.cli ...`:
run them inside the environment (`eval $(pdm venv activate)`) or prefix them with `pdm run`.

To run a released version without a checkout, install it from its tag; the
[releases page](https://github.com/CBIIT/nci-si-mcp/releases) lists the versions:

```bash
pip install "nci-si-mcp[server] @ git+https://github.com/CBIIT/nci-si-mcp@vX.Y.Z"
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

For a container deployment with an external index and model, follow the short
[container runbook](docs/container.md). The [deployment diagrams](docs/deployment.md) compare
local stdio, a local HTTP container and the proposed cloud layout.

For remote clients, run `pdm run nci-si-mcp serve --transport streamable-http` and connect to
`http://127.0.0.1:8000/mcp`. See [remote transport](docs/transport.md) for session modes,
replica routing, readiness and authentication hooks.

An MCP client starts the stdio server as a command. In a client that reads an `mcpServers`
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
| `NCI_SI_PROFILE` | `unified` | `evs`, `cadsr` or `unified`. Selects twelve EVS tools, ten caDSR tools, or all 29 tools including cross-domain and workflows. Unified also exposes four furnished prompts. Resources follow their group. CLI commands remain available in every profile |
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
| `NCI_SI_TRANSPORT` | `stdio` | Serve over stdio or streamable-http; `serve --transport` overrides this setting |
| `NCI_SI_HTTP_HOST` | `127.0.0.1` | HTTP bind address; binding all interfaces does not relax the Host allow-list |
| `NCI_SI_HTTP_PORT` | `8000` | HTTP port, 1–65535; MCP endpoint is /mcp |
| `NCI_SI_HTTP_SESSIONS` | `stateful` | stateful retains each session's implicit release and needs process affinity; stateless resolves omitted releases per call and needs no affinity |
| `NCI_SI_HTTP_AUTH_MODE` | `trusted-local` | `required` refuses HTTP startup without a complete approved integration; cannot serve stdio |
| `NCI_SI_HTTP_AUTH_FACTORY` | unset | In required mode, an installed `module:factory` returning SDK authentication and caller policy; see [governed HTTP](docs/governed-http.md) |
| `NCI_SI_HTTP_MAX_REQUEST_BYTES` | `4194304` | Maximum HTTP request body bytes, including chunked bodies; oversized requests return 413 before parsing |
| `NCI_SI_HTTP_ALLOWED_HOSTS` | `127.0.0.1:*,localhost:*,[::1]:*` | Comma-separated permitted Host authorities, exact or wildcard port; add the public authority when using a proxy |
| `NCI_SI_HTTP_ALLOWED_ORIGINS` | `http://127.0.0.1:*,http://localhost:*,http://[::1]:*` | Permitted Origin authorities, exact or wildcard port; requests without Origin are allowed |
| `NCI_SI_HTTP_REQUIRE_INDEX` | `0` | Numeric switch (only 0 or 1), following the settings' numeric parsing rather than introducing a separate boolean syntax. Set to 1 when deployment supplies an index: readiness requires an active build compatible with the configured embedding model. With 0 an absent index permits live tools; an existing active build is still verified |

The caDSR lookup, registry, matching, form and code-map tools use upstream APIs.
Registry discovery reads the export folder's exact distribution row. The folder gives
local server time without a zone, so `generatedAt` carries no offset (for example
`2026-07-01T22:19`), not the ZIP file's HTTP timestamp. API content without a published registry
release does not inherit that export date.

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
Samples use their own data directory; `index-sample` refuses to modify an active production
or unclassified build.
`search` only sees what has been indexed; no MCP
tool builds the index.

For a full release, `index-build` downloads and verifies all pinned NCIt search pages,
then evaluates the candidate and returns an inactive build id with its evaluation report.
Production activation requires a passing report. The shipped calibration is for NCIt 26.09d,
SapBERT (`cambridgeltl/SapBERT-from-PubMedBERT-fulltext`), 768 dimensions; another release or
model needs a new full-corpus calibration before activation. `evaluate --build-id BUILD_ID`
repeats evaluation on a completed candidate without activating it. Plain `evaluate` scores
the active build; samples and older unclassified snapshots report all twelve queries without
claiming a production pass. `index-builds` lists completed builds. Activate one
with `index-activate BUILD_ID`; activating the previous id rolls back. Every activation
keeps only the newly active build and the build it replaced. These are operator CLI commands.
The public manifest contains `terminology`, `version`, `concepts`, `embedding`
(`provider`, `model`, `dimensions`), `builtAt` and `provenance`.

Until the index is rebuilt after a new release, CLI `search` keeps serving
the old release (named in the `provenance.release` of each hit), and `lookup` fails with
`release_mismatch` for every code unless `--live-only` is given.
MCP `search_concepts` in semantic/hybrid mode requires the caller's release to match the index; `get_concept`
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
Rebuilding an older, unclassified snapshot creates a production build requiring evaluation;
the original remains available for rollback. For an old developer sample, recreate it with
`index-sample` in a separate data directory instead. The refusal message identifies the original
snapshot and both migration paths. See the [retrieval evaluation protocol](docs/retrieval-evaluation.md)
for metrics, calibration and the distinction between developer checks and production evidence.

New index files use 64 KiB SQLite pages to reduce cold vector-scan I/O. Existing files retain
their page size. To convert an existing file, stop all processes using it and back it up first;
in a SQLite connection to that file run `PRAGMA journal_mode=DELETE`,
`PRAGMA page_size=65536`, `VACUUM`, then `PRAGMA journal_mode=WAL`. This rewrites the file
and needs temporary disk space; it changes neither vectors nor rankings. Opening the index
does not perform this conversion automatically.

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
      "score": 0.969251231258575,
      "matchedOn": "definition"
    }
  ],
  "totalKnown": 5,
  "nextCursor": "opaque-continuation-token"
}
```

The example token is illustrative. Continue with the actual returned `nextCursor` as `cursor`,
keeping the other arguments unchanged; the final page has no `nextCursor`. Pages are not
truncation. An index activation, even for the same release, expires indexed search cursors.

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
| `release` | `{terminology, identifier, date}` of the terminology release the item was read from; `date` is left out when EVS gave none |
| `source` | `evs_rest` for EVS REST, `evs_fhir` for EVS FHIR expansion, `evs_index` for the local NCIt index |
| `servedBy` | `live` or `index` |
| `retrievedAt` | When the item was retrieved; for an indexed concept, when it was indexed |
| `sourceUri` | The upstream URL used for the item: a concept, relationship catalogue, traversal endpoint or FHIR expansion. It may include query parameters, such as the expansion's canonical value-set URL. Optional on empty results; absent for a local search with no hits |
| `correlationId` | The call's `_meta.correlationId`, or one the server generated; the same in every item of the call and in the error record |
| `upstream` | Origin fields the platform supplied, unchanged: REST `terminology` and `version`, or FHIR value-set `url` and `version`. Omitted where the returned item carried none; hydrated concepts retain their own origin fields |
| `attribution` | Licence or copyright text supplied upstream for that item. Omitted when none was supplied; an edge's licence is not copied onto its target concept |
| `graphs`, `registry` | The two graph-joined tools name both graph identities; cross-domain results include `registry` where caDSR content participates. An unpublished registry has no invented release identifier |

An item reached by traversal adds `depth` (an edge has that of the node it reaches; the start
codes have 0); and, for any item but a start code, `relationship` (`kind`; for a role or
association its `name` and its `code` when upstream supplied one; a hierarchy link has only its kind: `parent`, `child` or
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
log names them. CLI search reports `results` when `limit` left scored concepts out; `exact` is true
where every candidate was scored. MCP search pages with `nextCursor` instead of truncating.
`kind_budget` counts new nodes omitted by the first exhausted kind. `requests` counts
unread work as a lower bound; when the number of relations left out for a kind is
unknown, its record gives `omitted: 0` and `exact: false`.

```json
{"occurred": true, "bound": "nodes", "limit": 3, "reached": 3, "omitted": 2, "exact": false}
```

## MCP Tools

For NCIt content tools, omit `release` (or use `null`) to resolve the configured monthly/weekly
channel once. The first implicit release stays pinned for that MCP session. An explicit release
applies only to that call and never changes the session pin. Other terminologies require an
explicit release. If EVS withdraws the session release, start a new session or name a release;
the server never silently switches it. Cursors bind the effective release. For example, call
`get_concept` with `{"terminology": "ncit", "code": "C3262"}`. Every result still names its release.
CLI invocations and HTTP calls without a session resolve independently. Discovery operations
remain fresh, and resource URIs keep an explicit release. Completion audit records name the
selection as `explicit`, `session-held` or `freshly-resolved`. Implicit NCIt calls use
`ttlMs: 0` / `cacheScope: private`; explicit-release calls retain `86,400,000/public`.

- `get_concept`: fetch an EVS concept at the effective release with required `terminology` and `code`. Optional `include` selects synonyms, definitions, properties or semanticType; status is passed through from EVS.
- `resolve_retired_code`: fetch a required `terminology` and `code`, returning upstream `active` and optional `status`, with `replacements` always present. Active concepts return an empty list without a history request. Retired concepts use the pinned single-code history endpoint; each named replacement carries its code, name, terminology and provenance. A history 404 is an upstream error, not an empty result.
- `get_concept_subsets`: read the required `terminology` and `code`, returning its `Concept_In_Subset` associations as subset code, terminology, name and provenance, in platform order.
- `expand_value_set`: expand an NCIt subset with required `terminology` and exactly one of `valueSet` or `code`. `count` defaults to 200 and clamps to 1000; `offset` defaults to 0. `activeOnly` defaults to false; when true, inactive members are removed before paging and `total` counts those kept. Members retain platform order and FHIR provenance, with `inactive` only when true. Pages, including clamped and empty pages, are not truncation. EVS's unpinned expansion must report exactly the requested release or the call fails with `release_mismatch`; historical expansion is not guaranteed. Other terminologies return `capability_unavailable` without a request.
- `get_concept_mappings`: read the required `terminology` and `code`, returning its maps in platform order. Optional `targetTerminology` matches the platform label exactly, including case. Record values are unchanged; optional target version and term type are omitted when absent, null or empty. Both tools use one pinned concept read, carry provenance on empty results and fail on malformed content.
- `list_relationships`: list the pinned release’s roles and associations with code, terminology, name, kind, polarity and provenance. Reads each catalogue once per call; no cross-call cache. Missing configured exclusion codes make this tool and `get_concept_neighborhood` fail with `internal_error`, naming the absent codes in `details.missingCodes`. No network access is needed at startup.
- `get_concepts`: fetch a batch at the effective release with required `terminology` and `codes`. Returns `concepts` and `missing` in input order, preserving duplicate occurrences. Optional `include` works as in `get_concept`. Empty input makes no content request; implicit release discovery may still run. At most 650 supplied codes and a 7000-byte encoded request target are allowed; larger inputs are `invalid_request`. An oversized response is `bound_exceeded` (`NCI_SI_EVS_MAX_RESPONSE_BYTES`), never partial results.
- `search_concepts`: search a pinned terminology with required `terminology` and `query`. Default `lexical` and `typeahead` use EVS's order; lexical preserves its highlight as `matchedOn`, while typeahead omits it. Neither invents a score. `semantic` and `hybrid` use the exact NCIt index, which must hold the requested release; they require NumPy (`index` extra) and return a score and winning field. `limit` defaults to 10 and clamps to 1000. All modes return `totalKnown` and continue with `cursor` until `nextCursor` is absent. `retired: only` filters using the pinned terminology's advertised retirement status, or returns `invalid_request` if none is selectable; the default `include` keeps active and retired matches.
- `get_concept_hierarchy`: parents or children at the effective release, excluding the seed. Required `direction`; `depth` defaults to 1, maximum 4; `limit` defaults to 200, maximum 1000. `nextCursor` continues with the same applied arguments. Paging replays within 200 requests: roughly 9,900 nodes for ordinary depth-one fanout at 50 per batch, fewer with retries or oversized responses. Narrow the starting concept or depth if replay returns `bound_exceeded`. Historical releases continue while served; withdrawal expires an explicit-release cursor. An implicit session pin instead fails with `release_not_available`, asking for a new session or an explicit release. `pathsToRoot` returns every platform path and unique reached nodes; depth, limit and cursor do not apply to it.
- `get_concept_neighborhood`: a graph at the effective release including the seed. `depth` defaults to 2, maximum 4; `maxNodes` 200/1000; `maxEdges` 1000/5000; optional `budgetPerKind` maximum 1000. `kinds` selects among the six relation kinds. Negative assertions and targets are returned marked; their targets expand only with `includeNegative: true` or a positive route. A relationship without an upstream code remains positive, with its name and qualifiers preserved. Both graph tools share 200 requests per call, including a batched final-frontier check for depth truncation.

These content entries require an explicit release for non-NCIt terminologies. Live concept and graph reads support EVS
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
`ncit_*` tools and `cadsr_status` are removed. CLI diagnostics retain `search`, `lookup`, `traverse` and
`release-info`, including CLI-only options such as `--live-only` and `--include-raw`.
The release report's `selected_release` field names the configured channel's release.

The caDSR tools are available in `cadsr` and `unified`. Credentials have not been issued for
development; their tests use contract-crafted fixtures. Runtime responses always come from
the configured upstream, never from a built-in fixture.

- `get_data_element`: exactly one of `publicId`, `questionText` or `longName`. A preferred question with one candidate retrieves the full item; none is `not_found`, several is `invalid_request` naming candidates. Long-name lookup is unavailable (OP-C02). Optional `version` selects the item's version. Optional `include` selects permissibleValues, valueDomain, conceptAssociations, alternateNames or classificationSchemes; without it, only the base record is returned. Nested permissible values and schemes carry provenance. CLI: `get-data-element --public-id 2200604`.
- `search_data_elements`: required `query`, optional `mode` (default lexical), `filters`, `limit` (10/100), `cursor` and `registryRelease`. The requested keyword route (OP-C03, C-3) is not served by caDSR today. Failures retain their original details and `upstream_unavailable` code, with conditional guidance: persistent failures may reflect the missing keyword capability; use `get_data_element` by public id or question text. Transient failures may still recover on retry. Semantic/hybrid modes (OP-C04) and nonempty filters are `capability_unavailable`. Successful upstream lists retain order and page locally; a 1,000-row response reports `upstream_cap`, numeric omitted at least one and exact false. `totalKnown` appears only for an upstream count. CLI: `search-data-elements QUERY`; `--filters` accepts a JSON object, but filters remain unavailable until caDSR serves the keyword route ([cadsr-search](docs/upstream/cadsr.md#cadsr-search)).
- `list_contexts`: upstream context names as identifiers, with provenance and no invented definitions. Optional `limit` (100/1000), `cursor` and `registryRelease`. CLI: `list-contexts`.
- `list_classification_schemes`: optional `context`, `limit` (100/1000), `cursor` and `registryRelease`; returns `capability_unavailable` until OP-C13 exists. Use `get_data_element` with include classificationSchemes to read an element's schemes and nested items. CLI: `list-classification-schemes`.
- `resolve_registry_release`: no arguments. Returns published registry metadata if available, otherwise the exact export row's local date-time without an offset or identifier. TTL 0/public. CLI: `resolve-registry-release`.
- `get_form`: `publicId` is required for lookup; optional `version` selects its item version. Optional `keyword` is rejected because Form/query requires an identifier. `includeModules` defaults to true; false omits modules/questions, otherwise their order and fields pass through. Retired statuses remain unchanged. Optional `registryRelease` fails closed: unpublished pins are release_not_available; a published pin is capability_unavailable (pinned form lookup) until C-1 defines its transport. CLI: `get-form --public-id 5406471 --no-modules`.
- `get_permissible_value`: required `permissibleValueId`, optional `registryRelease`. Returns capability_unavailable (OP-C10): REST publishes the identifier but does not retrieve a value by it. Invalid ids and unlisted pins are rejected first. Read a containing data element with include permissibleValues instead. CLI: `get-permissible-value 9192925`.
- `get_code_map`: optional `sourceSystem` (CRDC only, the default), `targetContext`, `dataElementId`, `limit` (100/1000), `cursor` and `registryRelease`. One map per CRDC data element, preserving values and colon-joined concept codes; targetContext matches exact comma-split Used By names. Coverage counts values with concept codes. Missing binding is explicit as valueLevelBinding false and values empty. Cursors bind all arguments, and verified pins must be confirmed upstream. CLI: `get-code-map --target-context GDC`.
- `match_data_elements`: required `entities` (1–10 objects with `name`, optional `userTip` and `permissibleValues` strings). Optional `matchLimit` (10/100), `filters` and `registryRelease`. Optional `modelVariant` and `similarityThreshold` are rejected with `invalid_request`, citing C-6. Filters accept context, workflowStatus, registrationStatus and valueDomainType as text, and classificationScheme as `{publicId, version}` with both required. One object is sent per entity, matches retain entity/platform order with the platform's score, rule and matched text. Any failure fails the whole call. CLI: `match-data-elements '{"name":"Patient Gender"}'`; `--filters` takes a JSON object.
- `match_value_meanings`: required `values` (1–10 strings), optional `strictness` (restricted by default, or unrestricted), `terminologyScope` (list of code-system codes) and `registryRelease`. Matches retain platform order, type, rule, identity, context and workflowStatus; a missing concept/source, registrationStatus, score or empty/NA crosswalk is omitted. CLI: `match-value-meanings Male Female --strictness unrestricted`; repeat `--terminology-scope` for multiple codes.

The cross-domain tools are available in `unified`:

- `find_data_elements_for_concept`: required `conceptCode`; optional `terminology` (ncit), `release`, `expandDescendants`, `includePermissibleValues`, `limit` (100/1000) and `cursor`. Verifies both graph identities and the NCIt version. One page limit and 1,000-result cap cover data-element uses first, then value uses. Requested `permissibleValues` stays present, possibly empty. Cursors expire when content changes; a sentinel cut is inexact truncation, ordinary paging is not. CLI: `find-data-elements-for-concept C17357 --include-permissible-values`.
- `get_concept_for_permissible_value`: `dataElementId` with exact `value`, or `permissibleValueId` (unavailable, OP-C10); optional `release`. Selects the latest numeric item version first, then the matching value's main concept. Minor concepts are qualifiers; conflicting or missing main concepts report ambiguous registry data. Value text is matched locally, never interpolated into SPARQL. CLI: `get-concept-for-permissible-value --data-element-id 2200604 --value Male`.
- `resolve_stored_value`: required `conceptCode` and `commons`; optional `release` and `dataElementId`. GDC uses exact-code mapset matches and verifies its version; other commons use exact CRDC context membership and value bindings. `dataElementId` restricts CRDC and is explicitly unsupported for GDC. Missing bindings return no values with evidence and coverage zero. CLI: `resolve-stored-value C4817 GDC`.
- `get_release_alignment`: optional nonnegative `maxIntervalDays` (31). Reads NCIt, both Shared SI graphs and the caDSR export; reports their ISO dates and largest interval, warning strictly above the threshold. TTL 0/public. CLI: `get-release-alignment --max-interval-days 31`.

Cross-domain content uses the call/session NCIt pin when `release` is omitted, with TTL
0/private. Explicit content has a short public TTL because the joined sources remain
unpinned. Graph content has no REST fallback that cannot verify its NCIt release.

Matching results, including empty ones, use TTL 0/private. Calls use
`NCI_SI_MATCH_TIMEOUT_SECONDS` (45 seconds by default); timeouts are errors, never empty
results. Header filters must be printable ASCII; entity/value text stays unchanged in JSON.
Unlisted matching pins are `release_not_available`; a published pin is
`capability_unavailable` (`pinned matching`) because the matching APIs have no registryRelease
field yet (C-1, [cadsr-match-parameters](docs/upstream/cadsr.md#cadsr-match-parameters)). No
unpinned match is labelled pinned.

Workflow tools are available in `unified`:

- `ground_value`: exactly one of `conceptCode` or `text`; optional `commons`, `release` and `registryRelease`. Text selects the first result of default lexical search (limit 10), then reads that concept at the same release. No match is `not_found`; use other text or a concept code. Data-element, permissible-value and optional stored-value hops each have an independent 1,000-result cap. A cut reports full `perHop` truncation records; cutting one hop never cuts another. Without commons, storedValues is absent. Explicit-release joins use the shorter TTL/public; implicit NCIt selection uses 0/private. CLI: `ground-value --concept-code C4817 --commons GDC`.
- `expand_cohort`: required `conceptCode`; optional `release`, `maxDepth` (2/4), `includeNegative` (false) and `maxNodes` (200/1000). Returns the start and child descendants, withholding only codes excluded by the start's negative roles unless includeNegative is true. Every exclusion assertion is retained, including multiple assertions for one code. maxNodes counts returned codes including the start; graph edges and bounds retain provenance. Explicit release uses long/public caching; implicit release uses 0/private. CLI: `expand-cohort C4817 --max-depth 2`.
- `harmonize_data_dictionary`: required `columns` (1–10 objects with name, optional description and sampleValues); optional `registryRelease` and matching `filters`. Match names/descriptions, align samples through restricted VM Match in batches of ten, and return each column's matches/alignment plus unmatched names in caller order. Identical requests are reused. Every match names the same registry state. Any failure fails the whole call; results use 0/private. CLI: `harmonize-data-dictionary '{"name":"Patient Gender","sampleValues":["Male"]}'`.

Workflows share one outbound request budget, retries included. Omitted registryRelease is
unpinned; unlisted pins fail with release_not_available and published but unaddressable pins
with capability_unavailable. No export date becomes a registry release identifier.

The four furnished prompts—protocol_authoring, crdc_model_alignment, uscdi_cancer_curation
and cross_program_harmonization—are listed only in unified. Their declared arguments are
substituted into the exact templates in `spec/prompts.yaml`; prompts perform no content calls.

The Form-by-ID API uses type E even for an unknown form. Only HTTP 200 with an explicit
`form: null` and `apiResponse.type: E` on a validated id is interpreted as `not_found`.
The recording has no other discriminator: a genuine platform failure in exactly that shape
would also read as not found. Other operations, statuses and shapes retain normal upstream
error handling. The upstream package ([cadsr-forms](docs/upstream/cadsr.md#cadsr-forms)) asks
caDSR to make absence explicit.

Each caDSR content tool accepts optional `registryRelease`. A pin must be listed upstream
before any content request, and every supported pinned content response must confirm it; absent/unlisted
pins return `release_not_available`, a missing or different confirmation `release_mismatch`.
Unpinned content names only `{registry: cadsr}` in provenance. No item version or export date
is a registry pin. Cursors bind all normalized arguments, including the pin and applied limit;
passing another query or pin is `invalid_request`. Bounds above their maxima clamp.

### Graph bounds

The walk proceeds one depth at a time from the seed, so nearer nodes
claim the limits first. The result's `truncation` is `{"occurred": false}`, or
says which bound dropped something and how much (see Provenance and truncation).
Hierarchy excludes the seed from its page limit and has no edge cap; neighborhood
counts the seed against its global node limit. Within each depth, relationship kinds
take turns across the whole frontier; each kind spends its allowance only on
new nodes. Edges to existing nodes do not spend that allowance. Mixed-kind walks
report `perKind` truncation records when anything is dropped.

Each traversal can make at most 200 HTTP attempts, including retries, split batches and status hydration.
Hierarchy paging replays the pinned walk; exhausting its request budget returns
`bound_exceeded` and asks the caller to narrow the query. Neighborhood and CLI traversal
return `bound_exceeded` before any graph is available; otherwise the partial graph reports
the first bound that dropped anything, using `requests` if no earlier bound was reached. Unread kinds carry
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

- `ncit://concept/{release}/{code}`: the pinned `get_concept` record, with synonyms, definitions, properties and semanticType. The release is required; an upstream failure remains an error.
- `ncit://release/{version}`: a served NCIt version with terminology, channel, version, date, alternatives and provenance. Historical versions use their upstream tags; the configured channel is preferred when both monthly and weekly are present.
- `ncit://index/manifest/{release}`: the active index's manifest only when its release matches. No active index is `capability_unavailable`; another active release is `release_mismatch`. An inactive matching build is not served.
- `cadsr://data-element/{publicId}`: a data element at its latest item version, without extra sections or a registry pin.
- `cadsr://data-element/{publicId}/{version}`: a data element at the named item version; its version is not a registry release.
- `cadsr://registry/release`: registry state with source provenance; the export listing supplies local time without an offset while no registry release is published.
- `cadsr://crosswalk/crdc`: the CRDC crosswalk at its 1,000-map maximum page, with item provenance and short public caching. A larger crosswalk reports exact truncation; use `get_code_map` for filters and further pages.

EVS JSON resources appear in `evs` and `unified`; caDSR resources in `cadsr` and `unified`.
Successful reads carry public caching hints on the protocol result. Failed reads are protocol errors;
handler failures carry the shared error envelope. The old `nci-si://` URIs and moving
`current`, `latest` and `active` aliases are removed. Use `resolve_release` to discover a
version, then put that version in the resource URI. CLI `release-info` remains the status report.

## MCP output schemas

Every tool declares an `outputSchema` covering its success object and the shared error
record. Successful `structuredContent` is validated by the MCP SDK and has the same fields
as the JSON text content; optional fields remain omitted, and there is no extra `result`
wrapper. The schema includes the closed error-code set, provenance and recursive truncation
records. The tool listing stays the same across release channels and upstream availability.

## MCP caching hints

Tool results carry `ttlMs` and `cacheScope` in protocol `_meta`, separate from their JSON
content. Explicitly release-pinned content uses 86,400,000 ms/public; unpinned caDSR content (including
empty results) uses 3,600,000 ms/public. Implicit NCIt calls and computed matching results use 0/private.
Discovery tools use 0 and `public`; tool errors use 0 and
`private`. The hints describe freshness and sharing; they do not add a server-side cache.

The four list methods and `server/discover` carry 86,400,000 ms and `public` as result
fields. Resource reads carry the same fields on the read result: concept content and
version-addressed EVS release/index content use 86,400,000 ms and `public`.
The unpinned caDSR resources use 3,600,000 ms/public, including registry state: the resource
holds content, while the resolver tool is an uncached status operation.

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
Credentials and echoed credentials are redacted, even in correlation metadata and upstream
`Retry-After` error details; redaction does not change the actual retry delay. Exception
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
| `release_not_available` | EVS did not name exactly one latest NCIt release for the channel (`requested` names the requested channel; optional `found` lists versions when several rows were returned), EVS no longer serves the pinned release, or a release resource names an unserved version or one with absent/ambiguous channel metadata | `requested`, `source`, `found` |
| `release_mismatch` | The local index holds a different release than the requested one, or EVS served a concept of another release than the one requested | `requested`, `served` (a list of releases), `source` |
| `upstream_unavailable` | EVS could not be reached or kept failing after the retries, rejected the request, or returned something unusable: a malformed, HTML or masked-error body, or a 404 from any request other than a single-concept lookup (check `NCI_SI_EVS_BASE_URL`) | `surface`, `status`, `attempts`, `retryAfter` (`status` and `retryAfter` where known) |
| `timeout` | Every attempt at an upstream request timed out (`NCI_SI_TIMEOUT_SECONDS`; `NCI_SI_MATCH_TIMEOUT_SECONDS` for caDSR matching) | `surface`, `seconds`, `attempts` |
| `bound_exceeded` | An EVS response exceeds `NCI_SI_EVS_MAX_RESPONSE_BYTES`, the request budget is exhausted before a graph is available, or hierarchy page replay exhausts its request budget | `bound`, `limit`, `reached` (for response size, the limit plus one when EVS declared no length) |
| `capability_unavailable` | The requested terminology or operation is not supported yet, or an index resource has no active index; the MCP tool descriptions name the interim limits | `capability` |
| `cursor_expired` | EVS no longer serves a hierarchy or live-search cursor’s release, or the active indexed-search build changed; restart the query. Same-release build replacement also expires a cursor, with equal release identifiers | `cursorRelease`, `currentRelease` |
| `permission_denied` | Secured caller policy denies the operation, is missing, unavailable or expired; contact the service operator to review access | None; restricted identifiers and required permissions are not disclosed |
| `internal_error` | `search` or `evaluate` was called before an index was built, the index was built with other embedding settings than the runtime uses, SQLite could not open, read or write the index file named in the message, a production evaluation or sample-isolation check refused an operator command, the selected build is unavailable or a concurrent writer changed the active build, (CLI only) the index, the embedding model or the MCP package could not be loaded at startup, or the selected relationship catalogue lacks configured exclusion codes | `missingCodes` for missing exclusions only; absent for other causes |

The CLI `release-info` command succeeds during an EVS outage:
the `evs_api` and `selected_release` fields then hold an error record
next to the local index manifest.

## Caller permissions

The trusted-local default is unchanged. Embedders can inject verified per-request caller policy;
see [caller permissions](docs/caller-permissions.md) for the capability map, denials, session
ownership and private caching. Production identity integration is not yet enabled.
