"""CLI init/check/backup/restore/export and migration smoke."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from ptm.app import PtmApp
from ptm.cli import app as cli_app
from ptm.config import Settings
from ptm.database import connect, current_schema_version, initialize_database


runner = CliRunner()


def test_cli_init_check_backup_restore_export(tmp_path: Path) -> None:
    data = tmp_path / "data"
    backup = tmp_path / "b.sqlite3"
    export = tmp_path / "e.json"
    r = runner.invoke(cli_app, ["init", "--data-dir", str(data)])
    assert r.exit_code == 0
    assert (data / "ptm.sqlite3").exists()

    # seed a matter via app
    app = PtmApp(Settings.from_env(data))
    mid = app.call("ptm_capture", {"content": "cli-seed", "request_id": "cli-1"})["data"]["id"]

    check = runner.invoke(cli_app, ["check", "--data-dir", str(data)])
    assert check.exit_code == 0
    payload = json.loads(check.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["schema_version"] == 1
    assert payload["counts"]["matter"] >= 1

    b = runner.invoke(cli_app, ["backup", str(backup), "--data-dir", str(data)])
    assert b.exit_code == 0
    assert backup.exists()

    # mutate live db then restore
    app.call("ptm_capture", {"content": "after-backup", "request_id": "cli-2"})
    with app.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM matter").fetchone()[0] >= 2

    rest = runner.invoke(cli_app, ["restore", str(backup), "--data-dir", str(data)])
    assert rest.exit_code == 0
    app3 = PtmApp(Settings.from_env(data))
    with app3.connection() as conn:
        titles = [r[0] for r in conn.execute("SELECT title FROM matter").fetchall()]
        assert "cli-seed" in titles
        assert "after-backup" not in titles
        assert conn.execute("SELECT COUNT(*) FROM matter WHERE id=?", (mid,)).fetchone()[0] == 1

    ex = runner.invoke(cli_app, ["export", str(export), "--data-dir", str(data)])
    assert ex.exit_code == 0
    blob = json.loads(export.read_text(encoding="utf-8"))
    assert "matter" in blob and "event" in blob
    assert any(m["id"] == mid for m in blob["matter"])


def test_migration_idempotent_and_wal(tmp_path: Path) -> None:
    data = tmp_path / "mig"
    settings = Settings.from_env(data)
    initialize_database(settings)
    initialize_database(settings)  # second call no-op
    conn = connect(settings.db_path)
    try:
        assert current_schema_version(conn) == 1
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        n = conn.execute("SELECT COUNT(*) FROM category").fetchone()[0]
        assert n == 6
        # corrupt-looking second migration insert should not happen
        versions = conn.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0]
        assert versions == 1
    finally:
        conn.close()


def test_cli_check_missing_db(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    missing.mkdir()
    r = runner.invoke(cli_app, ["check", "--data-dir", str(missing)])
    assert r.exit_code != 0
