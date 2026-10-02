# Quickstart

How to install and run the prototype server, what it serves, and how it fails. What this repository
is, and the status of each tool group, is in [README.md](README.md).

## Install

The project needs Python 3.13 or newer and is managed with [PDM](https://pdm-project.org).

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
[releases page](https://github.com/hniedner/nci_si_mcp/releases) lists the versions:

```bash
pip install "nci-si-mcp[server] @ git+https://github.com/hniedner/nci_si_mcp@vX.Y.Z"
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
`version_mismatch` for every code unless `--live-only` is given.

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

Every failure the service handles uses one envelope, and the process exit code
of the CLI is 1:

```json
{
  "isError": true,
  "error": "invalid_request",
  "message": "Search query must not be blank"
}
```

Some errors add a `details` object. MCP tool results carrying the envelope are
also flagged as errors at the protocol level, and a failed resource read is a
protocol error whose message is the envelope. Arguments that the MCP schema or
the CLI argument parser reject (a wrong type, an unknown `mode`) are reported by
those layers in their own format. An unexpected exception is a bug and is not
converted into an envelope.

| Code | Meaning |
| --- | --- |
| `invalid_request` | An argument is missing, malformed, out of range, or contradicts another |
| `concept_not_found` | The current monthly release has no concept with that code |
| `concepts_missing` | `index-sample` named codes the release does not contain; nothing was indexed |
| `version_mismatch` | The local index holds a different release than the current monthly one |
| `release_unresolved` | EVS did not report exactly one latest monthly NCIt release |
| `evs_unavailable` | EVS could not be reached, or kept failing after the retries |
| `evs_invalid_response` | EVS rejected the request or returned something unusable: an oversized or malformed response, a concept from another release than requested, or a 404 from any request other than a single-concept lookup (check `NCI_SI_EVS_BASE_URL`) |
| `no_active_index` | `search` or `evaluate` was called before an index was built |
| `index_incompatible` | The index was built with other embedding settings than the runtime uses |
| `index_storage_error` | SQLite could not open, read or write the index file named in the message |
| `release_not_active`, `index_not_active` | A resource was requested for a release that is not the current one |
| `invalid_configuration` | CLI only: an environment variable, named in the message, is invalid |
| `startup_failed` | CLI only: the index (unreadable, or written by a newer version), the embedding model, or the MCP package could not be loaded |

`ncit_release_info` and the `release-info` command succeed during an EVS outage:
the `evs_api` and `selected_monthly_release` fields then hold an error envelope
next to the local index manifest.
