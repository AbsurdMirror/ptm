"""Seven natural-language scenarios validated via DB state (V0.3 §15.5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ptm.app import PtmApp


def test_seven_scenarios_db_truth(app: PtmApp) -> None:
    # 1. 我想学习香山处理器
    s1 = app.call("ptm_capture", {"content": "我想学习香山处理器", "request_id": "s1"})
    assert s1["ok"]
    matter_id = s1["data"]["id"]
    with app.connection() as conn:
        row = conn.execute("SELECT status, title FROM matter WHERE id=?", (matter_id,)).fetchone()
        assert row["status"] == "backlog"
        assert "香山" in row["title"]

    # 2. 这周找个时间安排一下 -> time_budget plan
    s2 = app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": matter_id,
            "plan_type": "time_budget",
            "estimated_minutes": 120,
            "expected_result": "本周推进香山学习",
            "request_id": "s2",
        },
    )
    assert s2["ok"]
    plan_budget_id = s2["data"]["plan"]["id"]
    with app.connection() as conn:
        p = conn.execute("SELECT * FROM plan WHERE id=?", (plan_budget_id,)).fetchone()
        assert p["plan_type"] == "time_budget"
        assert p["start_at"] is None
        assert conn.execute("SELECT status FROM matter WHERE id=?", (matter_id,)).fetchone()["status"] == "active"

    # 3. 今天研究了两个多小时，还没搞明白
    s3 = app.call(
        "ptm_mutate",
        {
            "operations": [
                {
                    "ref": "ex",
                    "action": "create",
                    "entity": "execution",
                    "data": {
                        "matter_id": matter_id,
                        "plan_id": plan_budget_id,
                        "duration_minutes": 120,
                        "duration_precision": "approximate",
                        "result": "partial",
                        "note": "还没搞明白",
                    },
                },
                {
                    "action": "create",
                    "entity": "observation",
                    "data": {
                        "type": "feedback",
                        "source": "user",
                        "content": "还没搞明白",
                        "execution_id": "$ex",
                        "matter_id": matter_id,
                    },
                },
            ],
            "request_id": "s3",
        },
    )
    assert s3["ok"]
    with app.connection() as conn:
        ex = conn.execute(
            "SELECT * FROM execution WHERE id=?", (s3["data"]["refs"]["ex"],)
        ).fetchone()
        assert ex["duration_precision"] == "approximate"
        assert ex["start_at"] is None
        assert conn.execute("SELECT status FROM matter WHERE id=?", (matter_id,)).fetchone()["status"] != "completed"

    # 4. 明天记得提醒我洗衣服
    remind_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    laundry = app.call("ptm_capture", {"content": "洗衣服", "request_id": "s4-matter"})
    s4 = app.call(
        "ptm_reminder",
        {
            "action": "create",
            "title": "记得洗衣服",
            "remind_at": remind_at,
            "matter_id": laundry["data"]["id"],
            "request_id": "s4",
        },
    )
    assert s4["ok"]
    with app.connection() as conn:
        rem = conn.execute(
            "SELECT status, title FROM reminder WHERE id=?",
            (s4["data"]["reminder"]["id"],),
        ).fetchone()
        assert rem["status"] == "pending"

    # 5. 今天加班，原定计划往后推
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=1)
    end = start + timedelta(hours=1)
    block = app.call(
        "ptm_plan",
        {
            "action": "create",
            "matter_id": matter_id,
            "plan_type": "time_block",
            "start_at": start.isoformat().replace("+00:00", "Z"),
            "end_at": end.isoformat().replace("+00:00", "Z"),
            "estimated_minutes": 60,
            "request_id": "s5-create",
        },
    )
    assert block["ok"]
    pid = block["data"]["plan"]["id"]
    ver = block["data"]["plan"]["version"]
    new_start = start + timedelta(hours=2)
    new_end = end + timedelta(hours=2)
    s5 = app.call(
        "ptm_plan",
        {
            "action": "update",
            "plan_id": pid,
            "expected_version": ver,
            "start_at": new_start.isoformat().replace("+00:00", "Z"),
            "end_at": new_end.isoformat().replace("+00:00", "Z"),
            "request_id": "s5",
        },
    )
    assert s5["ok"]
    with app.connection() as conn:
        p = conn.execute("SELECT start_at, version FROM plan WHERE id=?", (pid,)).fetchone()
        assert p["version"] == ver + 1
        assert p["start_at"].startswith(new_start.strftime("%Y-%m-%dT%H:%M:%S"))
        ev = conn.execute(
            "SELECT before_json, after_json FROM event WHERE entity_id=? AND event_type='plan.update'",
            (pid,),
        ).fetchone()
        assert ev is not None
        assert "start_at" in ev["before_json"]

    # 6. 看看我最近一周学习和运动的情况
    s6 = app.call(
        "ptm_review",
        {
            "start_date": (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat(),
            "end_date": datetime.now(timezone.utc).date().isoformat(),
            "timezone": "UTC",
            "detail": "standard",
        },
    )
    assert s6["ok"]
    assert "execution_summary" in s6["data"]
    assert s6["data"]["execution_summary"]["note"] == "missing_records_are_not_treated_as_zero"

    # 7. 最近是不是经常低估研究任务的耗时？
    s7 = app.call(
        "ptm_stats",
        {
            "metric": "estimate_accuracy",
            "start_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z"),
            "end_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        },
    )
    assert s7["ok"]
    assert "excluded_reason" in s7["data"]
    assert "not netted" in s7["data"]["excluded_reason"] or "non_exact" in s7["data"]["excluded_reason"]
