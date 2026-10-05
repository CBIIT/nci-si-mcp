"""Snapshot schema and explicit migration of the legacy concatenated index."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .errors import IndexCompatibilityError, IndexStorageError
from .models import IndexManifest, NcitConcept

SCHEMA_VERSION = 5


def name_key(text: str) -> str:
    """Exact names ignore case and collapsed whitespace after Unicode NFC."""
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def concept_fields(concept: NcitConcept) -> list[tuple[str, str]]:
    """Embed identical text once; precedence is name, synonym, then definition."""
    candidates = [("name", concept.preferred_name)]
    candidates.extend(("synonym", item["name"]) for item in concept.evidence.get("synonyms", []))
    candidates.extend(
        ("definition", item["definition"]) for item in concept.evidence.get("definitions", [])
    )
    by_text: dict[str, str] = {}
    for kind, text in candidates:
        if text.strip():
            by_text.setdefault(text, kind)
    return [(kind, text) for text, kind in by_text.items()]


def fts_table(build_id: str) -> str:
    """Only internal hexadecimal ids can form SQL identifiers, never caller input."""
    if not re.fullmatch(r"[0-9a-f]{32}", build_id):
        raise ValueError("Invalid stored index build identifier")
    return f"fts_{build_id}"


def create_fts(conn: sqlite3.Connection, build_id: str) -> None:
    # Per-build FTS keeps an inactive snapshot from changing active BM25 statistics.
    conn.execute(
        f"CREATE VIRTUAL TABLE {fts_table(build_id)} USING fts5("
        "code UNINDEXED, kind UNINDEXED, text, tokenize = 'unicode61')"
    )


def create_schema(conn: sqlite3.Connection) -> None:
    statements = (
        "CREATE TABLE manifests (build_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
        "active INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'complete', "
        "CHECK (active = 0 OR state = 'complete'))",
        "CREATE UNIQUE INDEX manifests_single_active ON manifests(active) WHERE active = 1",
        "CREATE TABLE concepts (build_id TEXT NOT NULL, code TEXT NOT NULL, "
        "payload TEXT NOT NULL, name_key TEXT NOT NULL, PRIMARY KEY (build_id, code))",
        "CREATE INDEX concepts_name ON concepts(build_id, name_key)",
        "CREATE TABLE fields (id INTEGER PRIMARY KEY, build_id TEXT NOT NULL, "
        "code TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, vector TEXT NOT NULL)",
        "CREATE INDEX fields_concept ON fields(build_id, code)",
        "CREATE TABLE vector_lsh (build_id TEXT NOT NULL, field_id INTEGER NOT NULL, "
        "band INTEGER NOT NULL, bucket INTEGER NOT NULL, PRIMARY KEY (field_id, band))",
        "CREATE INDEX vector_lsh_lookup ON vector_lsh(build_id, band, bucket)",
    )
    for statement in statements:
        conn.execute(statement)


def migrate(conn: sqlite3.Connection) -> None:
    """Retain every legacy raw concept and manifest, without relabelling its vectors."""
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'manifests'").fetchone()
    if not exists:
        create_schema(conn)
        return
    conn.execute("ALTER TABLE manifests RENAME TO legacy_manifests")
    conn.execute("ALTER TABLE concepts RENAME TO legacy_concepts")
    for name in ("manifests_single_active", "vector_lsh_lookup"):
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    for name in ("concepts_fts", "vector_lsh"):
        conn.execute(f"DROP TABLE IF EXISTS {name}")
    create_schema(conn)
    rows = conn.execute("SELECT * FROM legacy_manifests ORDER BY release_version").fetchall()
    selected = _legacy_active(rows)
    for row in rows:
        _migrate_build(conn, row, selected)
    old_count = conn.execute("SELECT COUNT(*) FROM legacy_concepts").fetchone()[0]
    new_count = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    if old_count != new_count:
        raise IndexCompatibilityError(
            "Legacy index has concepts without a manifest; repair before migration"
        )
    conn.execute("DROP TABLE legacy_concepts")
    conn.execute("DROP TABLE legacy_manifests")


def _legacy_active(rows: list[sqlite3.Row]) -> str | None:
    active = [row["release_version"] for row in rows if row["active"]]
    return active[-1] if active else None


def _migrate_build(conn: sqlite3.Connection, row: sqlite3.Row, active: str | None) -> None:
    manifest = replace(
        IndexManifest.from_payload(json.loads(row["payload"])),
        build_id=uuid4().hex,
        needs_rebuild=True,
        active=row["release_version"] == active,
    )
    if manifest.embedding_dimensions is None:
        vector = conn.execute(
            "SELECT vector FROM legacy_concepts WHERE release_version = ? LIMIT 1",
            (row["release_version"],),
        ).fetchone()
        if vector:
            manifest = replace(manifest, embedding_dimensions=len(json.loads(vector[0])))
    conn.execute(
        "INSERT INTO manifests(build_id, payload, active) VALUES (?, ?, ?)",
        (manifest.build_id, json.dumps(manifest.to_dict()), int(manifest.active)),
    )
    for item in conn.execute(
        "SELECT code, payload FROM legacy_concepts WHERE release_version = ?",
        (row["release_version"],),
    ):
        payload = json.loads(item["payload"])
        conn.execute(
            "INSERT INTO concepts VALUES (?, ?, ?, ?)",
            (manifest.build_id, item["code"], item["payload"], name_key(payload["preferred_name"])),
        )


def delete_build(conn: sqlite3.Connection, build_id: str) -> None:
    for table in ("concepts", "fields", "vector_lsh", "manifests"):
        conn.execute(f"DELETE FROM {table} WHERE build_id = ?", (build_id,))  # noqa: S608
    conn.execute(f"DROP TABLE IF EXISTS {fts_table(build_id)}")


@contextmanager
def build_lease(directory: Path, build_id: str) -> Iterator[None]:
    """A separate SQLite lock distinguishes a running writer from a stale build.

    No lock on the index is held while embedding. SQLite releases this small
    sidecar lock even after process death, on every supported operating system.
    """
    fts_table(build_id)
    path = directory / f"build-{build_id}.sqlite3"
    lock = _open_lease(path)
    try:
        yield
    finally:
        lock.close()
        path.unlink(missing_ok=True)


def _open_lease(path: Path) -> sqlite3.Connection:
    lock = None
    try:
        lock = sqlite3.connect(str(path), timeout=0)
        lock.execute("BEGIN EXCLUSIVE")
    except sqlite3.Error as exc:
        if lock is not None:
            lock.close()
        if exc.sqlite_errorcode == sqlite3.SQLITE_BUSY:
            raise
        raise IndexStorageError(f"{exc} ({path})") from exc
    return lock


def clean_stale_builds(conn: sqlite3.Connection, directory: Path) -> None:
    """Called under the index write lock; a live builder keeps its private lease."""
    rows = conn.execute("SELECT build_id FROM manifests WHERE state = 'building'").fetchall()
    for row in rows:
        try:
            with build_lease(directory, row[0]):
                delete_build(conn, row[0])
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                raise
