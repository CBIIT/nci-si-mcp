"""Command-line utilities for local development and MCP serving."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .config import Settings
from .evaluation import evaluate_retrieval
from .server import run_stdio
from .service import NCISIService


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(prog="nci-si-mcp")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("serve", help="Run the FastMCP stdio server")
    subcommands.add_parser("release-info", help="Show EVS monthly release and index status")

    index_sample = subcommands.add_parser("index-sample", help="Index a small list of NCIt codes")
    index_sample.add_argument("codes", nargs="+")

    search = subcommands.add_parser("search", help="Search the active local NCIt index")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--mode", choices=["hybrid", "bm25", "vector"], default="hybrid")
    search.add_argument("--include-raw", action="store_true")

    lookup = subcommands.add_parser("lookup", help="Look up one NCIt concept")
    lookup.add_argument("code")
    lookup.add_argument("--live-only", action="store_true")
    lookup.add_argument("--include-raw", action="store_true")

    traverse = subcommands.add_parser("traverse", help="Traverse NCIt graph relationships")
    traverse.add_argument("start_codes", nargs="+")
    traverse.add_argument("--direction", choices=["in", "out", "both"], default="out")
    traverse.add_argument("--max-depth", type=int, default=2)
    traverse.add_argument("--max-nodes", type=int, default=200)
    traverse.add_argument("--no-hierarchy", action="store_true")
    traverse.add_argument("--no-roles", action="store_true")
    traverse.add_argument("--no-associations", action="store_true")
    traverse.add_argument("--relationship-name", action="append", dest="relationship_names")
    traverse.add_argument("--edge-type", action="append", dest="edge_types")

    subcommands.add_parser("evaluate", help="Evaluate BM25/vector/hybrid ranking on the built-in gold set")

    args = parser.parse_args()
    if args.command == "serve":
        run_stdio()
        return

    service = NCISIService(Settings.from_env())
    if args.command == "release-info":
        _print_json(service.release_info())
    elif args.command == "index-sample":
        _print_json(service.index_codes(args.codes))
    elif args.command == "search":
        _print_json(
            service.search(
                args.query,
                limit=args.limit,
                mode=args.mode,
                include_raw=args.include_raw,
            )
        )
    elif args.command == "lookup":
        _print_json(
            service.lookup(
                args.code,
                live_only=args.live_only,
                include_raw=args.include_raw,
            )
        )
    elif args.command == "traverse":
        _print_json(
            service.traverse(
                start_codes=args.start_codes,
                direction=args.direction,
                max_depth=args.max_depth,
                max_nodes=args.max_nodes,
                include_hierarchy=not args.no_hierarchy,
                include_roles=not args.no_roles,
                include_associations=not args.no_associations,
                relationship_names=args.relationship_names,
                edge_types=args.edge_types,
            )
        )
    elif args.command == "evaluate":
        _print_json(
            [
                result.to_dict()
                for result in evaluate_retrieval(service.index, service.embedding_provider)
            ]
        )


if __name__ == "__main__":
    main()
