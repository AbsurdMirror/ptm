"""Broad domain coverage: stages, plans, executions, observations, events, archive."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ptm.app import PtmApp
from ptm.database import transaction
from ptm.services.matters import create_stage
from ptm.services.validation import would_create_category_cycle, would_create_stage_cycle
from ptm.timeutil import to_utc_iso


def _preview_submit(app: PtmApp, ops: list[dict], request_id: str) -> dict:
    preview = app.call("ptm_mutate", {"operations": ops, "dry_run": True})
    assert preview["ok"] is True, preview
    return app.call(
        "ptm_mutate",
        {
            "operations": ops,
            "preview_token": preview["data"]["preview_token"],
            "request_id": request_id,
        },
    )


def test_stage_recursive_tree_and_cross_matter_rejected(app: PtmApp) -> None:
    m1 = app.call("ptm_capture", {"content": "Matter A", "request_id": "dom-m1"})["data"]["id"]
    m2 = app.call("ptm_capture", {"content": "Matter B", "request_id": "dom-m2"})["data"]["id"]
    with app.connection() as conn:
        with transaction(conn):
            s1 = create_stage(conn, matter_id=m1, title="L1")
            s2 = create_stage(conn, matter_id=m1, title="L2", parent_stage_id=s1["id"])
            s3 = create_stage(conn, matter_id=m1, title="L3", parent_stage_id=s2["id"])
            assert s3["parent_stage_id"] == s2["id"]
            assert would_create_stage_cycle(conn, s1["id"], s3["id"]) is True
            # cross-matter parent rejected
            try:
                create_stage(conn, matter_id=m2, title="X", parent_stage_id=s1["id"])
                raised = False
            except Exception as exc:  # noqa: BLE001
                raised = True
                assert "same matter" in str(exc).lower() or "belong" in str(exc).lower()
            assert raised


def test_category_cycle_detection(app: PtmApp) -> None:
    with app.connection() as conn:
        cats = conn.execute("SELECT id, name FROM category WHERE parent_id IS NULL").fetchall()
        c_work = next(c for c in cats if c["name"] == "工作")["id"]
        # create child via mutate
    ops = [
        {
            "ref": "child",
            "action": "create",
            "entity": "category",
            "data": {"name": "子工作", "parent_id": c_work},
        }
    ]
    created = app.call("ptm_mutate", {"operations": ops, "request_id": "cat-child"})
    assert created["ok"]
    child_id = created["data"]["refs"]["child"]
    with app.connection() as conn:
        assert would_create_category_cycle(conn, c_work, child_id) is True
    # attempt to point parent at child via update (would cycle)
    bad_ops = [
        {
            "action": "update",
            "entity": "category",
            "id": c_work,
            "expected_version": 1,
            "data": {"parent_id": child_id},
        }
    ]
    preview = app.call("ptm_mutate", {"operations": bad_ops, "dry_run": True})
    assert preview["ok"] is False
    assert preview["error"]["code"] == "INVALID_ARGUMENT"


def test_plan_types_cancel_complete_and_conflicts(app: PtmApp) -> None:
    mid = app.call("ptm_capture", {"content": "计划冲突", "request_id": "plan-m"})["data"]["id"]
    start = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
    p1 = app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": mid,
            "plan_type": "time_block",
            "start_at": to_utc_iso(start),
            "end_at": to_utc_iso(start + timedelta(hours=1)),
            "estimated_minutes": 60,
            "flexibility": "flexible",
            "request_id": "plan-1",
        },
    )
    assert p1["ok"]
    p2 = app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": mid,
            "plan_type": "time_block",
            "start_at": to_utc_iso(start + timedelta(minutes=30)),
            "end_at": to_utc_iso(start + timedelta(hours=1, minutes=30)),
            "request_id": "plan-2",
        },
    )
    assert p2["ok"]
    assert len(p2["data"]["conflicts"]) >= 1
    # overlapping plan was NOT auto-cancelled
    with app.connection() as conn:
        statuses = [
            r["status"]
            for r in conn.execute(
                "SELECT status FROM plan WHERE matter_id=? ORDER BY created_at", (mid,)
            ).fetchall()
        ]
        assert statuses.count("planned") == 2

    budget = app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": mid,
            "plan_type": "time_budget",
            "estimated_minutes": 90,
            "request_id": "plan-budget",
        },
    )
    assert budget["ok"]
    assert budget["data"]["plan"]["start_at"] is None

    pid = p1["data"]["plan"]["id"]
    ver = p1["data"]["plan"]["version"]
    done = app.call(
        "ptm_plan",
        {"action": "complete", "plan_id": pid, "expected_version": ver, "request_id": "plan-done"},
    )
    assert done["ok"]
    cancel = app.call(
        "ptm_plan",
        {
            "action": "cancel",
            "plan_id": p2["data"]["plan"]["id"],
            "expected_version": p2["data"]["plan"]["version"],
            "request_id": "plan-cancel",
        },
    )
    assert cancel["ok"]
    with app.connection() as conn:
        rows = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM plan").fetchall()}
        assert rows[pid] == "completed"
        assert rows[p2["data"]["plan"]["id"]] == "cancelled"


def test_execution_precision_modes_and_planless(app: PtmApp) -> None:
    mid = app.call("ptm_capture", {"content": "执行精度", "request_id": "ex-m"})["data"]["id"]
    # planless approximate
    a = app.call(
        "ptm_record_execution",
        {
            "matter_id": mid,
            "duration_minutes": 40,
            "duration_precision": "approximate",
            "result": "partial",
            "note": "大约四十分钟",
            "request_id": "ex-a",
        },
    )
    assert a["ok"]
    # exact with matching window
    start = "2026-12-02T08:00:00Z"
    end = "2026-12-02T09:00:00Z"
    e = app.call(
        "ptm_record_execution",
        {
            "matter_id": mid,
            "start_at": start,
            "end_at": end,
            "duration_minutes": 60,
            "duration_precision": "exact",
            "result": "completed",
            "request_id": "ex-e",
        },
    )
    assert e["ok"]
    # unknown with note only
    u = app.call(
        "ptm_record_execution",
        {
            "matter_id": mid,
            "duration_precision": "unknown",
            "result": "unknown",
            "note": "做了一会儿但不记得多久",
            "request_id": "ex-u",
        },
    )
    # may require duration or note — accept ok or INVALID_ARGUMENT depending on validator
    if u["ok"]:
        with app.connection() as conn:
            row = conn.execute(
                "SELECT duration_minutes, start_at FROM execution WHERE id=?",
                (u["data"]["id"],),
            ).fetchone()
            assert row["start_at"] is None
            assert row["duration_minutes"] is None
    else:
        assert u["error"]["code"] == "INVALID_ARGUMENT"

    # exact mismatch rejected
    bad = app.call(
        "ptm_record_execution",
        {
            "matter_id": mid,
            "start_at": start,
            "end_at": end,
            "duration_minutes": 10,
            "duration_precision": "exact",
            "request_id": "ex-bad",
        },
    )
    assert bad["ok"] is False
    assert bad["error"]["code"] == "INVALID_ARGUMENT"

    # one plan many executions
    plan = app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": mid,
            "plan_type": "time_budget",
            "estimated_minutes": 120,
            "request_id": "ex-plan",
        },
    )
    pid = plan["data"]["plan"]["id"]
    for i in range(3):
        r = app.call(
            "ptm_record_execution",
            {
                "matter_id": mid,
                "plan_id": pid,
                "duration_minutes": 20,
                "duration_precision": "approximate",
                "result": "partial",
                "note": f"part-{i}",
                "request_id": f"ex-multi-{i}",
            },
        )
        assert r["ok"]
    with app.connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM execution WHERE plan_id=?", (pid,)).fetchone()["c"]
        assert n == 3
        mstatus = conn.execute("SELECT status FROM matter WHERE id=?", (mid,)).fetchone()["status"]
        assert mstatus != "completed"


def test_observation_sources_supersede_invalidate(app: PtmApp) -> None:
    mid = app.call("ptm_capture", {"content": "观察对象", "request_id": "obs-m"})["data"]["id"]
    user = app.call(
        "ptm_capture",
        {
            "kind": "observation",
            "content": "我喜欢早上学习",
            "related_id": mid,
            "observation_type": "preference",
            "source": "user",
            "request_id": "obs-u",
        },
    )
    assert user["ok"]
    oid = user["data"]["id"]
    ai = app.call(
        "ptm_mutate",
        {
            "operations": [
                {
                    "ref": "ai_pat",
                    "action": "create",
                    "entity": "observation",
                    "data": {
                        "type": "pattern",
                        "source": "ai",
                        "content": "用户常低估研究耗时",
                        "matter_id": mid,
                        "confidence": 0.4,
                        "evidence": [{"evidence_type": "note", "note": "sample"}],
                    },
                }
            ],
            "request_id": "obs-ai",
        },
    )
    assert ai["ok"]
    # supersede user preference
    with app.connection() as conn:
        ver = conn.execute("SELECT version FROM observation WHERE id=?", (oid,)).fetchone()[0]
    out = _preview_submit(
        app,
        [
            {
                "action": "set_status",
                "entity": "observation",
                "id": oid,
                "expected_version": ver,
                "data": {"status": "superseded"},
            }
        ],
        "obs-super",
    )
    assert out["ok"]
    ai_id = ai["data"]["refs"]["ai_pat"]
    with app.connection() as conn:
        ver2 = conn.execute("SELECT version FROM observation WHERE id=?", (ai_id,)).fetchone()[0]
    inv = _preview_submit(
        app,
        [
            {
                "action": "set_status",
                "entity": "observation",
                "id": ai_id,
                "expected_version": ver2,
                "data": {"status": "invalidated"},
            }
        ],
        "obs-inv",
    )
    assert inv["ok"]
    with app.connection() as conn:
        statuses = {
            r["id"]: (r["status"], r["source"])
            for r in conn.execute("SELECT id, status, source FROM observation").fetchall()
        }
        assert statuses[oid][0] == "superseded"
        assert statuses[oid][1] == "user"
        assert statuses[ai_id][0] == "invalidated"
        assert statuses[ai_id][1] == "ai"
        ev = conn.execute(
            "SELECT COUNT(*) AS c FROM observation_evidence WHERE observation_id=?",
            (ai_id,),
        ).fetchone()["c"]
        assert ev >= 1


def test_event_audit_and_persist_across_reconnect(app: PtmApp, settings) -> None:
    mid = app.call("ptm_capture", {"content": "审计持久化", "request_id": "evt-1"})["data"]["id"]
    app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": mid,
            "plan_type": "time_budget",
            "estimated_minutes": 30,
            "request_id": "evt-2",
        },
    )
    with app.connection() as conn:
        events = conn.execute(
            "SELECT event_type, entity_type, before_json, after_json, request_id FROM event ORDER BY occurred_at"
        ).fetchall()
        assert any(e["event_type"] == "matter.created" for e in events)
        assert any(e["event_type"] == "plan.created" for e in events)
        assert any(e["request_id"] == "evt-1" for e in events)
        # Event not writable via mutate
    bad = app.call(
        "ptm_mutate",
        {
            "operations": [
                {"action": "create", "entity": "event", "data": {"event_type": "x"}}  # type: ignore[typeddict-item]
            ],
            "request_id": "evt-bad",
        },
    )
    assert bad["ok"] is False

    # reconnect new app instance same data dir
    from ptm.config import Settings

    app2 = PtmApp(Settings.from_env(settings.data_dir))
    with app2.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM matter WHERE id=?", (mid,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] >= 2


def test_archive_matter_via_mutate(app: PtmApp) -> None:
    mid = app.call("ptm_capture", {"content": "归档事项", "request_id": "arc-m"})["data"]["id"]
    with app.connection() as conn:
        ver = conn.execute("SELECT version FROM matter WHERE id=?", (mid,)).fetchone()[0]
    out = _preview_submit(
        app,
        [{"action": "archive", "entity": "matter", "id": mid, "expected_version": ver, "data": {}}],
        "arc-1",
    )
    assert out["ok"]
    with app.connection() as conn:
        assert conn.execute("SELECT status FROM matter WHERE id=?", (mid,)).fetchone()[0] == "cancelled"
        # no physical delete
        assert conn.execute("SELECT COUNT(*) FROM matter WHERE id=?", (mid,)).fetchone()[0] == 1
