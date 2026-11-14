"""Typer CLI for init/check/backup/restore/export and reminder poll."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

import typer

from ptm.app import make_app
from ptm.config import get_settings
from ptm.database import connect, current_schema_version, initialize_database, rows_to_dicts
from ptm.timeutil import utc_now_iso

app = typer.Typer(add_completion=False, no_args_is_help=True, help="PTM maintenance CLI")


@app.command()
def init(
    data_dir: Optional[Path] = typer.Option(None, help="Override PTM_DATA_DIR"),
) -> None:
    """Initialize data directory and SQLite schema."""
    settings = get_settings(data_dir)
    initialize_database(settings)
    typer.echo(f"initialized {settings.db_path}")


@app.command()
def check(
    data_dir: Optional[Path] = typer.Option(None, help="Override PTM_DATA_DIR"),
) -> None:
    """Check database integrity and schema version."""
    settings = get_settings(data_dir)
    if not settings.db_path.exists():
        typer.echo("database missing", err=True)
        raise typer.Exit(1)
    conn = connect(settings.db_path)
    try:
        version = current_schema_version(conn)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        ok = row[0] == "ok"
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("matter", "plan", "execution", "observation", "reminder", "event")
        }
        typer.echo(
            json.dumps(
                {"ok": ok, "schema_version": version, "integrity": row[0], "counts": counts},
                ensure_ascii=False,
            )
        )
        if not ok:
            raise typer.Exit(2)
    finally:
        conn.close()


@app.command()
def backup(
    dest: Path = typer.Argument(..., help="Backup file path"),
    data_dir: Optional[Path] = typer.Option(None),
) -> None:
    """Backup database using SQLite Backup API."""
    settings = get_settings(data_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = connect(settings.db_path)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
        typer.echo(f"backup written to {dest}")
    finally:
        dst.close()
        src.close()


@app.command()
def restore(
    src: Path = typer.Argument(..., help="Backup file"),
    data_dir: Optional[Path] = typer.Option(None),
) -> None:
    """Restore database from backup after integrity check."""
    if not src.exists():
        typer.echo("backup missing", err=True)
        raise typer.Exit(1)
    check_conn = sqlite3.connect(str(src))
    try:
        row = check_conn.execute("PRAGMA integrity_check").fetchone()
        if row[0] != "ok":
            typer.echo(f"backup integrity failed: {row[0]}", err=True)
            raise typer.Exit(2)
        tables = {
            r[0]
            for r in check_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {"schema_migration", "matter", "plan", "execution", "event"}
        if not required.issubset(tables):
            typer.echo(f"backup missing tables: {required - tables}", err=True)
            raise typer.Exit(2)
    finally:
        check_conn.close()
    settings = get_settings(data_dir)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.db_path.exists():
        stamp = utc_now_iso().replace(":", "")
        shutil.copy2(settings.db_path, settings.db_path.with_suffix(f".pre-restore-{stamp}.sqlite3"))
    shutil.copy2(src, settings.db_path)
    typer.echo(f"restored to {settings.db_path}")


@app.command("export")
def export_cmd(
    dest: Path = typer.Argument(..., help="JSON export path"),
    data_dir: Optional[Path] = typer.Option(None),
) -> None:
    """Export core business objects to JSON."""
    settings = get_settings(data_dir)
    conn = connect(settings.db_path)
    try:
        payload = {
            "exported_at": utc_now_iso(),
            "schema_version": current_schema_version(conn),
            "category": rows_to_dicts(conn.execute("SELECT * FROM category").fetchall()),
            "matter": rows_to_dicts(conn.execute("SELECT * FROM matter").fetchall()),
            "stage": rows_to_dicts(conn.execute("SELECT * FROM stage").fetchall()),
            "plan": rows_to_dicts(conn.execute("SELECT * FROM plan").fetchall()),
            "execution": rows_to_dicts(conn.execute("SELECT * FROM execution").fetchall()),
            "observation": rows_to_dicts(conn.execute("SELECT * FROM observation").fetchall()),
            "reminder": rows_to_dicts(conn.execute("SELECT * FROM reminder").fetchall()),
            "event": rows_to_dicts(conn.execute("SELECT * FROM event").fetchall()),
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"exported to {dest}")
    finally:
        conn.close()


@app.command("reminder-poll")
def reminder_poll(
    data_dir: Optional[Path] = typer.Option(None),
    limit: int = typer.Option(10),
) -> None:
    """Host-timer fallback: claim due reminders and print JSON (no external delivery)."""
    ptm = make_app(data_dir)
    result = ptm.call(
        "ptm_reminder_dispatch",
        {"action": "claim_due", "limit": limit},
    )
    typer.echo(json.dumps(result, ensure_ascii=False, default=str))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
