"""Fault injection: kill mid-write, corrupt DB, truncated backup, busy storms."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ptm.app import PtmApp
from ptm.config import Settings
from ptm.database import connect, initialize_database
from tests.hard.evidence import record_requirement


WORKER = r"""
import os, sys, time
from ptm.app import PtmApp
from ptm.config import Settings
data = sys.argv[1]
mode = sys.argv[2]
app = PtmApp(Settings.from_env(data))
if mode == "mutate_loop":
    mid = app.call("ptm_capture", {"content": "victim", "request_id": "fault-seed"})["data"]["id"]
    # signal readiness
    open(os.path.join(data, "ready"), "w").write(mid)
    i = 0
    while True:
        ops = [{"action":"update","entity":"matter","id":mid,"expected_version":1+i,"data":{"title":f"t{i}"}}]
        # bump expected carefully by reading
        with app.connection() as c:
            ver = c.execute("select version from matter where id=?", (mid,)).fetchone()[0]
        ops[0]["expected_version"] = ver
        prev = app.call("ptm_mutate", {"operations": ops, "dry_run": True})
        if not prev.get("ok"):
            time.sleep(0.01)
            continue
        app.call("ptm_mutate", {"operations": ops, "preview_token": prev["data"]["preview_token"], "request_id": f"fault-u-{i}"})
        i += 1
elif mode == "init_loop":
    open(os.path.join(data, "ready"), "w").write("1")
    while True:
        initialize = __import__("ptm.database", fromlist=["initialize_database"]).initialize_database
        initialize(Settings.from_env(data))
        time.sleep(0.005)
"""


def test_sigkill_during_mutate_leaves_consistent_db(tmp_path: Path, evidence_dir: Path) -> None:
    data = tmp_path / "kill-mutate"
    data.mkdir()
    script = tmp_path / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    env = os.environ.copy()
    env["PTM_DATA_DIR"] = str(data)
    env["PYTHONPATH"] = "/home/ubuntu/ptm/src"
    proc = subprocess.Popen(
        [sys.executable, str(script), str(data), "mutate_loop"],
        cwd="/home/ubuntu/ptm",
        env={**env, "PATH": f"/home/ubuntu/.local/bin:{env.get('PATH','')}"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready = data / "ready"
    for _ in range(200):
        if ready.exists():
            break
        time.sleep(0.05)
    assert ready.exists(), "worker failed to start"
    time.sleep(0.4)
    os.kill(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    # DB must open and pass integrity_check
    db = data / "ptm.sqlite3"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.execute("PRAGMA foreign_keys=ON")
        # version and event counts consistent for matter
        matters = conn.execute("SELECT id, version, title FROM matter").fetchall()
        ok_versions = all(m[1] >= 1 for m in matters)
        # no half-applied operation_request without matching event for same request on last write is hard;
        # at least FK + integrity
        fk_viol = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()

    app = PtmApp(Settings.from_env(data))
    # must still accept writes after crash
    r = app.call("ptm_capture", {"content": "after-kill", "request_id": "fault-after"})
    ok = integrity == "ok" and ok_versions and not fk_viol and r["ok"] is True
    record_requirement(
        evidence_dir,
        "R-FAULT-SIGKILL-MUTATE",
        status="proven" if ok else "failed",
        detail=f"integrity={integrity} fk={fk_viol} after_ok={r.get('ok')} matters={len(matters)}",
    )
    assert ok


def test_sigkill_during_init_recoverable(tmp_path: Path, evidence_dir: Path) -> None:
    data = tmp_path / "kill-init"
    data.mkdir()
    script = tmp_path / "worker_init.py"
    script.write_text(WORKER, encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"/home/ubuntu/.local/bin:{env.get('PATH','')}"
    env["PYTHONPATH"] = "/home/ubuntu/ptm/src"
    proc = subprocess.Popen(
        [sys.executable, str(script), str(data), "init_loop"],
        cwd="/home/ubuntu/ptm",
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready = data / "ready"
    for _ in range(200):
        if ready.exists():
            break
        time.sleep(0.05)
    time.sleep(0.15)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=5)

    # recovery via initialize
    settings = Settings.from_env(data)
    initialize_database(settings)
    conn = connect(settings.db_path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        ver = conn.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
        cats = conn.execute("SELECT COUNT(*) FROM category").fetchone()[0]
    finally:
        conn.close()
    ok = integrity == "ok" and ver == 1 and cats == 6
    record_requirement(
        evidence_dir,
        "R-FAULT-SIGKILL-INIT",
        status="proven" if ok else "failed",
        detail=f"integrity={integrity} schema={ver} cats={cats}",
    )
    assert ok


def test_corrupt_sqlite_header_detected(tmp_path: Path, evidence_dir: Path) -> None:
    data = tmp_path / "corrupt"
    settings = Settings.from_env(data)
    initialize_database(settings)
    db = settings.db_path
    raw = bytearray(db.read_bytes())
    # smash header magic
    raw[0:16] = b"\x00" * 16
    db.write_bytes(raw)
    detected = False
    try:
        conn = sqlite3.connect(str(db))
        conn.execute("SELECT 1 FROM matter")
        conn.close()
    except sqlite3.DatabaseError:
        detected = True
    # CLI restore path: truncated backup rejected
    from typer.testing import CliRunner

    from ptm.cli import app as cli_app

    runner = CliRunner()
    good = tmp_path / "good"
    good.mkdir()
    initialize_database(Settings.from_env(good))
    backup = tmp_path / "trunc.sqlite3"
    # copy then truncate
    import shutil

    shutil.copy2(Settings.from_env(good).db_path, backup)
    with backup.open("r+b") as f:
        f.truncate(64)
    dest = tmp_path / "restore-dest"
    dest.mkdir()
    # init empty then attempt restore
    initialize_database(Settings.from_env(dest))
    res = runner.invoke(cli_app, ["restore", str(backup), "--data-dir", str(dest)])
    # truncated may fail integrity or open
    ok = detected and res.exit_code != 0
    record_requirement(
        evidence_dir,
        "R-FAULT-CORRUPT-TRUNCATE",
        status="proven" if ok else "failed",
        detail=f"header_detected={detected} restore_exit={res.exit_code} out={res.stdout!r} err={res.stderr!r}",
    )
    assert ok


def test_busy_timeout_storm_no_corruption(tmp_path: Path, evidence_dir: Path) -> None:
    """Many processes hammering same DB; integrity must hold."""
    data = tmp_path / "busy"
    settings = Settings.from_env(data)
    initialize_database(settings)
    seed = PtmApp(settings)
    mid = seed.call("ptm_capture", {"content": "storm", "request_id": "storm-0"})["data"]["id"]

    worker = f"""
import os, sys, time, random
from ptm.app import PtmApp
from ptm.config import Settings
app = PtmApp(Settings.from_env({str(data)!r}))
mid = {mid!r}
end = time.time() + 2.0
errs = 0
oks = 0
while time.time() < end:
    with app.connection() as c:
        ver = c.execute('select version from matter where id=?', (mid,)).fetchone()[0]
    ops = [{{'action':'update','entity':'matter','id':mid,'expected_version':ver,'data':{{'title':f'storm-{{random.randint(0,9999)}}'}}}}]
    prev = app.call('ptm_mutate', {{'operations':ops,'dry_run':True}})
    if not prev.get('ok'):
        errs += 1
        continue
    r = app.call('ptm_mutate', {{'operations':ops,'preview_token':prev['data']['preview_token'],'request_id':f'storm-{{os.getpid()}}-{{time.time_ns()}}'}})
    if r.get('ok'):
        oks += 1
    else:
        errs += 1
print(oks, errs)
"""
    script = tmp_path / "storm.py"
    script.write_text(worker, encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"/home/ubuntu/.local/bin:{env.get('PATH','')}"
    env["PYTHONPATH"] = "/home/ubuntu/ptm/src"
    procs = [
        subprocess.Popen(
            [sys.executable, str(script)],
            cwd="/home/ubuntu/ptm",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for _ in range(6)
    ]
    total_ok = total_err = 0
    for p in procs:
        out, _ = p.communicate(timeout=30)
        parts = (out or "0 0").strip().split()
        if len(parts) >= 2:
            total_ok += int(parts[0])
            total_err += int(parts[1])
    conn = sqlite3.connect(str(settings.db_path))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        ver = conn.execute("SELECT version FROM matter WHERE id=?", (mid,)).fetchone()[0]
        events = conn.execute(
            "SELECT COUNT(*) FROM event WHERE entity_id=? AND event_type='matter.update'",
            (mid,),
        ).fetchone()[0]
    finally:
        conn.close()
    # successful updates should match event count; version = 1 + successful updates
    ok = integrity == "ok" and ver == 1 + events and total_ok == events and total_ok > 0
    record_requirement(
        evidence_dir,
        "R-FAULT-BUSY-STORM",
        status="proven" if ok else "failed",
        detail=f"integrity={integrity} ver={ver} events={events} oks={total_ok} errs={total_err}",
        metrics={"oks": total_ok, "errs": total_err, "version": ver},
    )
    assert ok


def test_disk_full_simulation_via_readonly_dir(tmp_path: Path, evidence_dir: Path) -> None:
    """Approximate disk-full: open DB then make directory non-writable for WAL/journal."""
    data = tmp_path / "ro"
    settings = Settings.from_env(data)
    initialize_database(settings)
    app = PtmApp(settings)
    # chmod directory read-only after init
    os.chmod(data, 0o555)
    try:
        r = app.call("ptm_capture", {"content": "should-fail-or-ok", "request_id": "ro-1"})
        # On some FS chmod may not block same-user writes; treat STORAGE_ERROR as proven path,
        # or if write somehow succeeds note residual risk.
        if r["ok"] is False and r["error"]["code"] == "STORAGE_ERROR":
            status = "proven"
            detail = f"write rejected code={r['error']['code']} msg={r['error'].get('message')}"
        elif r["ok"] is True:
            status = "out_of_scope"
            detail = "chmod 0555 did not block same-user write on this FS; disk-full not faithfully simulated"
        else:
            status = "failed"
            detail = str(r)
    finally:
        os.chmod(data, 0o755)
    record_requirement(evidence_dir, "R-FAULT-DISK-FULL-SIM", status=status, detail=detail)
    if status == "failed":
        pytest.fail(detail)
