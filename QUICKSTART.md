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
| `NCI_SI_PROFILE` | `unified` | `evs`, `cadsr` or `unified`. Selects twelve EVS tools, ten caDSR tools, or all 29 tools including cross-domain and workflows. Unified also exposes four furnished prompts: the templates of `spec/prompts.yaml` with the arguments substituted, which make no content calls. Resources follow their group. CLI commands remain available in every profile |
| `NCI_SI_UPSTREAM_MODE` | `live` | `live` or `fixture`; selects the five base URLs below as a set (next paragraph) |
| `NCI_SI_EVS_BASE_URL` | `https://api-evsrest.nci.nih.gov` | EVS REST endpoint (`http` or `https`) |
| `NCI_SI_EVS_FHIR_BASE_URL` | `https://api-evsrest.nci.nih.gov/fhir/r4` | EVS FHIR endpoint |
| `NCI_SI_CADSR_BASE_URL` | `https://cadsrapi.cancer.gov/rad` | caDSR REST endpoint |
| `NCI_SI_CADSR_FTP_URL` | `https://cadsr.nci.nih.gov/ftp/caDSR_Downloads` | caDSR export (FTP) endpoint |
| `NCI_SI_SSIS_SPARQL_URL` | `https://shared.semantics.cancer.gov` | Shared Semantic Infrastructure SPARQL endpoint |
| `NCI_SI_RELEASE_CHANNEL` | `monthly` | The default channel for discovery and CLI diagnostics: `monthly` or `weekly`. The release is the one EVS row that is latest and tagged with the channel; with none or several the call fails with a release-not-available error. MCP content tools use the release the caller supplies |
| `NCI_SI_EXCLUSION_ROLE_CODES` | `R135,R136,R137,R138,R139,R140,R141,R142` | NCIt exclusion roles, a comma-separated list of codes (`R` and digits); checked against the requested release catalogue on each relationship listing or neighborhood call |
| `NCI_SI_EVS_LICENSE_KEY` | unset | EVS licence key, sent as the `X-EVSRESTAPI-License-Key` header on EVS requests and to no other host; at least 8 characters, so redaction never matches unrelated text. A credential: never logged, in no error message or string form |
| `NCI_SI_CADSR_CREDENTIAL` | unset | caDSR credential as `user:password` (the password at least 8 characters); handled like the licence key |
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
| `NCI_SI_HTTP_REQUIRE_INDEX` | `0` | Boolean switch, read as `0` or `1` only (anything else is a configuration error). Set to 1 when deployment supplies an index: readiness requires an active build compatible with the configured embedding model. With 0 an absent index permits live tools; an existing active build is still verified |

The caDSR lookup, registry, matching, form and code-map tools use upstream APIs.
Registry discovery reads the export folder's exact distribution row. The folder gives
local server time without a zone, so `generatedAt` carries no offset (for example
`2026-07-01T22:19`), not the ZIP file's HTTP timestamp. API content without a published registry
release does not inherit that export date.

The five base URLs are one set. In `live` mode a base URL that is not given takes its production
default, and one that is given replaces that default. In `fixture` mode every one of the five must be given, so a fixture
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
    }
  ],
  "semanticType": [
    "Neoplastic Process"
  ]
}
```

The result is shortened: the concept has two further definitions.

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
          "kind": "child",
          "name": ""
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
          "kind": "child",
          "name": ""
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

The result is shortened to the seed, one subtype and the edge between them: the full
result holds four nodes and three edges, and the `truncation` record is as returned.

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
codes have 0); and, for any item but a start code, `relationship` (`kind` and `name`; for a role
or association the name is upstream's type, else the edge type, and the `code` is present when
upstream supplied one; a hierarchy link has the kind `parent`, `child` or `descendant`, an empty
`name` and no code), `direction` (`out` or `in`, the way the edge type is followed) and `polarity`
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

Every parameter of every tool is described in the served input schema, with an example, its default
and its maximum where it has them, its stated form (`pattern`) and its list limits
(`minItems`, `maxItems`); the schema closes the argument set, and that of each nested record (`additionalProperties: false`),
and carries no generated titles.
A parameter caDSR does not serve yet says so there, with its requirement identifier, and is left unset.

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

Bounds above their maxima clamp, and invalid arguments are `invalid_request`. Live concept and
graph reads accept any EVS terminology: NCIt codes follow their C-number form and other codes are
encoded as one path segment. Matching header filters must be printable ASCII, and entity and value
text is sent unchanged. The former `ncit_*` tools and `cadsr_status` are not served.

The tools, generated from `spec/tools.yaml` by `pdm run quickstart-tools`:

<!-- tool-summaries:begin -->
| Tool | Group | What it does |
|---|---|---|
| `resolve_release` | EVS | Which release of a terminology is current, by channel (without one, the channel configured, A3.6.2); called explicitly for discovery; NCIt content calls may instead omit release under X-22. |
| `get_concept` | EVS | One concept with the detail selected. |
| `get_concepts` | EVS | Many concepts in one platform call, with the detail selected. |
| `search_concepts` | EVS | Ranked search of a terminology; semantic and hybrid from the interim NCIt index (M4.1). Retired concepts are returned with the others (retired include, the default, as the platform returns them), or alone (retired only), where retirement is a concept status the search can select: the listing's metadata.retiredStatusValue is among its metadata.conceptStatuses, and that status is what is sent. On 2 October 2026 this holds for NCIt alone. Every mode takes the same two values. Search cannot leave retired concepts out: the platform offers no such selection, and each result's active and status let a caller drop them itself. |
| `get_concept_hierarchy` | EVS | A concept's parents, children or paths to the root, bounded. nodes holds the concepts reached, not the one asked about, and limit is a page that the cursor continues. pathsToRoot returns each path the platform gives in paths, the codes from the concept to the root in the platform's order, with each concept on them once in nodes; depth, limit and cursor do not apply to it. |
| `expand_value_set` | EVS | The members of a value set, paged and bounded by the tool itself. It pages by count and offset, as FHIR $expand does, in place of a cursor (the exception to M6.1); activeOnly is false unless given. |
| `get_concept_neighborhood` | EVS | Bounded traversal across roles and associations with a budget per kind; nodes holds the concept asked about at depth 0 and those reached, and maxNodes counts them all. Given, budgetPerKind bounds the nodes each kind adds; not given, the tool shares maxNodes among the kinds asked so that none present is starved, in a way of its own. Negative assertions are returned marked, with the nodes they reach, which are not followed further unless includeNegative. |
| `get_concept_subsets` | EVS | The subsets a concept belongs to. |
| `get_concept_mappings` | EVS | The maps the platform carries on a concept, from it to other terminologies, unchanged and each with its target's version where the platform gives one; maps into the terminology from others are not this tool's. targetTerminology keeps the maps whose target the platform names exactly so, case included. |
| `resolve_retired_code` | EVS | Whether a code is retired (active false, as the platform publishes it), its status, and what the platform names as replacing it: replacements is an empty list, present, where the platform names none, retired or not. |
| `list_relationships` | EVS | The relationship catalogue of a release, polarity marked by code. |
| `list_terminologies` | EVS | The terminologies available, with their current releases. |
| `resolve_registry_release` | caDSR | The registry's content state: published false and the export's date while caDSR publishes no registry release, never an invented identifier; the release itself where one is. |
| `get_data_element` | caDSR | One data element, at its latest version or the version given, with the sections include selects; without include, the record's own fields. questionText finds it by its preferred question text, an invalid request naming the candidates where several have it and not_found where none has; longName is capability_unavailable until the platform serves that lookup (OP-C02). |
| `search_data_elements` | caDSR | Search of data elements, filtered by context, status and value-domain type, a page at a time up to the platform's cap of 1,000 results a query, which truncation reports (upstream_cap). semantic and hybrid are capability_unavailable until the platform serves them (OP-C04). |
| `match_data_elements` | caDSR | Data elements matched to described entities, scored and rule-attributed, at most matchLimit for each entity; the contract's default is 10, and the maximum of 100 is this specification's, the contract stating none. The classificationScheme filter takes both publicId and version. modelVariant and similarityThreshold, which the platform does not take, are an invalid request when given. At most 10 entities a call: the contract states no maximum, and one entity took 28.9 s (10 September 2026) against a match timeout of 45 s. |
| `match_value_meanings` | caDSR | Value meanings and concepts matched to values, each rule-attributed with its crosswalk; strictness is vmMatch's matchType, terminologyScope its evsTerminologyCodes. At most 10 values a call: the contract states no maximum, and one value took 15.5 s (10 September 2026) against a match timeout of 45 s. |
| `get_form` | caDSR | A form or case report form by public id, with its modules and questions; a keyword is an invalid request saying the platform needs an identifier (Form/query takes a public or protocol id only). |
| `get_permissible_value` | caDSR | A permissible value by the identifier caDSR REST publishes for it; capability_unavailable until the platform retrieves a value by it (OP-C10). |
| `get_code_map` | caDSR | Code maps between source code systems and registered value sets, one per data element of the CRDC crosswalk with the contexts and commons that use it; targetContext selects those a context or commons uses, dataElementId one data element. |
| `list_contexts` | caDSR | The registry's contexts, by name. |
| `list_classification_schemes` | caDSR | Classification schemes as objects with their nested items; capability_unavailable until the platform lists them (OP-C13). A data element's schemes come with get_data_element. |
| `find_data_elements_for_concept` | Cross-domain | The data elements that use a concept, as an object class, property or permissible value concept, optionally across its descendants; with includePermissibleValues, also the permissible values whose value meaning stands for it (the reverse lookup, OP-S04). The effective release (X-22) is checked against the NCIt graph's: release_mismatch on a difference. It is served from a surface that names the NCIt release it used: today the Shared SI Service; caDSR REST, which cannot, answers release_not_available. |
| `get_concept_for_permissible_value` | Cross-domain | The concept a permissible value stands for, named by its data element and value, as the concept record of the release pinned, its provenance naming both content states (release and registry); the caller may name a release or use implicit NCIt resolution (X-22). The data element is asked for by its id, and the value is compared locally with its permissible values by exact, case-sensitive equality (no trimming, no case-folding), never sent upstream (A7.7); a value it does not have is not_found. By permissibleValueId, capability_unavailable until the platform retrieves a value by it (OP-C10). |
| `resolve_stored_value` | Cross-domain | The literal a data commons stores for a concept of the release given: for GDC through the EVS mapset NCIt_Maps_To_GDC, served unpinned, so the release the mapset reports is compared with the one asked for (release_mismatch on a difference); for the other commons through the CRDC crosswalk, which names the registry state. confidence is asserted when a published source names the value and none otherwise; evidence names each source consulted ({ mapset, version } or { crosswalk, dataElement }), whether the commons binds values to concepts (valueLevelBinding), and its coverage: the number of stored values found. A commons without a value-level binding (PDC, IDC) returns no stored value with evidence saying so, never the preferred term as if stored. |
| `get_release_alignment` | Cross-domain | The release of every dataset a cross-domain answer rests on (NCIt, the Shared SI NCIt and caDSR graphs, the caDSR export), each date ISO-8601; intervalDays, the largest interval in days between any two of their dates; and a warning naming maxIntervalDays when intervalDays exceeds it. |
| `ground_value` | Workflow | For a concept: the data elements that use it, the permissible values that stand for it, and, with commons, the stored values a commons uses for it, each asked by the concept, under one provenance envelope naming both content states (release and registry). Without commons there is no storedValues. A text is resolved as search_concepts resolves it with its defaults (lexical, limit 10), to its first result, the concept chosen named in the result. Without registryRelease the registry is unpinned, as for every caDSR tool (X-21). A hop holds at most find_data_elements_for_concept's maximum (1,000), its bound `results`; each hop's truncation is carried (perHop), and since each hop is asked by the concept, one hop's bound limits no other. |
| `expand_cohort` | Workflow | The codes a cohort query should use: the concept itself and its descendants to maxDepth. A code an exclusion role of the concept asked about asserts is withheld from codes and listed in excluded with its assertion, unless includeNegative keeps it in codes (still listed), one excluded record per exclusion assertion; a descendant's own exclusion roles are its own, not the cohort's. maxNodes counts the codes, the concept included. The result equals composing get_concept_hierarchy (child, depth maxDepth) and get_concept_neighborhood (depth 1). |
| `harmonize_data_dictionary` | Workflow | A data dictionary's columns matched to data elements, one match call per column (its name as the entity, its description as entityUserTip), and each column's sample values aligned to value meanings (vmMatch); unmatched names the columns without a match; every match names one registry state. |
<!-- tool-summaries:end -->

[docs/specification.md](docs/specification.md#2-tools) gives each tool's inputs, bounds, defaults and
result records. The description a client receives with each tool says what the tool is for, what its arguments mean,
what comes back and how it fails, and names the requirement (OP-*, C-*) of every capability the
platform lacks. The server instructions carry what spans tools: the error record, the release rule and
a map from each decision to the tool that serves it.
The caDSR tools are available in `cadsr` and `unified`, the cross-domain and workflow tools in
`unified`; caDSR credentials have not been issued, so their tests use contract-crafted fixtures.
Runtime responses always come from the configured upstream, never from a built-in fixture.

### CLI

The caDSR, cross-domain and workflow tools, `resolve-release` and `list-terminologies` have a
subcommand of the same name with dashes; the ones below show the shape of a call. The other EVS
content tools (concept, hierarchy, neighborhood, subsets, mappings, value sets, relationships,
retired codes) are MCP-only.
`search`, `lookup` and `traverse` are the local-index CLI commands beside `release-info`; the
index lifecycle commands (`index-sample`, `index-build`, `index-rebuild`, `index-builds`,
`index-activate`) and `evaluate` are described under [Build a small local
index](#build-a-small-local-index).

- `resolve-release ncit --channel monthly`; `list-terminologies`
- `get-data-element --public-id 2200604`
- `search-data-elements QUERY`; `--filters` accepts a JSON object, but filters remain unavailable
  until caDSR serves the keyword route ([cadsr-search](docs/upstream/cadsr.md#cadsr-search))
- `list-contexts`; `list-classification-schemes`; `resolve-registry-release`
- `get-form --public-id 5406471 --no-modules`; `get-permissible-value 9192925`
- `get-code-map --target-context GDC`
- `match-data-elements '{"name":"Patient Gender"}'` (`--filters` takes a JSON object);
  `match-value-meanings Male Female --strictness unrestricted` (repeat `--terminology-scope` for
  several codes)
- `find-data-elements-for-concept C17357 --include-permissible-values`;
  `get-concept-for-permissible-value --data-element-id 2200604 --value Male`;
  `resolve-stored-value C4817 GDC`; `get-release-alignment --max-interval-days 31`
- `ground-value --concept-code C4817 --commons GDC`; `expand-cohort C4817 --max-depth 2`;
  `harmonize-data-dictionary '{"name":"Patient Gender","sampleValues":["Male"]}'`

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
Hierarchy paging replays the pinned walk within those 200 attempts (roughly 9,900 nodes for an
ordinary depth-one fanout at 50 per batch, fewer with retries or oversized responses);
exhausting the request budget returns
`bound_exceeded` and asks the caller to narrow the query. `list_relationships` shares the same 200
attempts across its two catalogues, and the workflow tools share one outbound request budget,
retries included. Neighborhood and CLI traversal
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
as the JSON text content, which is compact with sorted keys and is the one text form every tool
result takes, errors included; optional fields remain omitted, and there is no extra `result`
wrapper. The schema includes the closed error-code set, provenance and recursive truncation
records. The tool listing stays the same across release channels and upstream availability.

## MCP caching hints

Tool results carry `ttlMs` and `cacheScope` in protocol `_meta`, separate from their JSON
content. Explicitly release-pinned content uses 86,400,000 ms/public; unpinned caDSR content (including
empty results) uses 3,600,000 ms/public. Implicit NCIt calls and computed matching results use 0/private.
Explicit-release cross-domain joins use the shorter 3,600,000 ms/public because the joined caDSR
sources are unpinned; `expand_cohort`, which reads EVS only, keeps 86,400,000 ms/public.
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
| `not_found` | The requested release has no concept with that code, or `index-sample` named codes the release does not contain (nothing was indexed); Form-by-ID alone also reads HTTP 200 with `form: null` and `apiResponse.type` E as `not_found`, after the id is validated ([cadsr-forms](docs/upstream/cadsr.md#cadsr-forms)) | `identifiers` |
| `release_not_available` | EVS did not name exactly one latest NCIt release for the channel (`requested` names the requested channel; optional `found` lists versions when several rows were returned), EVS no longer serves the pinned release, or a release resource names an unserved version or one with absent/ambiguous channel metadata; a caDSR matching pin that is not listed upstream | `requested`, `source`, `found` |
| `release_mismatch` | The local index holds a different release than the requested one, or EVS served a concept of another release than the one requested | `requested`, `served` (a list of releases), `source` |
| `upstream_unavailable` | EVS could not be reached or kept failing after the retries, rejected the request, or returned something unusable: a malformed, HTML or masked-error body, or a 404 from any request other than a single-concept lookup (check `NCI_SI_EVS_BASE_URL`); an empty unfiltered terminology listing, with its actual `status` and `attempts` | `surface`, `status`, `attempts`, `retryAfter` (`status` and `retryAfter` where known) |
| `timeout` | Every attempt at an upstream request timed out (`NCI_SI_TIMEOUT_SECONDS`; `NCI_SI_MATCH_TIMEOUT_SECONDS` for caDSR matching); timeouts are errors, never empty results | `surface`, `seconds`, `attempts` |
| `bound_exceeded` | An EVS response exceeds `NCI_SI_EVS_MAX_RESPONSE_BYTES`, the request budget is exhausted before a graph is available, or hierarchy page replay exhausts its request budget | `bound`, `limit`, `reached` (for response size, the limit plus one when EVS declared no length) |
| `capability_unavailable` | The requested terminology or operation is not supported yet, or an index resource has no active index; the MCP tool descriptions name the interim limits; a published caDSR matching pin, with capability `pinned matching`, because the matching APIs have no `registryRelease` field yet ([cadsr-match-parameters](docs/upstream/cadsr.md#cadsr-match-parameters)); no unpinned match is labelled pinned | `capability` |
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
