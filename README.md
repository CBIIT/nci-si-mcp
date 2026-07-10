# NCI SI MCP Server

EVS-first MVP for exposing NCI Thesaurus search, lookup, and graph traversal through a local `stdio` Model Context Protocol server.

## What Is Implemented

- Python/FastMCP server entrypoint with local `stdio` transport.
- EVS REST client for monthly NCIt release resolution, concept lookup, search, and traversal endpoints.
- Fail-closed monthly release selection: no fallback to weekly if monthly metadata is missing or ambiguous.
- SQLite-backed active-release cache and local concept index.
- Pure-Python BM25, vector, and hybrid ranking.
- Embedding provider abstraction:
  - deterministic hashing provider for local smoke tests and CI;
  - optional `sentence-transformers` provider for SapBERT, MiniLM, or internal models.
- Named relationship traversal filters for role/association edge names.
- caDSR adapter stub that reports reuse-discovery status without inventing CDE results.

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -e ".[test]"
python -m nci_si_mcp.cli release-info
```

The core CLI and tests support Python 3.9+. The MCP server dependency currently
requires Python 3.10+, so install the server extra in a Python 3.10+ environment.
Check first:

```bash
python --version
```

Then, only if it reports Python 3.10 or newer:

```bash
pip install -e ".[server,test]"
python -m nci_si_mcp.cli serve
```

If your machine only has Apple Python 3.9, install Python 3.10+ through an approved
NIH/NCI software channel, Homebrew, pyenv, or conda before using the MCP server.

For real embeddings:

```bash
pip install -e ".[embeddings,test]"
export NCI_SI_EMBEDDING_PROVIDER=sentence-transformers
export NCI_SI_EMBEDDING_MODEL=cambridgeltl/SapBERT-from-PubMedBERT-fulltext
```

## Build A Small Local Index

The full NCIt concept universe is large. Start with a known sample:

```bash
python -m nci_si_mcp.cli index-sample C3262 C2991 C40704 C153397 C116938
python -m nci_si_mcp.cli search "kinase inhibition"
python -m nci_si_mcp.cli evaluate
```

Search and lookup hide the full EVS `raw` payload by default to keep MCP context
compact. Use `--include-raw` only for debugging:

```bash
python -m nci_si_mcp.cli lookup C3262 --include-raw
```

Traversal can be tested from the terminal before using MCP:

```bash
python -m nci_si_mcp.cli traverse C3262 --max-depth 1 --edge-type role --relationship-name Disease_Has_Abnormal_Cell
```

The active local data directory defaults to `.nci-si-mcp/`. Override it with:

```bash
export NCI_SI_DATA_DIR=/path/to/data
```

## MCP Tools

- `ncit_search`
- `ncit_lookup`
- `ncit_traverse`
- `ncit_release_info`
- `cadsr_status`

`ncit_traverse` supports:

- `start_codes`
- `direction`
- `max_depth`
- `max_nodes`
- `include_hierarchy`
- `include_roles`
- `include_associations`
- `relationship_names`
- `edge_types`

## MCP Resources

- `nci-si://concept/ncit/{code}`
- `nci-si://release/ncit/{version}` where `version` is `monthly`, `latest`, `monthly-latest`, or the active monthly version.
- `nci-si://index/ncit/{version}/manifest` where `version` is `active` or the active monthly version.

## caDSR Reuse Spike

The caDSR adapter is intentionally non-fabricating. It reports `reuse_pending` until the project confirms reusable access paths or logic from CDE AI / FAIR Data Workbench, such as:

- CDE Match API endpoints
- caDSR schema and credential requirements
- existing BM25/vector indexes
- fine-tuned embedding models
- confidence scoring and ranking rules

## Tests

```bash
python -m unittest discover -s tests
```

`pytest` also works when installed:

```bash
python -m pytest
```

Live EVS tests are not enabled by default. The included tests use fixtures/fakes for deterministic validation of release selection, normalization, cache provenance, traversal filtering, and retrieval ranking.
