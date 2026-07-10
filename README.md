# NCI SI MCP Server

EVS-first MVP for exposing NCI Thesaurus search, lookup, and graph traversal through a local `stdio` Model Context Protocol server.

## What Is Implemented

- Python/FastMCP server entrypoint with local `stdio` transport.
- EVS REST client for monthly NCIt release resolution, concept lookup, search, and traversal endpoints.
- Fail-closed monthly release selection: no fallback to weekly if monthly metadata is missing or ambiguous.
- Migration-backed SQLite active-release cache with transactional release activation.
- SQLite FTS5 BM25 plus a persistent locality-sensitive-hashing vector candidate index.
- BM25, vector, and hybrid ranking with embedding provider/model/dimension checks.
- Embedding provider abstraction:
  - deterministic hashing provider for local smoke tests and CI;
  - optional `sentence-transformers` provider for SapBERT, MiniLM, or internal models.
- Named relationship traversal filters with hard node, edge, and depth limits.
- Bounded EVS retries, response-size protection, batched indexing, and stderr logging.
- Shared input validation and structured errors across CLI and MCP surfaces.
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
python -m nci_si_mcp.cli traverse C3262 \
  --max-depth 1 \
  --max-edges 100 \
  --edge-type role \
  --relationship-name Disease_Has_Abnormal_Cell
```

The active local data directory defaults to `.nci-si-mcp/`. Override it with:

```bash
export NCI_SI_DATA_DIR=/path/to/data
```

Additional runtime controls:

| Variable | Default | Purpose |
| --- | --- | --- |
| `NCI_SI_EVS_BASE_URL` | NCI EVS production API | EVS endpoint |
| `NCI_SI_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| `NCI_SI_EVS_MAX_ATTEMPTS` | `3` | Bounded request attempts |
| `NCI_SI_EVS_RETRY_BACKOFF_SECONDS` | `0.25` | Initial exponential backoff |
| `NCI_SI_EVS_MAX_RESPONSE_BYTES` | `10485760` | Maximum accepted EVS response |
| `NCI_SI_INDEX_BATCH_SIZE` | `100` | Codes per EVS indexing request |
| `NCI_SI_LOG_LEVEL` | `INFO` | Stderr diagnostic level |

Indexes record their embedding provider, model, and dimensions. A runtime with
incompatible embedding settings must rebuild the index instead of silently
mixing vector spaces.

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
- `max_edges`
- `include_hierarchy`
- `include_roles`
- `include_associations`
- `relationship_names`
- `edge_types`

Invalid inputs and operational failures use one response envelope:

```json
{
  "isError": true,
  "error": "invalid_request",
  "message": "Search query must not be blank"
}
```

Release status remains available during EVS outages and includes nested error
details alongside any active local index manifest.

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

The dependency-free local path uses `unittest`:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

For coverage and quality gates:

```bash
pip install -e ".[test,dev]"
pytest
ruff check src tests
mypy src
```

Coverage is required to remain at or above 70%. GitHub Actions runs the quality
suite and compatibility tests on Python 3.9 through 3.12. Live EVS tests are not
enabled by default; network behavior is tested with deterministic fakes.
