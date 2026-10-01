"""Command-line utilities for local development and MCP serving."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .config import Settings, configure_logging
from .errors import error_response
from .evaluation import evaluate_retrieval
from .server import run_stdio
from .service import NCISIService
from .traversal import DEFAULT_MAX_EDGES


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nci-si-mcp")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("serve", help="Run the MCP stdio server")
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
    traverse.add_argument("--max-edges", type=int, default=DEFAULT_MAX_EDGES)
    traverse.add_argument("--no-hierarchy", action="store_true")
    traverse.add_argument("--no-roles", action="store_true")
    traverse.add_argument("--no-associations", action="store_true")
    traverse.add_argument("--relationship-name", action="append", dest="relationship_names")
    traverse.add_argument("--edge-type", action="append", dest="edge_types")

    subcommands.add_parser(
        "evaluate",
        help="Evaluate BM25/vector/hybrid ranking on the built-in gold set",
    )
    return parser


def _print_result(value: Any) -> int:
    _print_json(value)
    return 1 if isinstance(value, dict) and value.get("isError") else 0


def main() -> int:
    parser = build_parser()

    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        configure_logging(settings.log_level)
    except ValueError as exc:
        return _print_result(error_response("invalid_configuration", str(exc)))
    if args.command == "serve":
        run_stdio(settings)
        return 0

    try:
        service = NCISIService(settings)
    except (RuntimeError, ValueError) as exc:
        return _print_result(error_response("startup_failed", str(exc)))
    result: Any
    if args.command == "release-info":
        result = service.release_info()
    elif args.command == "index-sample":
        result = service.index_codes(args.codes)
    elif args.command == "search":
        result = service.search(
            args.query,
            limit=args.limit,
            mode=args.mode,
            include_raw=args.include_raw,
        )
    elif args.command == "lookup":
        result = service.lookup(
            args.code,
            live_only=args.live_only,
            include_raw=args.include_raw,
        )
    elif args.command == "traverse":
        result = service.traverse(
            start_codes=args.start_codes,
            direction=args.direction,
            max_depth=args.max_depth,
            max_nodes=args.max_nodes,
            max_edges=args.max_edges,
            include_hierarchy=not args.no_hierarchy,
            include_roles=not args.no_roles,
            include_associations=not args.no_associations,
            relationship_names=args.relationship_names,
            edge_types=args.edge_types,
        )
    elif args.command == "evaluate":
        try:
            result = [
                result.to_dict()
                for result in evaluate_retrieval(service.index, service.embedding_provider)
            ]
        except (RuntimeError, ValueError) as exc:
            result = error_response("evaluation_unavailable", str(exc))
    else:  # pragma: no cover - argparse enforces known commands
        result = error_response("invalid_request", "Unknown command")
    return _print_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
