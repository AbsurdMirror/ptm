"""Concurrent writers and large-ish data smoke."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from ptm.app import PtmApp
from ptm.config import Settings


def test_concurrent_captures_unique_ids(settings: Settings) -> None:
    def worker(i: int) -> str:
        app = PtmApp(Settings.from_env(settings.data_dir))
        r = app.call(
            "ptm_capture",
            {"content": f"并发事项-{i}", "request_id": f"conc-cap-{i}"},
        )
        assert r["ok"], r
        return r["data"]["id"]

    ids: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(worker, i) for i in range(24)]
        for f in as_completed(futs):
            ids.append(f.result())
    assert len(ids) == 24
    assert len(set(ids)) == 24
    app = PtmApp(Settings.from_env(settings.data_dir))
    with app.connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM matter WHERE title LIKE '并发事项-%'").fetchone()[0]
        assert n == 24
        ev = conn.execute(
            "SELECT COUNT(*) FROM event WHERE event_type='matter.created' AND request_id LIKE 'conc-cap-%'"
        ).fetchone()[0]
        assert ev == 24


def test_largeish_query_and_agenda_smoke(app: PtmApp) -> None:
    # create 60 backlog matters + some plans
    for i in range(60):
        r = app.call(
            "ptm_capture",
            {"content": f"bulk-{i:03d}", "request_id": f"bulk-{i}"},
        )
        assert r["ok"]
        if i < 15:
            mid = r["data"]["id"]
            p = app.call(
                "ptm_plan",
                {
                    "action": "create",
                    "matter_id": mid,
                    "plan_type": "time_budget",
                    "estimated_minutes": 25,
                    "request_id": f"bulk-p-{i}",
                },
            )
            assert p["ok"]

    agenda = app.call(
        "ptm_agenda",
        {"date": "2026-12-10", "timezone": "UTC", "include_backlog": True, "limit": 20},
    )
    assert agenda["ok"]
    assert len(agenda["data"]["backlog_candidates"]) == 20
    assert agenda["data"]["backlog_candidates_has_more"] is True

    q = app.call(
        "ptm_query",
        {
            "entity": "matter",
            "filters": {"all": [{"field": "title", "op": ">=", "value": "bulk-"}]},
            "limit": 50,
            "include_total": True,
            "order_by": [{"field": "created_at", "direction": "asc"}],
        },
    )
    assert q["ok"]
    assert q["data"]["truncated"] is True
    assert q["data"]["total_count"] >= 60
    # enforce max limit
    too_big = app.call("ptm_query", {"entity": "matter", "limit": 200})
    assert too_big["ok"]
    capped = app.call("ptm_query", {"entity": "matter", "limit": 201})
    assert capped["ok"] is False
