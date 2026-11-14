"""Integration tests proving DB state for core V0.3 flows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ptm.app import PtmApp


def _matter_row(conn, matter_id: str):
    return conn.execute("SELECT * FROM matter WHERE id=?", (matter_id,)).fetchone()


def test_capture_plan_execution_persist(app: PtmApp) -> None:
    c1 = app.call(
        "ptm_capture",
        {"kind": "matter", "content": "我想学习香山处理器", "request_id": "cap-1"},
    )
    assert c1["ok"] is True
    matter_id = c1["data"]["id"]

    # idempotent replay
    c1b = app.call(
        "ptm_capture",
        {"kind": "matter", "content": "我想学习香山处理器", "request_id": "cap-1"},
    )
    assert c1b["data"]["id"] == matter_id
    with app.connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM matter WHERE title LIKE ?", ("%香山%",)).fetchone()["c"]
        assert count == 1

    start = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    plan = app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": matter_id,
            "plan_type": "time_block",
            "start_at": start.isoformat().replace("+00:00", "Z"),
            "end_at": end.isoformat().replace("+00:00", "Z"),
            "expected_result": "了解香山整体架构",
            "flexibility": "flexible",
            "request_id": "plan-1",
        },
    )
    assert plan["ok"] is True
    plan_id = plan["data"]["plan"]["id"]

    with app.connection() as conn:
        m = _matter_row(conn, matter_id)
        assert m["status"] == "active"
        assert m["version"] >= 2

    exe = app.call(
        "ptm_record_execution",
        {
            "matter_id": matter_id,
            "plan_id": plan_id,
            "duration_minutes": 120,
            "duration_precision": "approximate",
            "result": "partial",
            "note": "研究了两个多小时，尚未搞明白",
            "request_id": "exe-1",
        },
    )
    assert exe["ok"] is True
    assert exe["data"]["matter_auto_completed"] is False
    assert exe["data"]["duration_precision"] == "approximate"

    with app.connection() as conn:
        erow = conn.execute("SELECT * FROM execution WHERE id=?", (exe["data"]["id"],)).fetchone()
        assert erow["start_at"] is None
        assert erow["end_at"] is None
        assert erow["duration_minutes"] == 120
        m = _matter_row(conn, matter_id)
        assert m["status"] == "active"
        events = conn.execute(
            "SELECT event_type FROM event WHERE entity_id=? ORDER BY occurred_at",
            (exe["data"]["id"],),
        ).fetchall()
        assert any(e["event_type"] == "execution.created" for e in events)


def test_version_conflict_and_mutate_atomic(app: PtmApp) -> None:
    matter = app.call("ptm_capture", {"content": "原子批测", "request_id": "m-atom"})
    mid = matter["data"]["id"]

    # Updates require preview_token; stale expected_version fails after preview.
    ops_bad = [
        {
            "action": "update",
            "entity": "matter",
            "id": mid,
            "expected_version": 999,
            "data": {"title": "should-fail"},
        }
    ]
    need = app.call("ptm_mutate", {"operations": ops_bad, "request_id": "mut-need"})
    assert need["ok"] is False
    assert need["error"]["code"] == "CONFIRMATION_REQUIRED"

    preview = app.call("ptm_mutate", {"operations": ops_bad, "dry_run": True})
    # dry_run itself hits VERSION_CONFLICT while simulating
    assert preview["ok"] is False
    assert preview["error"]["code"] == "VERSION_CONFLICT"

    with app.connection() as conn:
        title = conn.execute("SELECT title FROM matter WHERE id=?", (mid,)).fetchone()["title"]
        assert title == "原子批测"

    ok = app.call(
        "ptm_mutate",
        {
            "operations": [
                {
                    "ref": "new_execution",
                    "action": "create",
                    "entity": "execution",
                    "data": {
                        "matter_id": mid,
                        "duration_minutes": 45,
                        "duration_precision": "approximate",
                        "result": "partial",
                        "note": "batch",
                    },
                },
                {
                    "ref": "new_feedback",
                    "action": "create",
                    "entity": "observation",
                    "data": {
                        "type": "feedback",
                        "source": "user",
                        "content": "今天研究得挺累",
                        "execution_id": "$new_execution",
                    },
                },
            ],
            "request_id": "mut-ok",
        },
    )
    assert ok["ok"] is True
    refs = ok["data"]["refs"]
    with app.connection() as conn:
        obs = conn.execute(
            "SELECT * FROM observation WHERE id=?", (refs["new_feedback"],)
        ).fetchone()
        assert obs["execution_id"] == refs["new_execution"]
        assert obs["content"] == "今天研究得挺累"


def test_agenda_review_stats_query(app: PtmApp) -> None:
    learning = None
    with app.connection() as conn:
        learning = conn.execute("SELECT id FROM category WHERE name='学习'").fetchone()["id"]

    matter = app.call(
        "ptm_capture",
        {"content": "运动半小时", "category_id": learning, "request_id": "ag-1"},
    )
    mid = matter["data"]["id"]
    start = "2026-11-16T12:00:00Z"
    end = "2026-11-16T13:00:00Z"
    app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": mid,
            "plan_type": "time_block",
            "start_at": start,
            "end_at": end,
            "estimated_minutes": 60,
            "request_id": "ag-plan",
        },
    )
    app.call(
        "ptm_record_execution",
        {
            "matter_id": mid,
            "duration_minutes": 90,
            "duration_precision": "exact",
            "start_at": start,
            "end_at": "2026-11-16T13:30:00Z",
            "result": "completed",
            "request_id": "ag-exe",
        },
    )

    agenda = app.call(
        "ptm_agenda",
        {"date": "2026-11-16", "timezone": "UTC", "include_backlog": True},
    )
    assert agenda["ok"] is True
    assert any(p["matter_id"] == mid for p in agenda["data"]["plans"])

    # execution.created_at is "now"; include today in review window
    today = datetime.now(timezone.utc).date().isoformat()
    review = app.call(
        "ptm_review",
        {"start_date": today, "end_date": today, "timezone": "UTC"},
    )
    assert review["ok"] is True
    assert review["data"]["execution_summary"]["count"] >= 1

    stats = app.call(
        "ptm_stats",
        {"metric": "time_by_category", "start_at": "2026-11-01T00:00:00Z", "end_at": "2026-12-01T00:00:00Z"},
    )
    assert stats["ok"] is True
    assert stats["data"]["sample_count"] >= 1

    q = app.call(
        "ptm_query",
        {
            "entity": "execution",
            "filters": {"all": [{"field": "duration_minutes", "op": ">=", "value": 60}]},
            "limit": 10,
            "include_total": True,
        },
    )
    assert q["ok"] is True
    assert q["data"]["total_count"] >= 1
    assert all(i["duration_minutes"] >= 60 for i in q["data"]["items"])


def test_reminder_state_machine(app: PtmApp) -> None:
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    created = app.call(
        "ptm_reminder",
        {
            "action": "create",
            "title": "明天记得提醒我洗衣服",
            "remind_at": past,
            "request_id": "rem-1",
        },
    )
    assert created["ok"] is True
    rid = created["data"]["reminder"]["id"]

    claimed = app.call("ptm_reminder_dispatch", {"action": "claim_due", "limit": 5, "request_id": "claim-1"})
    assert claimed["ok"] is True
    match = next(c for c in claimed["data"]["claimed"] if c["id"] == rid)
    token = match["claim_token"]

    with app.connection() as conn:
        row = conn.execute("SELECT status, claim_token FROM reminder WHERE id=?", (rid,)).fetchone()
        assert row["status"] == "claimed"
        assert row["claim_token"] == token

    ack = app.call(
        "ptm_reminder_dispatch",
        {
            "action": "ack",
            "reminder_id": rid,
            "claim_token": token,
            "ack_status": "submitted",
            "request_id": "ack-1",
        },
    )
    assert ack["ok"] is True
    with app.connection() as conn:
        row = conn.execute("SELECT status, submitted_at, delivered_at FROM reminder WHERE id=?", (rid,)).fetchone()
        assert row["status"] == "submitted"
        assert row["submitted_at"] is not None
        assert row["delivered_at"] is None  # do not invent delivered

    # invalid token cannot ack
    bad = app.call(
        "ptm_reminder_dispatch",
        {
            "action": "ack",
            "reminder_id": rid,
            "claim_token": "nope",
            "ack_status": "delivered",
            "request_id": "ack-bad",
        },
    )
    assert bad["ok"] is False


def test_schema_tool(app: PtmApp) -> None:
    schema = app.call("ptm_schema", {"entity": "plan"})
    assert schema["ok"] is True
    assert "plan_type" in schema["data"]["enums"]
    assert "create" in schema["data"]["operations"]
    all_schema = app.call("ptm_schema", {})
    assert len(all_schema["data"]["tools"]) == 11
