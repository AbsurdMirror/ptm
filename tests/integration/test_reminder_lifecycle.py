"""Full reminder lifecycle: create/update/cancel/list + dispatch delivered path."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ptm.app import PtmApp
from ptm.timeutil import to_utc_iso


def test_reminder_crud_and_delivered_ack(app: PtmApp) -> None:
    mid = app.call("ptm_capture", {"content": "提醒关联", "request_id": "rl-m"})["data"]["id"]
    future = to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=2))
    past = to_utc_iso(datetime.now(timezone.utc) - timedelta(minutes=1))

    created = app.call(
        "ptm_reminder",
        {
            "action": "create",
            "title": "未来提醒",
            "remind_at": future,
            "matter_id": mid,
            "request_id": "rl-c1",
        },
    )
    assert created["ok"]
    rid = created["data"]["reminder"]["id"]
    ver = created["data"]["reminder"]["version"]

    got = app.call("ptm_reminder", {"action": "get", "reminder_id": rid})
    assert got["ok"] and got["data"]["reminder"]["title"] == "未来提醒"

    updated = app.call(
        "ptm_reminder",
        {
            "action": "update",
            "reminder_id": rid,
            "expected_version": ver,
            "remind_at": past,
            "title": "已到期提醒",
            "request_id": "rl-u1",
        },
    )
    assert updated["ok"]
    assert updated["data"]["reminder"]["version"] == ver + 1

    listed = app.call("ptm_reminder", {"action": "list", "status": "pending", "limit": 50})
    assert listed["ok"]
    assert any(i["id"] == rid for i in listed["data"]["items"])

    # plan-linked reminder returned on plan update
    plan = app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": mid,
            "plan_type": "time_block",
            "start_at": to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=3)),
            "end_at": to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=4)),
            "request_id": "rl-p",
        },
    )
    pid = plan["data"]["plan"]["id"]
    rem2 = app.call(
        "ptm_reminder",
        {
            "action": "create",
            "title": "跟计划",
            "remind_at": past,
            "plan_id": pid,
            "request_id": "rl-c2",
        },
    )
    assert rem2["ok"]
    moved = app.call(
        "ptm_plan",
        {
            "action": "update",
            "plan_id": pid,
            "expected_version": plan["data"]["plan"]["version"],
            "start_at": to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=5)),
            "end_at": to_utc_iso(datetime.now(timezone.utc) + timedelta(hours=6)),
            "request_id": "rl-p-u",
        },
    )
    assert moved["ok"]
    assert any(r["id"] == rem2["data"]["reminder"]["id"] for r in moved["data"]["linked_reminders"])

    claimed = app.call("ptm_reminder_dispatch", {"action": "claim_due", "limit": 20, "request_id": "rl-claim"})
    assert claimed["ok"]
    match = next(c for c in claimed["data"]["claimed"] if c["id"] == rid)
    ack_sub = app.call(
        "ptm_reminder_dispatch",
        {
            "action": "ack",
            "reminder_id": rid,
            "claim_token": match["claim_token"],
            "ack_status": "submitted",
            "request_id": "rl-sub",
        },
    )
    assert ack_sub["ok"]
    with app.connection() as conn:
        row = conn.execute(
            "SELECT status, submitted_at, delivered_at FROM reminder WHERE id=?", (rid,)
        ).fetchone()
        assert row["status"] == "submitted"
        assert row["delivered_at"] is None

    # cancel the other pending/claimed reminder
    r2 = rem2["data"]["reminder"]
    # may already be claimed; cancel via reminder tool needs current version
    with app.connection() as conn:
        cur = conn.execute("SELECT version, status FROM reminder WHERE id=?", (r2["id"],)).fetchone()
    if cur["status"] in {"pending", "failed", "claimed"}:
        # if claimed, cancel still allowed via reminder.cancel
        cancelled = app.call(
            "ptm_reminder",
            {
                "action": "cancel",
                "reminder_id": r2["id"],
                "expected_version": cur["version"],
                "request_id": "rl-cancel",
            },
        )
        assert cancelled["ok"]
        with app.connection() as conn:
            assert (
                conn.execute("SELECT status FROM reminder WHERE id=?", (r2["id"],)).fetchone()[0]
                == "cancelled"
            )

    # deliver path on a fresh due reminder
    due3 = app.call(
        "ptm_reminder",
        {"action": "create", "title": "送达", "remind_at": past, "request_id": "rl-c3"},
    )
    c3 = app.call("ptm_reminder_dispatch", {"action": "claim_due", "limit": 10, "request_id": "rl-c3c"})
    m3 = next(x for x in c3["data"]["claimed"] if x["id"] == due3["data"]["reminder"]["id"])
    delivered = app.call(
        "ptm_reminder_dispatch",
        {
            "action": "ack",
            "reminder_id": m3["id"],
            "claim_token": m3["claim_token"],
            "ack_status": "delivered",
            "request_id": "rl-del",
        },
    )
    assert delivered["ok"]
    with app.connection() as conn:
        row = conn.execute(
            "SELECT status, delivered_at, submitted_at FROM reminder WHERE id=?", (m3["id"],)
        ).fetchone()
        assert row["status"] == "delivered"
        assert row["delivered_at"] is not None
        assert row["submitted_at"] is not None
