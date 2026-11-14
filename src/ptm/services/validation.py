"""Cross-entity validation helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from ptm.errors import InvalidArgument, NotFound
from ptm.models import DURATION_MINUTE_TOLERANCE, PlanType
from ptm.repositories.common import get_by_id, require_by_id
from ptm.timeutil import parse_datetime


def ensure_stage_belongs_to_matter(
    conn: sqlite3.Connection, stage_id: str | None, matter_id: str | None
) -> dict[str, Any] | None:
    if not stage_id:
        return None
    stage = require_by_id(conn, "stage", stage_id)
    if matter_id and stage["matter_id"] != matter_id:
        raise InvalidArgument(
            "stage does not belong to matter",
            details={"stage_id": stage_id, "matter_id": matter_id},
        )
    return stage


def would_create_stage_cycle(
    conn: sqlite3.Connection, stage_id: str, new_parent_id: str | None
) -> bool:
    if not new_parent_id:
        return False
    if new_parent_id == stage_id:
        return True
    current = new_parent_id
    seen: set[str] = set()
    while current:
        if current == stage_id:
            return True
        if current in seen:
            return True
        seen.add(current)
        row = get_by_id(conn, "stage", current)
        if not row:
            break
        current = row["parent_stage_id"]
    return False


def would_create_category_cycle(
    conn: sqlite3.Connection, category_id: str, new_parent_id: str | None
) -> bool:
    if not new_parent_id:
        return False
    if new_parent_id == category_id:
        return True
    current = new_parent_id
    seen: set[str] = set()
    while current:
        if current == category_id:
            return True
        if current in seen:
            return True
        seen.add(current)
        row = get_by_id(conn, "category", current)
        if not row:
            break
        current = row["parent_id"]
    return False


def validate_plan_fields(data: dict[str, Any]) -> None:
    plan_type = data.get("plan_type")
    start_at = parse_datetime(data.get("start_at"))
    end_at = parse_datetime(data.get("end_at"))
    estimated = data.get("estimated_minutes")
    if plan_type == PlanType.TIME_BLOCK:
        if not start_at or not end_at:
            raise InvalidArgument("time_block requires start_at and end_at")
        if end_at <= start_at:
            raise InvalidArgument("end_at must be after start_at")
    elif plan_type == PlanType.TIME_BUDGET:
        if estimated is None or int(estimated) <= 0:
            raise InvalidArgument("time_budget requires positive estimated_minutes")
    if start_at and end_at and end_at <= start_at:
        raise InvalidArgument("end_at must be after start_at")


def validate_execution_fields(data: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    matter_id = data.get("matter_id")
    stage_id = data.get("stage_id")
    plan_id = data.get("plan_id")
    start_at = parse_datetime(data.get("start_at"))
    end_at = parse_datetime(data.get("end_at"))
    duration = data.get("duration_minutes")
    precision = data.get("duration_precision", "approximate")

    if plan_id:
        plan = require_by_id(conn, "plan", plan_id)
        if matter_id and matter_id != plan["matter_id"]:
            raise InvalidArgument("execution matter_id conflicts with plan")
        matter_id = matter_id or plan["matter_id"]
        if stage_id and plan["stage_id"] and stage_id != plan["stage_id"]:
            raise InvalidArgument("execution stage_id conflicts with plan")
        if not stage_id and plan["stage_id"]:
            stage_id = plan["stage_id"]
    if stage_id:
        stage = require_by_id(conn, "stage", stage_id)
        if matter_id and matter_id != stage["matter_id"]:
            raise InvalidArgument("stage does not belong to matter")
        matter_id = matter_id or stage["matter_id"]
    if matter_id:
        require_by_id(conn, "matter", matter_id)

    if start_at and end_at and end_at <= start_at:
        raise InvalidArgument("end_at must be after start_at")

    if precision == "exact":
        if start_at and end_at and duration is not None:
            delta = int((end_at - start_at).total_seconds() // 60)
            if abs(delta - int(duration)) > DURATION_MINUTE_TOLERANCE:
                raise InvalidArgument(
                    "exact duration inconsistent with start/end",
                    details={"computed_minutes": delta, "duration_minutes": duration},
                )
        if duration is None and not (start_at and end_at):
            raise InvalidArgument("exact precision requires duration or start/end")
    if precision == "approximate" and duration is None and not (start_at and end_at):
        # approximate may be only a note-driven duration; require at least one signal
        if not data.get("note") and not data.get("output"):
            raise InvalidArgument("approximate execution needs duration or note")

    # Never invent start/end for approximate/unknown
    if precision in {"approximate", "unknown"}:
        # keep provided values only; no fabrication
        pass

    return {
        **data,
        "matter_id": matter_id,
        "stage_id": stage_id,
        "plan_id": plan_id,
        "start_at": data.get("start_at"),
        "end_at": data.get("end_at"),
        "duration_minutes": duration,
        "duration_precision": precision,
    }


def find_plan_conflicts(
    conn: sqlite3.Connection,
    *,
    start_at: str | None,
    end_at: str | None,
    exclude_plan_id: str | None = None,
) -> list[dict[str, Any]]:
    start = parse_datetime(start_at)
    end = parse_datetime(end_at)
    if not start or not end:
        return []
    rows = conn.execute(
        """
        SELECT * FROM plan
        WHERE status = 'planned'
          AND plan_type = 'time_block'
          AND start_at IS NOT NULL AND end_at IS NOT NULL
          AND start_at < ? AND end_at > ?
        """,
        (end.isoformat().replace("+00:00", "Z"), start.isoformat().replace("+00:00", "Z")),
    ).fetchall()
    conflicts = []
    for row in rows:
        item = dict(row)
        if exclude_plan_id and item["id"] == exclude_plan_id:
            continue
        conflicts.append(item)
    return conflicts


def derive_is_overdue(plan: dict[str, Any], now: datetime | None = None) -> bool:
    if plan.get("status") != "planned":
        return False
    end = parse_datetime(plan.get("end_at"))
    if not end:
        return False
    now = now or datetime.now(end.tzinfo)
    return end < now
