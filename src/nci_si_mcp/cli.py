"""Command-line utilities for local development and MCP serving."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from typing import Any, TextIO

from .config import Settings, configure_logging
from .errors import PlatformError, correlated, is_error_record, serialise, with_next_step
from .server import create_mcp
from .service import NCISIService
from .traversal import DEFAULT_MAX_DEPTH, DEFAULT_MAX_EDGES, DEFAULT_MAX_NODES
from .validation import SEARCH_MODES, TRAVERSAL_DIRECTIONS, TRAVERSAL_EDGE_TYPES

logger = logging.getLogger(__name__)

# What can go wrong while opening the index, loading the embedding model or
# importing the optional MCP package: environment problems, not bugs.
_STARTUP_ERRORS = (RuntimeError, ValueError, OSError)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nci-si-mcp")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("serve", help="Run the MCP stdio server")
    subcommands.add_parser(
        "release-info", help="Show the EVS release of the configured channel and index status"
    )

    index_sample = subcommands.add_parser("index-sample", help="Index a small list of NCIt codes")
    index_sample.add_argument("codes", nargs="+", help="NCIt codes such as C3262")

    search = subcommands.add_parser("search", help="Search the active local NCIt index")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10, help="number of hits, 1 to 100")
    search.add_argument("--mode", choices=sorted(SEARCH_MODES), default="hybrid")
    search.add_argument("--include-raw", action="store_true", help="add the full EVS payload")

    lookup = subcommands.add_parser("lookup", help="Look up one NCIt concept in live EVS")
    lookup.add_argument("code")
    lookup.add_argument(
        "--live-only",
        action="store_true",
        help="skip the index release check and the cache fallback",
    )
    lookup.add_argument("--include-raw", action="store_true", help="add the full EVS payload")

    traverse = subcommands.add_parser("traverse", help="Traverse NCIt graph relationships")
    traverse.add_argument("start_codes", nargs="+")
    traverse.add_argument("--direction", choices=sorted(TRAVERSAL_DIRECTIONS), default="out")
    traverse.add_argument(
        "--max-depth", type=int, default=DEFAULT_MAX_DEPTH, help="hops from a start code, at most 4"
    )
    traverse.add_argument("--max-nodes", type=int, default=DEFAULT_MAX_NODES, help="at most 1000")
    traverse.add_argument("--max-edges", type=int, default=DEFAULT_MAX_EDGES, help="at most 5000")
    traverse.add_argument("--no-hierarchy", action="store_true")
    traverse.add_argument("--no-roles", action="store_true")
    traverse.add_argument("--no-associations", action="store_true")
    traverse.add_argument(
        "--relationship-name",
        action="append",
        dest="relationship_names",
        help="keep only edges with this name; repeatable",
    )
    traverse.add_argument(
        "--edge-type",
        action="append",
        dest="edge_types",
        choices=sorted(TRAVERSAL_EDGE_TYPES),
        help="follow only this edge type; repeatable",
    )

    subcommands.add_parser(
        "evaluate",
        help="Evaluate BM25/vector/hybrid ranking on the built-in gold set",
    )
    return parser


def _print_result(value: dict[str, Any], stream: TextIO) -> int:
    """Print a result as JSON and return the exit code: 1 for an error record."""

    print(json.dumps(value, indent=2, sort_keys=True), file=stream)
    return 1 if is_error_record(value) else 0


def _run(service: NCISIService, args: argparse.Namespace) -> dict[str, Any]:
    """Call the service method that the parsed command names."""

    if args.command == "release-info":
        return service.release_info()
    if args.command == "index-sample":
        return service.index_codes(args.codes)
    if args.command == "search":
        return service.search(
            args.query,
            limit=args.limit,
            mode=args.mode,
            include_raw=args.include_raw,
        )
    if args.command == "lookup":
        return service.lookup(
            args.code,
            live_only=args.live_only,
            include_raw=args.include_raw,
        )
    if args.command == "traverse":
        return service.traverse(
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
    return service.evaluate()


def _configuration_error(exc: ValueError) -> PlatformError:
    """The invalid_request for a bad setting, naming the variable when the message does."""

    named = re.search(r"NCI_SI_[A-Z_]+", str(exc))
    details = {"parameter": named.group(), "reason": str(exc)} if named else {}
    message = with_next_step(str(exc), "Fix the environment variable and rerun.")
    return PlatformError("invalid_request", message, **details)


def main() -> int:
    args = build_parser().parse_args()
    # One correlation identifier for the whole command.
    with correlated():
        return _main(args)


def _main(args: argparse.Namespace) -> int:
    # The MCP server speaks its protocol on stdout, so its failures go to stderr.
    serve = args.command == "serve"
    errors = sys.stderr if serve else sys.stdout
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        return _print_result(serialise(_configuration_error(exc)), errors)
    configure_logging(settings.log_level)
    try:
        service = NCISIService(settings)
        mcp = create_mcp(settings, service=service) if serve else None
    except _STARTUP_ERRORS as exc:
        logger.debug("startup_failed", exc_info=True)
        message = with_next_step(f"{type(exc).__name__}: {exc}", "Fix the cause named and rerun.")
        return _print_result(serialise(PlatformError("internal_error", message)), errors)
    if mcp:
        mcp.run()
        return 0
    return _print_result(_run(service, args), sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
