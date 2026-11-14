"""SQLite connection, migrations, and helpers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any, Iterator

from ptm.config import SCHEMA_VERSION, Settings, get_settings
from ptm.errors import StorageError
from ptm.models import SEED_CATEGORIES
from ptm.timeutil import new_id, utc_now_iso


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _migration_sql(name: str) -> str:
    return resources.files("ptm.migrations").joinpath(name).read_text(encoding="utf-8")


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migration'"
    ).fetchone()
    if not row:
        return 0
    ver = conn.execute("SELECT MAX(version) AS v FROM schema_migration").fetchone()
    return int(ver["v"] or 0)


def apply_migrations(conn: sqlite3.Connection) -> int:
    version = current_schema_version(conn)
    if version < 1:
        # executescript auto-commits; concurrent first-boot is tolerated.
        try:
            conn.executescript(_migration_sql("001_initial.sql"))
        except sqlite3.OperationalError:
            # Another connection may be creating the same objects.
            pass
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = current_schema_version(conn)
            if version < 1:
                now = utc_now_iso()
                try:
                    conn.execute(
                        "INSERT INTO schema_migration(version, applied_at) VALUES (?, ?)",
                        (1, now),
                    )
                    _seed_categories(conn, now)
                except sqlite3.IntegrityError:
                    # Lost the race to another initializer; continue.
                    pass
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        version = current_schema_version(conn)
    if version != SCHEMA_VERSION:
        raise StorageError(
            f"Unsupported schema version {version}; expected {SCHEMA_VERSION}",
            details={"version": version},
        )
    return version


def _seed_categories(conn: sqlite3.Connection, now: str) -> None:
    existing = conn.execute("SELECT COUNT(*) AS c FROM category").fetchone()["c"]
    if existing:
        return
    for name, description in SEED_CATEGORIES:
        try:
            conn.execute(
                """
                INSERT INTO category(id, name, description, parent_id, status, created_at, updated_at, version)
                VALUES (?, ?, ?, NULL, 'active', ?, ?, 1)
                """,
                (new_id(), name, description, now, now),
            )
        except sqlite3.IntegrityError:
            continue


def initialize_database(settings: Settings | None = None) -> Settings:
    settings = settings or get_settings()
    conn = connect(settings.db_path)
    try:
        apply_migrations(conn)
    finally:
        conn.close()
    return settings


@contextmanager
def db_session(settings: Settings | None = None) -> Iterator[sqlite3.Connection]:
    settings = settings or get_settings()
    conn = connect(settings.db_path)
    try:
        apply_migrations(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{k: r[k] for k in r.keys()} for r in rows]
