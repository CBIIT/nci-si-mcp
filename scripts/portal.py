"""Import bounded local evidence and serve the read-only loopback validation companion."""

from __future__ import annotations

import argparse
from contextlib import suppress
from pathlib import Path

from scripts.portal_http import create_server
from scripts.portal_store import MAX_BUNDLE_BYTES, EvidenceStore, load_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path(".nci-si-portal/evidence.sqlite"))
    parser.add_argument("--retention", type=int, default=100)
    commands = parser.add_subparsers(dest="command", required=True)
    imported = commands.add_parser("import", help="Import a bound original evidence bundle")
    imported.add_argument("directory", type=Path)
    legacy = commands.add_parser("legacy", help="Retain old bytes as unverified, without verdicts")
    legacy.add_argument("report", type=Path)
    legacy.add_argument("--kind", choices=("acceptance", "benchmark"), required=True)
    serve = commands.add_parser(
        "serve", help="Serve only on 127.0.0.1; no login is required locally"
    )
    serve.add_argument("--port", type=int, default=8081)
    return parser


def _legacy(store: EvidenceStore, path: Path, kind: str) -> str:
    if path.is_symlink():
        raise ValueError("Legacy evidence cannot be a symlink")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BUNDLE_BYTES + 1)
    return store.import_legacy(raw, kind)


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    store = EvidenceStore(args.store, retention=args.retention)
    if args.command == "import":
        print(store.import_bundle(load_bundle(args.directory)))
    elif args.command == "legacy":
        print(_legacy(store, args.report, args.kind))
    else:
        _serve(store, args.port)


def _serve(store: EvidenceStore, port: int) -> None:
    with create_server(store, port=port) as server:
        print(f"Local validation: http://127.0.0.1:{server.server_port}/", flush=True)
        # Local Ctrl+C closes the listening socket through the outer context manager.
        with suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
