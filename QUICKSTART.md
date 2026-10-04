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

A call of `ncit_lookup` with `{"code": "C4817"}` answers, abridged:

```json
{
  "code": "C4817",
  "preferred_name": "Ewing Sarcoma",
  "terminology": "ncit",
  "release_version": "26.09d",
  "release_date": "2026-09-28",
  "source": "live_evs",
  "evidence": {
    "semantic_types": ["Neoplastic Process"],
    "definitions": [{"source": "NCI", "type": "DEFINITION", "definition": "A small round cell tumor that lacks ..."}]
  }
}
```

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
| `NCI_SI_EVS_BASE_URL` | `https://api-evsrest.nci.nih.gov` | EVS endpoint (`http` or `https`) |
| `NCI_SI_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| `NCI_SI_EVS_MAX_ATTEMPTS` | `3` | Request attempts, 1 to 10 |
| `NCI_SI_EVS_RETRY_BACKOFF_SECONDS` | `0.25` | Initial exponential backoff; a single wait is capped at 60 seconds |
| `NCI_SI_EVS_MAX_RESPONSE_BYTES` | `10485760` | Maximum accepted EVS response, up to 1 GiB |
| `NCI_SI_INDEX_BATCH_SIZE` | `100` | Codes per EVS indexing request |
| `NCI_SI_LOG_LEVEL` | `INFO` | Stderr diagnostic level |

## Build a small local index

The full NCIt concept universe is large. Start with a known sample:

```bash
python -m nci_si_mcp.cli index-sample C3262 C2991 C40704 C153397 C116938
python -m nci_si_mcp.cli search "kinase inhibition"
python -m nci_si_mcp.cli evaluate
```

`index-sample` adds concepts to the index while the monthly release stays the
same. After a new monthly release, the next `index-sample` replaces the index
with the concepts it names. `search` only sees what has been indexed; no MCP
tool builds the index.

Until the index is rebuilt after a new monthly release, `search` keeps serving
the old release (named in `release_version`), and `lookup` fails with
`release_mismatch` for every code unless `--live-only` is given.

Search and lookup hide the full EVS `raw` payload by default to keep MCP context
compact. Use `--include-raw` only for debugging:

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
settings, delete `nci_si.sqlite3` in the data directory and run `index-sample`
again.

## Usage examples

Each example gives what a user asks, the call a client makes for it, and the result. The calls
are exact. The results are captured from a live run on 2026-10-03 against EVS release 26.09d,
shortened where marked …; the search example needs the five sample concepts of the previous section in the index.

### Look up a concept

User prompt: "What is the NCI Thesaurus concept C4817?"

Call:

```json
{"tool": "ncit_lookup", "arguments": {"code": "C4817"}}
```

Result:

```json
{
  "code": "C4817",
  "preferred_name": "Ewing Sarcoma",
  "source_vocabulary": "NCI Thesaurus",
  "terminology": "ncit",
  "release_version": "26.09d",
  "release_date": "2026-09-28",
  "retrieved_at": "2026-10-03T22:47:35.948616Z",
  "source": "live_evs",
  "evidence": {
    "definitions": [
      {
        "definition": "A malignant neoplasm of the bone, or the soft tissue adjacent to bone, that is comprised of primitive neuroectodermal cells.",
        "type": "ALT_DEFINITION",
        "source": "NICHD"
      },
      {
        "definition": "A small round cell tumor that lacks morphologic, immunohistochemical, and electron microscopic evidence of neuroectodermal differentiation. It represents one of the two ends of the spectrum called Ewing sarcoma/peripheral neuroectodermal tumor. It affects mostly males under age 20, and it can occur in soft tissue or bone. Pain and the presence of a mass are the most common clinical symptoms.",
        "type": "DEFINITION",
        "source": "NCI"
      },
      {
        "definition": "A type of cancer that forms in bone or soft tissue.",
        "type": "ALT_DEFINITION",
        "source": "NCI-GLOSS"
      }
    ],
    "synonyms": [
      {
        "name": "Ewing Sarcoma",
        "term_type": "PT",
        "type": "FULL_SYN",
        "source": "Cellosaurus"
      },
      {
        "name": "Ewing Sarcoma",
        "term_type": "PT",
        "type": "FULL_SYN",
        "source": "CPTAC"
      },
      {
        "name": "Ewing Sarcoma",
        "term_type": "PT",
        "type": "FULL_SYN",
        "source": "CTRP"
      },
      {
        "…": "13 more items omitted"
      }
    ],
    "semantic_types": [
      "Neoplastic Process"
    ],
    "contributing_sources": [
      "Cellosaurus",
      "CPTAC",
      "CTRP",
      "GDC",
      "HemOnc",
      "MedDRA",
      "NICHD"
    ]
  }
}
```

### Search the local index

User prompt: "Which of the indexed concepts are about kinase inhibition?"

Call:

```json
{"tool": "ncit_search", "arguments": {"query": "kinase inhibition", "mode": "hybrid", "limit": 5}}
```

Result:

```json
{
  "query": "kinase inhibition",
  "mode": "hybrid",
  "release_version": "26.09d",
  "hits": [
    {
      "concept": {
        "code": "C40704",
        "preferred_name": "Receptor Tyrosine Kinase Inhibition",
        "source_vocabulary": "NCI Thesaurus",
        "terminology": "ncit",
        "release_version": "26.09d",
        "release_date": "2026-09-28",
        "retrieved_at": "2026-10-03T22:47:33.896612Z",
        "source": "active_cache",
        "evidence": {
          "contributing_sources": [],
          "definitions": [
            {
              "definition": "A process that negatively regulates the intracellular catalytic activities that originate with the binding of a tyrosine kinase-associated transmembrane receptor with its cognate ligand. This process is involved in regulation of signaling related to cellular division, cellular differentiation and morphogenesis.",
              "source": "NCI",
              "type": "DEFINITION"
            }
          ],
          "semantic_types": [
            "Physiologic Function"
          ],
          "synonyms": [
            {
              "name": "Receptor Tyrosine Kinase Inhibition",
              "source": "NCI",
              "term_type": "PT",
              "type": "FULL_SYN"
            },
            {
              "name": "Tyrosine Kinase Receptor Inhibition",
              "source": "NCI",
              "term_type": "SY",
              "type": "FULL_SYN"
            },
            {
              "name": "Receptor Tyrosine Kinase Inhibition",
              "source": null,
              "term_type": null,
              "type": "Preferred_Name"
            }
          ]
        }
      },
      "score": 0.969251231258575,
      "rank": 1,
      "score_components": {
        "bm25": 0.9440931477428637,
        "vector": 1.0
      }
    },
    {
      "concept": {
        "code": "C153397",
        "preferred_name": "In Vitro Kinase Inhibitor Assay",
        "source_vocabulary": "NCI Thesaurus",
        "terminology": "ncit",
        "release_version": "26.09d",
        "release_date": "2026-10-03T22:47:33.896588Z",
        "retrieved_at": "2026-10-03T22:47:33.896588Z",
        "source": "active_cache",
        "evidence": {
          "contributing_sources": [
            "CTRP"
          ],
          "definitions": [
            {
              "definition": "Any of various laboratory assay methods designed to measure the kinase inhibition activity of a test compound.",
              "source": "NCI",
              "type": "DEFINITION"
            }
          ],
          "semantic_types": [
            "Laboratory Procedure"
          ],
          "synonyms": [
            {
              "name": "In Vitro Kinase Inhibitor Assay",
              "source": "CTRP",
              "term_type": "DN",
              "type": "FULL_SYN"
            },
            {
              "name": "In Vitro Kinase Inhibition Assay",
              "source": "NCI",
              "term_type": "SY",
              "type": "FULL_SYN"
            },
            {
              "name": "In Vitro Kinase Inhibitor Assay",
              "source": "NCI",
              "term_type": "PT",
              "type": "FULL_SYN"
            }
          ]
        }
      },
      "score": 0.9560088666814356,
      "rank": 2,
      "score_components": {
        "bm25": 1.0,
        "vector": 0.9022419259587456
      }
    },
    {
      "concept": {
        "code": "C116938",
        "preferred_name": "CDK4/6 Inhibition",
        "source_vocabulary": "NCI Thesaurus",
        "terminology": "ncit",
        "release_version": "26.09d",
        "release_date": "2026-09-28",
        "retrieved_at": "2026-10-03T22:47:33.896569Z",
        "source": "active_cache",
        "evidence": {
          "contributing_sources": [
            "GDC"
          ],
          "definitions": [
            {
              "definition": "Inhibition of cyclin-dependent kinases 4 and 6 pathway activity to prevent proliferation of cancer cells and tumor growth.",
              "source": "NCI",
              "type": "DEFINITION"
            }
          ],
          "semantic_types": [
            "Therapeutic or Preventive Procedure"
          ],
          "synonyms": [
            {
              "name": "CDK4/6 Inhibition",
              "source": "NCI",
              "term_type": "PT",
              "type": "FULL_SYN"
            },
            {
              "name": "Cyclin-Dependent Kinases 4 and 6 Inhibition",
              "source": "NCI",
              "term_type": "SY",
              "type": "FULL_SYN"
            }
          ]
        }
      },
      "score": 0.33446213441111494,
      "rank": 3,
      "score_components": {
        "bm25": 0.0,
        "vector": 0.7432491875802554
      }
    },
    {
      "…": "2 more items omitted"
    }
  ],
  "retrieved_at": "2026-10-03T22:47:35.952544Z"
}
```

### List the subtypes of a concept

User prompt: "Which concepts are the direct subtypes of Neoplasm?"

Call:

```json
{"tool": "ncit_traverse", "arguments": {"start_codes": ["C3262"], "direction": "out", "max_depth": 1, "edge_types": ["child"]}}
```

Result:

```json
{
  "start_codes": [
    "C3262"
  ],
  "release_version": "26.09d",
  "nodes": [
    {
      "code": "C3262",
      "preferred_name": "Neoplasm",
      "terminology": "ncit",
      "release_version": "26.09d",
      "source_vocabulary": "NCI Thesaurus"
    },
    {
      "code": "C4741",
      "preferred_name": "Neoplasm by Morphology",
      "terminology": "ncit",
      "release_version": "26.09d",
      "source_vocabulary": "NCI Thesaurus"
    },
    {
      "code": "C3263",
      "preferred_name": "Neoplasm by Site",
      "terminology": "ncit",
      "release_version": "26.09d",
      "source_vocabulary": "NCI Thesaurus"
    },
    {
      "code": "C7062",
      "preferred_name": "Neoplasm by Special Category",
      "terminology": "ncit",
      "release_version": "26.09d",
      "source_vocabulary": "NCI Thesaurus"
    }
  ],
  "edges": [
    {
      "source_code": "C3262",
      "target_code": "C4741",
      "edge_type": "child",
      "relationship_name": "is_a_child",
      "target_name": "Neoplasm by Morphology",
      "source_name": "Neoplasm"
    },
    {
      "source_code": "C3262",
      "target_code": "C3263",
      "edge_type": "child",
      "relationship_name": "is_a_child",
      "target_name": "Neoplasm by Site",
      "source_name": "Neoplasm"
    },
    {
      "source_code": "C3262",
      "target_code": "C7062",
      "edge_type": "child",
      "relationship_name": "is_a_child",
      "target_name": "Neoplasm by Special Category",
      "source_name": "Neoplasm"
    }
  ],
  "truncated": false,
  "max_depth": 1,
  "max_nodes": 200,
  "max_edges": 1000,
  "retrieved_at": "2026-10-03T22:47:37.361790Z",
  "unexpanded_codes": []
}
```

## MCP Tools

- `ncit_search`: text search over the locally indexed concepts.
- `ncit_lookup`: one concept from live EVS. When EVS is unreachable and the concept is in the local index, it is served from there and marked as a fallback.
- `ncit_traverse`: breadth-first walk over hierarchy, role, and association edges in live EVS.
- `ncit_release_info`: EVS API version, current monthly release, and local index status.
- `cadsr_status`: reports that caDSR search is not implemented.

Each tool description, as sent to MCP clients, states the contract in full.

`ncit_traverse` supports:

- `start_codes`
- `direction`: `out` (child, role, association), `in` (parent, inverse role, inverse association), or `both`
- `max_depth`, `max_nodes`, `max_edges`: clamped to 4, 1,000, and 5,000; the result reports the effective values
- `include_hierarchy`, `include_roles`, `include_associations`
- `relationship_names`: keep only edges with these names; hierarchy edges are named `is_a_parent`, `is_a_child`, and `is_a_descendant`
- `edge_types`: any of `parent`, `child`, `descendant`, `role`, `inverse_role`, `association`, `inverse_association`

`descendant` edges are followed only when named in `edge_types`, with direction
`out` or `both` and hierarchy included. They link each start code directly to
every descendant that EVS places within `max_depth` levels, using one EVS
request per start code. EVS gives a descendant one level, which can be deeper
than its shortest path, so `descendant` edges can miss concepts that a `child`
walk of the same depth reaches: up to a few percent at depth 2, and up to a
third at depth 3 or 4, depending on the concept (1,414 against 2,043 concepts
for C3262 at depth 4 in release 26.09d, but 2,914 against 2,968 for C12219).
Use `child` edges when every concept within `max_depth` is needed. Naming an
edge type that the direction or the include flags exclude is an
`invalid_request`.

The walk proceeds one depth at a time over all start codes, so nearer nodes
claim the limits first. `truncated` in the result means the node limit, the
edge limit, or the EVS response-size limit dropped something. In the last case
`unexpanded_codes` lists the concepts whose relations or descendants were too
large to read; raising `max_nodes` or `max_edges` does not help there, raising
`NCI_SI_EVS_MAX_RESPONSE_BYTES` does, and so can a smaller `max_depth` for
descendants.

## MCP Resources

- `nci-si://concept/ncit/{code}`: the result of `ncit_lookup` with default options.
- `nci-si://release/ncit/{version}`: `monthly`, `latest`, or `monthly-latest` return the full `ncit_release_info` report; the version of the current monthly release returns that release's record.
- `nci-si://index/ncit/{version}/manifest`: `active`, or the release the local index holds, returns its manifest; without an index the result is `{"active_index": null}`.

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
Arguments that the MCP schema or the CLI argument parser reject (a wrong type,
an unknown `mode`) are reported by those layers in their own format. An
unexpected exception is a bug and is not converted into a record.

Each message ends with the caller's next step. A query that matches nothing is
not an error: it returns its normal shape with an empty list. An answer that
EVS wraps in a success status but that is an error envelope, an error
`OperationOutcome` or an HTML page is `upstream_unavailable`.

| Code | Meaning | `details` |
| --- | --- | --- |
| `invalid_request` | An argument is missing, malformed, out of range, or contradicts another; CLI only: an environment variable is invalid | `parameter`, `reason` |
| `not_found` | The current monthly release has no concept with that code, or `index-sample` named codes the release does not contain (nothing was indexed) | `identifiers` |
| `release_not_available` | EVS did not report exactly one latest monthly NCIt release, or a resource names a release that is not the current one (or not the one the index holds) | `requested`, `source` |
| `release_mismatch` | The local index holds a different release than the current monthly one, or EVS served a concept of another release than the one requested | `requested`, `served` (a list of releases), `source` |
| `upstream_unavailable` | EVS could not be reached or kept failing after the retries, rejected the request, or returned something unusable: a malformed, HTML or masked-error body, or a 404 from any request other than a single-concept lookup (check `NCI_SI_EVS_BASE_URL`) | `surface`, `status`, `attempts`, `retryAfter` (`status` and `retryAfter` where known) |
| `timeout` | Every attempt at an EVS request timed out (`NCI_SI_TIMEOUT_SECONDS`) | `surface`, `seconds`, `attempts` |
| `bound_exceeded` | An EVS response was larger than `NCI_SI_EVS_MAX_RESPONSE_BYTES` and the call cannot proceed without it | `bound`, `limit`, `reached` (the limit plus one when EVS declared no length) |
| `capability_unavailable` | Defined by the specification; no tool returns it yet | `capability` |
| `cursor_expired` | Defined by the specification; no tool returns it yet | `cursorRelease`, `currentRelease` |
| `internal_error` | `search` or `evaluate` was called before an index was built, the index was built with other embedding settings than the runtime uses, SQLite could not open, read or write the index file named in the message, or (CLI only) the index, the embedding model or the MCP package could not be loaded at startup | none |

`ncit_release_info` and the `release-info` command succeed during an EVS outage:
the `evs_api` and `selected_monthly_release` fields then hold an error record
next to the local index manifest.
