# NCI SI MCP

The government-furnished prototype of the Model Context Protocol (MCP) server for the NCI
Semantic Infrastructure, and the acceptance suite it and its successors are measured by. Two
Statements of Work build on it: *EVS SOW v2.1* (terminology: NCI Thesaurus through EVS) and
*caDSR SOW v1.1* (metadata: common data elements through caDSR), with the cross-domain tools
that join them.

It is for the contractors who extend the server under those SOWs, for the NCI teams who review
their work, and for whoever maintains the suite. [docs/SPEC.md](docs/SPEC.md) specifies the work,
phase by phase.

## What is here

- `src/nci_si_mcp/`: the prototype server. It answers NCI Thesaurus search, lookup and traversal
  over the EVS REST API, served over MCP `stdio`.
- `acceptance/`: the behavioural acceptance suite. It tests the MCP tool surface of any server
  against recorded and crafted upstream answers; [acceptance/README.md](acceptance/README.md).
- `docs/SPEC.md`: how the prototype becomes the platform the SOWs describe.

## Status

The *MCP API Specification* names 29 tools in four groups. Today:

| Group | Tools | Status |
|---|---|---|
| A · Terminology (EVS) | 12 | The prototype's `ncit_search`, `ncit_lookup`, `ncit_traverse` and `ncit_release_info` cover parts of five of them; the required tools come in Phase 2 |
| B · Metadata (caDSR) | 10 | Not implemented: `cadsr_status` reports so and returns no data elements; Phase 3 |
| C · Cross-domain | 4 | Not implemented; Phase 4 |
| W · Workflow | 3 | Not implemented; Phase 5 |

The acceptance suite comes first (Phase 0): its mechanics and the EVS fixture set are in place,
and the tests of each group follow. The [milestones](https://github.com/hniedner/nci_si_mcp/milestones)
track the phases.

## Next

- [QUICKSTART.md](QUICKSTART.md): install and run the server, its settings, tools, resources and
  errors.
- [CONTRIBUTING.md](CONTRIBUTING.md): how to work on it, the commands, standards and gates.
- [ARCHITECTURE.md](ARCHITECTURE.md): how the server is built.

## License

[Apache License 2.0](LICENSE).
