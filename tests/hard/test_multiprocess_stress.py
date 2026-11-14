"""Multi-process (not thread) concurrency stress on same SQLite DB."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from ptm.app import PtmApp
from ptm.config import Settings
from ptm.database import initialize_database
from ptm.timeutil import to_utc_iso
from datetime import datetime, timedelta, timezone
from tests.hard.evidence import record_requirement

WORKER = r'''
import os, sys, time, uuid
from datetime import datetime, timedelta, timezone
from ptm.app import PtmApp
from ptm.config import Settings

data, n, wid = sys.argv[1], int(sys.argv[2]), sys.argv[3]
app = PtmApp(Settings.from_env(data))
created = []
for i in range(n):
    rid = f"mp-{wid}-{i}"
    r = app.call("ptm_capture", {"content": f"mp-{wid}-{i}", "request_id": rid})
    assert r["ok"], r
    mid = r["data"]["id"]
    created.append(mid)
    start = datetime.now(timezone.utc) + timedelta(minutes=i)
    p = app.call("ptm_plan", {
        "action": "create",
        "matter_id": mid,
        "plan_type": "time_budget",
        "estimated_minutes": 15,
        "request_id": f"mp-p-{wid}-{i}",
    })
    assert p["ok"], p
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat().replace("+00:00","Z")
    rem = app.call("ptm_reminder", {
        "action": "create",
        "title": f"r-{wid}-{i}",
        "remind_at": past,
        "matter_id": mid,
        "request_id": f"mp-r-{wid}-{i}",
    })
    assert rem["ok"], rem
# claim storm
app.call("ptm_reminder_dispatch", {"action": "claim_due", "limit": 50, "request_id": f"mp-c-{wid}-{time.time_ns()}"})
print(len(created))
'''


def test_multiprocess_writers_invariants(tmp_path: Path, evidence_dir: Path) -> None:
    data = tmp_path / "mp"
    settings = Settings.from_env(data)
    initialize_database(settings)
    per_proc = 40
    procs_n = 8
    script = tmp_path / "mp_worker.py"
    script.write_text(WORKER, encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"/home/ubuntu/.local/bin:{env.get('PATH','')}"
    env["PYTHONPATH"] = "/home/ubuntu/ptm/src"
    t0 = time.perf_counter()
    procs = []
    for i in range(procs_n):
        procs.append(
            subprocess.Popen(
                [sys.executable, str(script), str(data), str(per_proc), str(i)],
                cwd="/home/ubuntu/ptm",
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    counts = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        counts.append(int((out or "0").strip().splitlines()[-1]))
    elapsed = time.perf_counter() - t0
    expected = per_proc * procs_n

    conn = sqlite3.connect(str(settings.db_path))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        matters = conn.execute("SELECT COUNT(*) FROM matter").fetchone()[0]
        uniq = conn.execute("SELECT COUNT(DISTINCT id) FROM matter").fetchone()[0]
        plans = conn.execute("SELECT COUNT(*) FROM plan").fetchone()[0]
        reminders = conn.execute("SELECT COUNT(*) FROM reminder").fetchone()[0]
        bad_ver = conn.execute("SELECT COUNT(*) FROM matter WHERE version < 1").fetchone()[0]
        # claim tokens unique among claimed/submitted/delivered
        tok_dup = conn.execute(
            """
            SELECT claim_token, COUNT(*) FROM reminder
            WHERE claim_token IS NOT NULL
            GROUP BY claim_token HAVING COUNT(*) > 1
            """
        ).fetchall()
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()

    ops = expected * 3  # capture+plan+reminder approx
    ops_per_sec = ops / elapsed if elapsed else 0
    ok = (
        integrity == "ok"
        and matters == expected == uniq
        and plans == expected
        and reminders == expected
        and bad_ver == 0
        and not tok_dup
        and not fk
        and sum(counts) == expected
    )
    record_requirement(
        evidence_dir,
        "R-STRESS-MULTIPROCESS",
        status="proven" if ok else "failed",
        detail=(
            f"integrity={integrity} matters={matters}/{expected} plans={plans} "
            f"reminders={reminders} tok_dup={tok_dup} fk={fk} elapsed={elapsed:.2f}s"
        ),
        metrics={
            "processes": procs_n,
            "per_process": per_proc,
            "rows_matter": matters,
            "elapsed_sec": round(elapsed, 3),
            "approx_ops": ops,
            "ops_per_sec": round(ops_per_sec, 1),
        },
    )
    assert ok
