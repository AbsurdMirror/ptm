"""Agenda and review query services."""

from __future__ import annotations

import sqlite3
from typing import Any

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ptm.database import rows_to_dicts
from ptm.errors import InvalidArgument
from ptm.services.validation import derive_is_overdue
from ptm.timeutil import day_bounds_utc, to_utc_iso, utc_now


def _require_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidArgument("unknown timezone", details={"timezone": name}) from exc


def get_agenda(
    conn: sqlite3.Connection,
    *,
    date: str,
    timezone: str = "Asia/Shanghai",
    include_backlog: bool = True,
    limit: int = 20,
) -> dict[str, Any]:
    _require_timezone(timezone)
    start, end = day_bounds_utc(date, timezone)
    start_s = to_utc_iso(start)
    end_s = to_utc_iso(end)
    now = utc_now()

    plan_rows = rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM plan
            WHERE status = 'planned'
              AND (
                (start_at IS NOT NULL AND start_at >= ? AND start_at < ?)
                OR (plan_type = 'time_budget' AND created_at >= ? AND created_at < ?)
              )
            ORDER BY start_at IS NULL, start_at ASC
            LIMIT ?
            """,
            (start_s, end_s, start_s, end_s, limit + 1),
        ).fetchall()
    )
    plans_truncated = len(plan_rows) > limit
    plans = [_plan_summary(p, now) for p in plan_rows[:limit]]

    overdue_rows = rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM plan
            WHERE status = 'planned'
              AND end_at IS NOT NULL
              AND end_at < ?
            ORDER BY end_at ASC
            LIMIT ?
            """,
            (to_utc_iso(now), limit + 1),
        ).fetchall()
    )
    overdue_truncated = len(overdue_rows) > limit
    overdue_plans = [_plan_summary(p, now) for p in overdue_rows[:limit]]

    due_rows = rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM matter
            WHERE due_at IS NOT NULL
              AND due_at >= ? AND due_at < ?
              AND status NOT IN ('completed', 'cancelled')
            ORDER BY due_at ASC
            LIMIT ?
            """,
            (start_s, end_s, limit + 1),
        ).fetchall()
    )
    due_truncated = len(due_rows) > limit
    due_matters = [_matter_summary(m) for m in due_rows[:limit]]

    active_wo_plan = rows_to_dicts(
        conn.execute(
            """
            SELECT m.* FROM matter m
            WHERE m.status = 'active'
              AND NOT EXISTS (
                SELECT 1 FROM plan p
                WHERE p.matter_id = m.id AND p.status = 'planned'
              )
            ORDER BY m.updated_at DESC
            LIMIT ?
            """,
            (limit + 1,),
        ).fetchall()
    )
    active_truncated = len(active_wo_plan) > limit
    active_matters_without_plan = [_matter_summary(m) for m in active_wo_plan[:limit]]

    backlog_candidates: list[dict[str, Any]] = []
    backlog_truncated = False
    if include_backlog:
        backlog_rows = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM matter
                WHERE status = 'backlog'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit + 1,),
            ).fetchall()
        )
        backlog_truncated = len(backlog_rows) > limit
        backlog_candidates = [_matter_summary(m) for m in backlog_rows[:limit]]

    prefs = rows_to_dicts(
        conn.execute(
            """
            SELECT id, type, content, source, matter_id, confidence, status
            FROM observation
            WHERE status = 'active' AND type IN ('preference', 'pattern')
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (min(limit, 10),),
        ).fetchall()
    )

    return {
        "date": date,
        "timezone": timezone,
        "plans": plans,
        "plans_has_more": plans_truncated,
        "overdue_plans": overdue_plans,
        "overdue_plans_has_more": overdue_truncated,
        "due_matters": due_matters,
        "due_matters_has_more": due_truncated,
        "active_matters_without_plan": active_matters_without_plan,
        "active_matters_without_plan_has_more": active_truncated,
        "backlog_candidates": backlog_candidates,
        "backlog_candidates_has_more": backlog_truncated,
        "relevant_preferences": prefs,
    }


def get_review(
    conn: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
    timezone: str = "Asia/Shanghai",
    detail: str = "summary",
) -> dict[str, Any]:
    _require_timezone(timezone)
    start, _ = day_bounds_utc(start_date, timezone)
    _, end = day_bounds_utc(end_date, timezone)
    if end <= start:
        raise InvalidArgument("end_date must be on or after start_date")
    # `end` is start of the day after end_date — exclusive upper bound
    start_s = to_utc_iso(start)
    end_s = to_utc_iso(end)

    plan_status = rows_to_dicts(
        conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM plan
            WHERE created_at >= ? AND created_at < ?
               OR (start_at IS NOT NULL AND start_at >= ? AND start_at < ?)
            GROUP BY status
            """,
            (start_s, end_s, start_s, end_s),
        ).fetchall()
    )

    executions = rows_to_dicts(
        conn.execute(
            """
            SELECT id, matter_id, plan_id, duration_minutes, duration_precision, result, created_at
            FROM execution
            WHERE created_at >= ? AND created_at < ?
            ORDER BY created_at ASC
            """,
            (start_s, end_s),
        ).fetchall()
    )
    total_minutes = sum(e["duration_minutes"] or 0 for e in executions if e["duration_minutes"] is not None)
    approx_count = sum(1 for e in executions if e["duration_precision"] == "approximate")
    unknown_count = sum(1 for e in executions if e["duration_precision"] == "unknown" or e["duration_minutes"] is None)

    by_category = rows_to_dicts(
        conn.execute(
            """
            SELECT c.id AS category_id, c.name AS category_name,
                   SUM(COALESCE(e.duration_minutes, 0)) AS minutes,
                   COUNT(e.id) AS execution_count
            FROM execution e
            LEFT JOIN matter m ON m.id = e.matter_id
            LEFT JOIN category c ON c.id = m.category_id
            WHERE e.created_at >= ? AND e.created_at < ?
              AND e.duration_minutes IS NOT NULL
            GROUP BY c.id, c.name
            ORDER BY minutes DESC
            """,
            (start_s, end_s),
        ).fetchall()
    )

    # plan deviations: planned time_block with no execution in range, or with duration gap
    deviations = rows_to_dicts(
        conn.execute(
            """
            SELECT p.id AS plan_id, p.matter_id, p.estimated_minutes, p.start_at, p.end_at, p.status,
                   COALESCE(SUM(e.duration_minutes), 0) AS executed_minutes,
                   COUNT(e.id) AS execution_count
            FROM plan p
            LEFT JOIN execution e ON e.plan_id = p.id
            WHERE p.plan_type = 'time_block'
              AND p.start_at IS NOT NULL AND p.start_at >= ? AND p.start_at < ?
            GROUP BY p.id
            """,
            (start_s, end_s),
        ).fetchall()
    )
    plan_deviations = []
    for d in deviations:
        if d["execution_count"] == 0:
            plan_deviations.append(
                {
                    "plan_id": d["plan_id"],
                    "matter_id": d["matter_id"],
                    "kind": "no_execution_recorded",
                    "estimated_minutes": d["estimated_minutes"],
                    "executed_minutes": 0,
                    "note": "unrecorded_or_unexecuted_indeterminate",
                }
            )
        elif d["estimated_minutes"] is not None:
            plan_deviations.append(
                {
                    "plan_id": d["plan_id"],
                    "matter_id": d["matter_id"],
                    "kind": "duration_delta",
                    "estimated_minutes": d["estimated_minutes"],
                    "executed_minutes": d["executed_minutes"],
                    "delta_minutes": d["executed_minutes"] - d["estimated_minutes"],
                }
            )

    feedback = rows_to_dicts(
        conn.execute(
            """
            SELECT id, type, content, source, matter_id, created_at
            FROM observation
            WHERE created_at >= ? AND created_at < ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (start_s, end_s, 50 if detail == "standard" else 10),
        ).fetchall()
    )

    stagnant = rows_to_dicts(
        conn.execute(
            """
            SELECT id, title, status, updated_at
            FROM matter
            WHERE status IN ('backlog', 'active', 'paused')
              AND updated_at < ?
            ORDER BY updated_at ASC
            LIMIT 20
            """,
            (start_s,),
        ).fetchall()
    )

    result: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "timezone": timezone,
        "detail": detail,
        "plan_status_distribution": plan_status,
        "execution_summary": {
            "count": len(executions),
            "recorded_minutes": total_minutes,
            "approximate_count": approx_count,
            "unknown_or_missing_duration_count": unknown_count,
            "note": "missing_records_are_not_treated_as_zero",
        },
        "time_by_category": by_category,
        "plan_deviations": plan_deviations[:20],
        "feedback": feedback,
        "stagnant_matters": stagnant,
        "suggested_query_ids": {
            "execution_ids": [e["id"] for e in executions[:10]],
            "matter_ids": list({e["matter_id"] for e in executions if e["matter_id"]})[:10],
        },
    }
    if detail == "standard":
        result["executions"] = executions
    return result


def _plan_summary(plan: dict[str, Any], now) -> dict[str, Any]:
    return {
        "id": plan["id"],
        "matter_id": plan["matter_id"],
        "stage_id": plan["stage_id"],
        "plan_type": plan["plan_type"],
        "start_at": plan["start_at"],
        "end_at": plan["end_at"],
        "estimated_minutes": plan["estimated_minutes"],
        "expected_result": plan["expected_result"],
        "status": plan["status"],
        "flexibility": plan["flexibility"],
        "version": plan["version"],
        "is_overdue": derive_is_overdue(plan, now),
    }


def _matter_summary(matter: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": matter["id"],
        "title": matter["title"],
        "status": matter["status"],
        "priority": matter["priority"],
        "due_at": matter["due_at"],
        "category_id": matter["category_id"],
        "version": matter["version"],
    }
