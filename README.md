# NCI SI MCP

[![CI](https://github.com/hniedner/nci-si-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/hniedner/nci-si-mcp/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](pyproject.toml)

The government-furnished prototype of the Model Context Protocol (MCP) server for the NCI
Semantic Infrastructure, and the acceptance suite it and its successors are measured by. Two
Statements of Work build on it: *EVS SOW v2.1* (terminology: NCI Thesaurus through EVS) and
*caDSR SOW v1.1* (metadata: common data elements through caDSR), with the cross-domain tools that
join them. It is a prototype, not a production service, and it never returns caDSR content it
did not retrieve.

## Try it

```bash
pdm install                  # Python 3.13+ and PDM
pdm run nci-si-mcp serve     # the server, over MCP stdio
pdm run acceptance           # the acceptance suite against it, on recorded upstream answers
```

[QUICKSTART.md](QUICKSTART.md) has the rest: connecting a client, settings, tools and errors.

## How the pieces fit

```mermaid
flowchart LR
    suite["Acceptance suite"] -- "MCP over stdio" --> server["Server under test"]
    server -- "fixture mode" --> fixtures["Fixture server<br/>recorded and crafted answers"]
    server -- "live mode" --> live["EVS · caDSR · Shared SI"]
    fixtures -. "request log" .-> suite
```

The suite tests the MCP tool surface of whatever server it starts, against fixtures or the live
services; it knows nothing of the server's code.

## Status

The [specification](docs/specification.md) names 29 tools in four groups.

| Group | Tools | Status |
|---|---|---|
| EVS tools | 12 | Four prototype tools stand in, in part, for five: `resolve_release`, `get_concept`, `search_concepts`, `get_concept_hierarchy`, `get_concept_neighborhood`. The required tools come in Phase 2 |
| caDSR tools | 10 | Not implemented; Phase 3 |
| Cross-domain tools | 4 | Not implemented; Phase 4 |
| Workflow tools | 3 | Not implemented; Phase 5 |

The acceptance suite comes first (Phase 0): its mechanics and the EVS fixture set are in place,
and the tests of each group follow. The
[milestones](https://github.com/hniedner/nci-si-mcp/milestones) track the phases.

## Start here

- **The EVS and caDSR teams**: [docs/specification.md](docs/specification.md) specifies the
  required tools and their behaviour (rendered from `spec/`); [docs/SPEC.md](docs/SPEC.md) plans
  the work, phase by phase;
  [acceptance/README.md](acceptance/README.md) runs the suite against your server and renders the
  per-tool report; the upstream requests the tools rest on, each with its operation and
  rationale, are listed for [EVS](acceptance/request-forms/evs.md), for
  [caDSR](acceptance/request-forms/cadsr.md) and for [both](acceptance/request-forms/all.md).
- **NCI reviewers and the suite's maintainers**: [acceptance/README.md](acceptance/README.md) and
  [acceptance/fixtures/README.md](acceptance/fixtures/README.md), where the fixtures come from.
- **Working on this repository**: [CONTRIBUTING.md](CONTRIBUTING.md) for the commands, standards
  and gates; [ARCHITECTURE.md](ARCHITECTURE.md) for how the server is built.

## License

[Apache License 2.0](LICENSE). The recorded NCI Thesaurus content in the fixtures is under its own
terms, stated in [acceptance/fixtures/README.md](acceptance/fixtures/README.md).
