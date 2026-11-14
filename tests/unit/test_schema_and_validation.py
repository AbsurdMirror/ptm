"""Unit tests for schema init and validation rules."""

from __future__ import annotations

import pytest

from ptm.database import current_schema_version
from ptm.errors import InvalidArgument
from ptm.services.matters import create_stage
from ptm.services.validation import validate_execution_fields, validate_plan_fields, would_create_stage_cycle
from ptm.app import PtmApp


def test_seed_categories_and_schema_version(app: PtmApp, db_conn) -> None:
    assert current_schema_version(db_conn) == 1
    rows = db_conn.execute("SELECT name FROM category WHERE status='active' ORDER BY name").fetchall()
    names = [r["name"] for r in rows]
    assert set(names) == {"工作", "学习", "运动健康", "日常生活", "社交关系", "娱乐休闲"}


def test_time_block_requires_range() -> None:
    with pytest.raises(InvalidArgument):
        validate_plan_fields({"plan_type": "time_block", "start_at": None, "end_at": None})


def test_time_budget_requires_minutes() -> None:
    with pytest.raises(InvalidArgument):
        validate_plan_fields({"plan_type": "time_budget", "estimated_minutes": 0})


def test_exact_duration_consistency(app: PtmApp, db_conn) -> None:
    matter = app.call("ptm_capture", {"kind": "matter", "content": "consistency", "request_id": "u1"})
    mid = matter["data"]["id"]
    with pytest.raises(InvalidArgument):
        validate_execution_fields(
            {
                "matter_id": mid,
                "start_at": "2026-11-16T12:00:00Z",
                "end_at": "2026-11-16T14:00:00Z",
                "duration_minutes": 30,
                "duration_precision": "exact",
            },
            db_conn,
        )


def test_stage_cycle_rejected(app: PtmApp, db_conn) -> None:
    matter = app.call("ptm_capture", {"kind": "matter", "content": "recursive", "request_id": "u2"})
    mid = matter["data"]["id"]
    s1 = create_stage(db_conn, matter_id=mid, title="A")
    s2 = create_stage(db_conn, matter_id=mid, title="B", parent_stage_id=s1["id"])
    assert would_create_stage_cycle(db_conn, s1["id"], s2["id"]) is True
