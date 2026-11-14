"""Hardening tests: concurrency, preview flow, lease reclaim, timezone, invalid input."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from ptm.app import PtmApp
from ptm.config import Settings
from ptm.database import transaction
from ptm.timeutil import day_bounds_utc, to_utc_iso


def test_optimistic_locking_concurrent_updates(app: PtmApp, settings: Settings) -> None:
    matter = app.call("ptm_capture", {"content": "并发锁", "request_id": "lock-m"})
    mid = matter["data"]["id"]
    with app.connection() as conn:
        ver = conn.execute("SELECT version FROM matter WHERE id=?", (mid,)).fetchone()[0]

    results: list[dict] = []

    def _worker(title: str) -> dict:
        local = PtmApp(Settings.from_env(settings.data_dir))
        ops = [
            {
                "action": "update",
                "entity": "matter",
                "id": mid,
                "expected_version": ver,
                "data": {"title": title},
            }
        ]
        preview = local.call("ptm_mutate", {"operations": ops, "dry_run": True})
        if not preview["ok"]:
            return preview
        return local.call(
            "ptm_mutate",
            {
                "operations": ops,
                "preview_token": preview["data"]["preview_token"],
                "request_id": f"lock-{title}",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_worker, f"t-{i}") for i in range(2)]
        for f in as_completed(futs):
            results.append(f.result())

    oks = [r for r in results if r.get("ok")]
    fails = [r for r in results if not r.get("ok")]
    assert len(oks) == 1
    assert len(fails) == 1
    assert fails[0]["error"]["code"] in {"VERSION_CONFLICT", "PREVIEW_EXPIRED"}
    with app.connection() as conn:
        row = conn.execute("SELECT version, title FROM matter WHERE id=?", (mid,)).fetchone()
        assert row["version"] == ver + 1
        assert row["title"].startswith("t-")


def test_mutate_dry_run_confirm_idempotent_replay(app: PtmApp) -> None:
    matter = app.call("ptm_capture", {"content": "预览提交", "request_id": "prev-m"})
    mid = matter["data"]["id"]
    with app.connection() as conn:
        ver = int(conn.execute("SELECT version FROM matter WHERE id=?", (mid,)).fetchone()[0])

    ops = [
        {
            "action": "update",
            "entity": "matter",
            "id": mid,
            "expected_version": ver,
            "data": {"title": "已确认标题"},
        }
    ]
    # confirmed alone must NOT authorize
    denied = app.call(
        "ptm_mutate",
        {"operations": ops, "confirmed": True, "request_id": "prev-denied"},
    )
    assert denied["ok"] is False
    assert denied["error"]["code"] == "CONFIRMATION_REQUIRED"

    preview = app.call("ptm_mutate", {"operations": ops, "dry_run": True})
    assert preview["ok"] is True
    token = preview["data"]["preview_token"]
    assert preview["data"]["confirmation_required"] is True
    with app.connection() as conn:
        title = conn.execute("SELECT title FROM matter WHERE id=?", (mid,)).fetchone()[0]
        assert title == "预览提交"  # dry_run left no write

    submit = app.call(
        "ptm_mutate",
        {
            "operations": ops,
            "preview_token": token,
            "request_id": "prev-sub",
        },
    )
    assert submit["ok"] is True
    with app.connection() as conn:
        row = conn.execute("SELECT title, version FROM matter WHERE id=?", (mid,)).fetchone()
        assert row["title"] == "已确认标题"
        assert row["version"] == ver + 1

    replay = app.call(
        "ptm_mutate",
        {
            "operations": ops,
            "preview_token": token,
            "request_id": "prev-sub",
        },
    )
    assert replay["ok"] is True
    assert replay["data"] == submit["data"]
    with app.connection() as conn:
        # still only one version bump
        assert conn.execute("SELECT version FROM matter WHERE id=?", (mid,)).fetchone()[0] == ver + 1
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM event WHERE entity_id=? AND event_type='matter.update'",
            (mid,),
        ).fetchone()["c"]
        assert count == 1


def test_reminder_lease_expiry_and_reclaim(app: PtmApp) -> None:
    past = to_utc_iso(datetime.now(timezone.utc) - timedelta(minutes=10))
    created = app.call(
        "ptm_reminder",
        {"action": "create", "title": "租约恢复", "remind_at": past, "request_id": "lease-c"},
    )
    rid = created["data"]["reminder"]["id"]

    claim1 = app.call(
        "ptm_reminder_dispatch",
        {"action": "claim_due", "limit": 5, "lease_seconds": 60, "request_id": "lease-1"},
    )
    assert claim1["ok"]
    match = next(c for c in claim1["data"]["claimed"] if c["id"] == rid)
    old_token = match["claim_token"]

    # Expire lease in DB
    expired = to_utc_iso(datetime.now(timezone.utc) - timedelta(seconds=5))
    with app.connection() as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE reminder SET lease_until=?, updated_at=? WHERE id=?",
                (expired, to_utc_iso(datetime.now(timezone.utc)), rid),
            )

    # Old token must not ack after expiry
    bad_ack = app.call(
        "ptm_reminder_dispatch",
        {
            "action": "ack",
            "reminder_id": rid,
            "claim_token": old_token,
            "ack_status": "delivered",
            "request_id": "lease-bad-ack",
        },
    )
    assert bad_ack["ok"] is False

    # Reclaim after expiry
    claim2 = app.call(
        "ptm_reminder_dispatch",
        {"action": "claim_due", "limit": 5, "lease_seconds": 120, "request_id": "lease-2"},
    )
    assert claim2["ok"]
    match2 = next(c for c in claim2["data"]["claimed"] if c["id"] == rid)
    assert match2["claim_token"] != old_token
    assert match2["attempt_count"] >= 2

    ack = app.call(
        "ptm_reminder_dispatch",
        {
            "action": "ack",
            "reminder_id": rid,
            "claim_token": match2["claim_token"],
            "ack_status": "failed",
            "error": "channel timeout",
            "request_id": "lease-ack",
        },
    )
    assert ack["ok"]
    with app.connection() as conn:
        row = conn.execute("SELECT status, delivered_at, last_error FROM reminder WHERE id=?", (rid,)).fetchone()
        assert row["status"] == "failed"
        assert row["delivered_at"] is None
        assert "timeout" in row["last_error"]


def test_timezone_agenda_day_bounds(app: PtmApp) -> None:
    # 2026-11-16 01:30 +08:00 == 2026-11-15 17:30Z — belongs to Shanghai Nov 16, not UTC Nov 15
    matter = app.call("ptm_capture", {"content": "时区计划", "request_id": "tz-m"})
    mid = matter["data"]["id"]
    start = "2026-11-16T01:30:00+08:00"
    end = "2026-11-16T02:30:00+08:00"
    plan = app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": mid,
            "plan_type": "time_block",
            "start_at": start,
            "end_at": end,
            "request_id": "tz-p",
        },
    )
    assert plan["ok"]

    sh = app.call("ptm_agenda", {"date": "2026-11-16", "timezone": "Asia/Shanghai"})
    utc = app.call("ptm_agenda", {"date": "2026-11-15", "timezone": "UTC"})
    assert any(p["id"] == plan["data"]["plan"]["id"] for p in sh["data"]["plans"])
    assert any(p["id"] == plan["data"]["plan"]["id"] for p in utc["data"]["plans"])

    # Wrong local day should miss it
    miss = app.call("ptm_agenda", {"date": "2026-11-15", "timezone": "Asia/Shanghai"})
    assert all(p["id"] != plan["data"]["plan"]["id"] for p in miss["data"]["plans"])

    bad_tz = app.call("ptm_agenda", {"date": "2026-11-16", "timezone": "Mars/Phobos"})
    assert bad_tz["ok"] is False
    assert bad_tz["error"]["code"] == "INVALID_ARGUMENT"

    start_b, end_b = day_bounds_utc("2026-11-16", "Asia/Shanghai")
    assert to_utc_iso(start_b) == "2026-11-15T16:00:00Z"
    assert to_utc_iso(end_b) == "2026-11-16T16:00:00Z"


def test_invalid_inputs_surface_codes(app: PtmApp) -> None:
    empty = app.call("ptm_capture", {"content": ""})
    assert empty["ok"] is False
    assert empty["error"]["code"] == "INVALID_ARGUMENT"

    unknown_tool_entity = app.call("ptm_query", {"entity": "matter", "limit": 201})
    assert unknown_tool_entity["ok"] is False
    assert unknown_tool_entity["error"]["code"] in {"INVALID_ARGUMENT", "LIMIT_EXCEEDED"}

    # limit 201 fails pydantic ge/le → INVALID_ARGUMENT
    assert unknown_tool_entity["error"]["code"] == "INVALID_ARGUMENT"

    no_plan = app.call(
        "ptm_plan",
        {"action": "create", "plan_type": "time_block", "request_id": "no-matter"},
    )
    assert no_plan["ok"] is False
    assert no_plan["error"]["code"] == "INVALID_ARGUMENT"

    dup = app.call("ptm_capture", {"content": "a", "request_id": "dup-x"})
    clash = app.call("ptm_capture", {"content": "b", "request_id": "dup-x"})
    assert dup["ok"] and clash["ok"] is False and clash["error"]["code"] == "DUPLICATE_REQUEST"
