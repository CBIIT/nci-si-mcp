"""Plan installation targets and optional components."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass

COMPONENTS = ("server", "index", "documentation", "companion")
INSTALL_STAGES = ("check_prerequisites", "install", "configure", "verify")


@dataclass(frozen=True)
class InstallationPlan:
    """The target, components and ordered stages an installation will run."""

    target: str
    components: tuple[str, ...]
    stages: tuple[str, ...] = INSTALL_STAGES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nci-si-install",
        description=(
            "Plan installation of the NCI Semantic Infrastructure MCP server and optional "
            "components."
        ),
        color=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Print the ordered installation plan", color=False)
    plan.add_argument(
        "--target", choices=("local", "aws", "cloud-one"), required=True, help="installation target"
    )
    plan.add_argument(
        "--with-index",
        action="store_true",
        help="include an existing NCIt index and its embedding runtime",
    )
    plan.add_argument(
        "--with-documentation", action="store_true", help="include the static documentation site"
    )
    plan.add_argument(
        "--with-companion", action="store_true", help="include the validation companion"
    )
    return parser


def _components(args: argparse.Namespace) -> tuple[str, ...]:
    selected = [COMPONENTS[0]]
    for enabled, component in (
        (args.with_index, COMPONENTS[1]),
        (args.with_documentation, COMPONENTS[2]),
        (args.with_companion, COMPONENTS[3]),
    ):
        if enabled:
            selected.append(component)
    return tuple(selected)


def _blocked(args: argparse.Namespace) -> str | None:
    if args.target == "cloud-one":
        return "BLOCKED: the Cloud One target awaits the consultation decisions in #292."
    if args.target == "aws" and args.with_companion:
        return "BLOCKED: AWS validation administration awaits approved platform protection in #197."
    return None


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    blocked = _blocked(args)
    if blocked:
        sys.stderr.write(blocked + "\n")
        return 2
    plan = InstallationPlan(args.target, _components(args))
    record = {
        "target": plan.target,
        "components": plan.components,
        "stages": plan.stages,
    }
    sys.stdout.write(json.dumps(record, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
