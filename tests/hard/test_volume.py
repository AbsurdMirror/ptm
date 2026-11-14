"""Data volume: thousands of rows; agenda/query/stats correctness + timings."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ptm.app import PtmApp
from ptm.config import Settings
from ptm.database import initialize_database, transaction
from ptm.timeutil import new_id, to_utc_iso, utc_now_iso
from tests.hard.evidence import record_requirement


def test_volume_thousands_rows_query_agenda_stats(
    tmp_path: Path, evidence_dir: Path
) -> None:
    data = tmp_path / "vol"
    settings = Settings.from_env(data)
    initialize_database(settings)
    app = PtmApp(settings)

    n_matters = 3000
    n_exec = 5000
    t_insert = time.perf_counter()
    with app.connection() as conn:
        with transaction(conn):
            now = utc_now_iso()
            cat = conn.execute("SELECT id FROM category WHERE name='学习'").fetchone()[0]
            matter_ids = []
            for i in range(n_matters):
                mid = new_id()
                matter_ids.append(mid)
                conn.execute(
                    """
                    INSERT INTO matter(id, category_id, title, description, status, priority,
                      due_at, estimated_effort_minutes, created_at, updated_at, completed_at, version)
                    VALUES (?, ?, ?, NULL, 'backlog', NULL, NULL, NULL, ?, ?, NULL, 1)
                    """,
                    (mid, cat, f"vol-matter-{i:05d}", now, now),
                )
            for i in range(n_exec):
                mid = matter_ids[i % n_matters]
                conn.execute(
                    """
                    INSERT INTO execution(id, matter_id, stage_id, plan_id, start_at, end_at,
                      duration_minutes, duration_precision, result, output, note,
                      created_at, updated_at, version)
                    VALUES (?, ?, NULL, NULL, NULL, NULL, ?, 'approximate', 'partial', NULL, 'vol', ?, ?, 1)
                    """,
                    (new_id(), mid, 15 + (i % 50), now, now),
                )
            # some plans for agenda day
            day = datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc)
            for i in range(200):
                mid = matter_ids[i]
                conn.execute(
                    """
                    INSERT INTO plan(id, matter_id, stage_id, plan_type, start_at, end_at,
                      estimated_minutes, expected_result, status, flexibility,
                      created_at, updated_at, version)
                    VALUES (?, ?, NULL, 'time_block', ?, ?, 60, NULL, 'planned', 'flexible', ?, ?, 1)
                    """,
                    (
                        new_id(),
                        mid,
                        to_utc_iso(day + timedelta(minutes=i)),
                        to_utc_iso(day + timedelta(minutes=i + 30)),
                        now,
                        now,
                    ),
                )
    insert_sec = time.perf_counter() - t_insert

    t_q = time.perf_counter()
    q = app.call(
        "ptm_query",
        {
            "entity": "execution",
            "filters": {"all": [{"field": "duration_minutes", "op": ">=", "value": 40}]},
            "limit": 50,
            "include_total": True,
            "order_by": [{"field": "duration_minutes", "direction": "desc"}],
        },
    )
    q_sec = time.perf_counter() - t_q
    assert q["ok"]
    assert q["data"]["truncated"] is True
    assert q["data"]["total_count"] is not None
    assert all(i["duration_minutes"] >= 40 for i in q["data"]["items"])

    t_a = time.perf_counter()
    agenda = app.call(
        "ptm_agenda",
        {"date": "2027-03-01", "timezone": "UTC", "include_backlog": True, "limit": 20},
    )
    a_sec = time.perf_counter() - t_a
    assert agenda["ok"]
    assert len(agenda["data"]["plans"]) == 20
    assert agenda["data"]["plans_has_more"] is True
    assert agenda["data"]["backlog_candidates_has_more"] is True

    t_s = time.perf_counter()
    stats = app.call(
        "ptm_stats",
        {
            "metric": "time_by_category",
            "start_at": to_utc_iso(datetime.now(timezone.utc) - timedelta(days=1)),
            "end_at": to_utc_iso(datetime.now(timezone.utc) + timedelta(days=1)),
        },
    )
    s_sec = time.perf_counter() - t_s
    assert stats["ok"]

    with app.connection() as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        mc = conn.execute("SELECT COUNT(*) FROM matter").fetchone()[0]
        ec = conn.execute("SELECT COUNT(*) FROM execution").fetchone()[0]
        pc = conn.execute("SELECT COUNT(*) FROM plan").fetchone()[0]

    ok = integrity == "ok" and mc == n_matters and ec == n_exec and pc == 200
    record_requirement(
        evidence_dir,
        "R-VOLUME-THOUSANDS",
        status="proven" if ok else "failed",
        detail=(
            f"integrity={integrity} matters={mc} exec={ec} plans={pc} "
            f"insert={insert_sec:.2f}s query={q_sec:.3f}s agenda={a_sec:.3f}s stats={s_sec:.3f}s "
            f"total_count={q['data']['total_count']}"
        ),
        metrics={
            "matters": mc,
            "executions": ec,
            "plans": pc,
            "insert_sec": round(insert_sec, 3),
            "query_sec": round(q_sec, 3),
            "agenda_sec": round(a_sec, 3),
            "stats_sec": round(s_sec, 3),
            "query_total_count": q["data"]["total_count"],
        },
    )
    assert ok
