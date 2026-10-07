"""Command-line utilities for local development and MCP serving."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import replace
from typing import Any, TextIO

from .audit import emit
from .config import Settings, configure_logging
from .context import Context
from .errors import PlatformError, correlated, is_error_record, serialise, with_next_step
from .registry import SPECS, cli_arguments, invoke
from .server import create_mcp
from .transport import run_http
from .validation import TRANSPORTS

logger = logging.getLogger(__name__)

# What can go wrong while opening the index, loading the embedding model or
# importing the optional MCP package: environment problems, not bugs.
_STARTUP_ERRORS = (RuntimeError, ValueError, OSError)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nci-si-mcp")
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser("serve", help="Run the MCP server")
    serve.add_argument("--transport", choices=sorted(TRANSPORTS), default=None)
    for spec in SPECS:
        if spec.command:
            command = subcommands.add_parser(spec.command, help=spec.description.splitlines()[0])
            command.set_defaults(operation=spec.operation)
            for flags, options in cli_arguments(spec):
                command.add_argument(*flags, **options)
    return parser


def _print_result(value: dict[str, Any], stream: TextIO) -> int:
    """Print a result as JSON and return the exit code: 1 for an error record."""

    print(json.dumps(value, indent=2, sort_keys=True), file=stream)
    return 1 if is_error_record(value) else 0


def _run(context: Context, args: argparse.Namespace) -> dict[str, Any]:
    arguments = vars(args).copy()
    arguments.pop("command")
    operation = arguments.pop("operation")
    return invoke(context, operation, **arguments)


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


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    if args.command == "serve" and args.transport is not None:
        settings = replace(settings, transport=args.transport)
    return settings


def _serve(settings: Settings, context: Context) -> int:
    if settings.transport == "streamable-http":
        run_http(settings, context)
    else:
        if settings.http_auth_mode == "required":
            raise ValueError("Required HTTP authentication cannot run over trusted-local stdio")
        create_mcp(settings, context=context).run()
    return 0


def _main(args: argparse.Namespace) -> int:
    # The MCP server speaks its protocol on stdout, so its failures go to stderr.
    serve = args.command == "serve"
    errors = sys.stderr if serve else sys.stdout
    try:
        settings = _settings(args)
    except ValueError as exc:
        return _print_result(serialise(_configuration_error(exc)), errors)
    configure_logging(settings.log_level)
    try:
        context = Context(settings)
        if serve:
            return _serve(settings, context)
    except _STARTUP_ERRORS as exc:
        emit(logger, logging.DEBUG, "startup_failed", errorType=type(exc).__name__)
        message = with_next_step(f"{type(exc).__name__}: {exc}", "Fix the cause named and rerun.")
        return _print_result(serialise(PlatformError("internal_error", message)), errors)
    return _print_result(_run(context, args), sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
