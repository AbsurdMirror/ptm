"""Descriptive statistics (V0)."""

from __future__ import annotations

import sqlite3
from typing import Any

from ptm.database import rows_to_dicts
from ptm.errors import InvalidArgument
from ptm.timeutil import to_utc_iso


def compute_stats(
    conn: sqlite3.Connection,
    *,
    metric: str,
    start_at: str | None = None,
    end_at: str | None = None,
    category_id: str | None = None,
    matter_id: str | None = None,
) -> dict[str, Any]:
    start_s = to_utc_iso(start_at)
    end_s = to_utc_iso(end_at)
    base_filters = []
    params: list[Any] = []
    if start_s:
        base_filters.append("e.created_at >= ?")
        params.append(start_s)
    if end_s:
        base_filters.append("e.created_at < ?")
        params.append(end_s)
    if matter_id:
        base_filters.append("e.matter_id = ?")
        params.append(matter_id)
    if category_id:
        base_filters.append("m.category_id = ?")
        params.append(category_id)
    where = (" AND ".join(base_filters)) if base_filters else "1=1"

    if metric == "time_by_category":
        rows = rows_to_dicts(
            conn.execute(
                f"""
                SELECT c.id AS category_id, c.name AS category_name,
                       SUM(e.duration_minutes) AS minutes,
                       COUNT(e.id) AS sample_count,
                       SUM(CASE WHEN e.duration_precision = 'approximate' THEN 1 ELSE 0 END) AS approximate_count,
                       SUM(CASE WHEN e.duration_minutes IS NULL THEN 1 ELSE 0 END) AS excluded_missing_duration
                FROM execution e
                LEFT JOIN matter m ON m.id = e.matter_id
                LEFT JOIN category c ON c.id = m.category_id
                WHERE {where} AND e.duration_minutes IS NOT NULL
                GROUP BY c.id, c.name
                ORDER BY minutes DESC
                """,
                params,
            ).fetchall()
        )
        return _wrap(metric, start_s, end_s, rows, unit="minutes")

    if metric == "time_by_matter":
        rows = rows_to_dicts(
            conn.execute(
                f"""
                SELECT e.matter_id, m.title,
                       SUM(e.duration_minutes) AS minutes,
                       COUNT(e.id) AS sample_count
                FROM execution e
                LEFT JOIN matter m ON m.id = e.matter_id
                WHERE {where} AND e.duration_minutes IS NOT NULL
                GROUP BY e.matter_id, m.title
                ORDER BY minutes DESC
                """,
                params,
            ).fetchall()
        )
        return _wrap(metric, start_s, end_s, rows, unit="minutes")

    if metric == "time_by_stage":
        rows = rows_to_dicts(
            conn.execute(
                f"""
                SELECT e.stage_id, s.title,
                       SUM(e.duration_minutes) AS minutes,
                       COUNT(e.id) AS sample_count
                FROM execution e
                LEFT JOIN stage s ON s.id = e.stage_id
                LEFT JOIN matter m ON m.id = e.matter_id
                WHERE {where} AND e.stage_id IS NOT NULL AND e.duration_minutes IS NOT NULL
                GROUP BY e.stage_id, s.title
                ORDER BY minutes DESC
                """,
                params,
            ).fetchall()
        )
        return _wrap(metric, start_s, end_s, rows, unit="minutes")

    if metric == "plan_vs_execution":
        plan_filters = []
        plan_params: list[Any] = []
        if start_s:
            plan_filters.append("p.start_at >= ?")
            plan_params.append(start_s)
        if end_s:
            plan_filters.append("p.start_at < ?")
            plan_params.append(end_s)
        if matter_id:
            plan_filters.append("p.matter_id = ?")
            plan_params.append(matter_id)
        pwhere = " AND ".join(plan_filters) if plan_filters else "1=1"
        rows = rows_to_dicts(
            conn.execute(
                f"""
                SELECT p.id AS plan_id, p.matter_id, p.estimated_minutes,
                       COALESCE(SUM(e.duration_minutes), 0) AS executed_minutes,
                       COUNT(e.id) AS execution_count,
                       SUM(CASE WHEN e.duration_precision = 'approximate' THEN 1 ELSE 0 END) AS approximate_executions
                FROM plan p
                LEFT JOIN execution e ON e.plan_id = p.id
                WHERE {pwhere}
                GROUP BY p.id
                """,
                plan_params,
            ).fetchall()
        )
        values = []
        excluded = 0
        for r in rows:
            if r["execution_count"] == 0:
                excluded += 1
                values.append(
                    {
                        **r,
                        "status": "no_execution_recorded",
                        "note": "not_treated_as_zero_completion",
                    }
                )
            else:
                values.append(
                    {
                        **r,
                        "delta_minutes": (r["executed_minutes"] or 0) - (r["estimated_minutes"] or 0)
                        if r["estimated_minutes"] is not None
                        else None,
                        "status": "compared",
                    }
                )
        return {
            "metric": metric,
            "range": {"start_at": start_s, "end_at": end_s},
            "unit": "minutes",
            "values": values,
            "sample_count": sum(1 for v in values if v["status"] == "compared"),
            "excluded_count": excluded,
            "excluded_reason": "plans_without_linked_execution_not_zero_filled",
            "methodology": "sum executions per plan; approximate durations included but flagged",
        }

    if metric == "completion_summary":
        matters = rows_to_dicts(
            conn.execute(
                """
                SELECT status, COUNT(*) AS count FROM matter GROUP BY status
                """
            ).fetchall()
        )
        plans = rows_to_dicts(
            conn.execute(
                """
                SELECT status, COUNT(*) AS count FROM plan GROUP BY status
                """
            ).fetchall()
        )
        return {
            "metric": metric,
            "range": {"start_at": start_s, "end_at": end_s},
            "unit": "count",
            "values": {"matters": matters, "plans": plans},
            "sample_count": sum(m["count"] for m in matters),
            "excluded_count": 0,
            "excluded_reason": None,
            "methodology": "status histogram; not a productivity score",
        }

    if metric == "estimate_accuracy":
        rows = rows_to_dicts(
            conn.execute(
                f"""
                SELECT p.id AS plan_id, p.matter_id, p.estimated_minutes,
                       SUM(e.duration_minutes) AS executed_minutes,
                       COUNT(e.id) AS execution_count,
                       SUM(CASE WHEN e.duration_precision != 'exact' THEN 1 ELSE 0 END) AS non_exact
                FROM plan p
                JOIN execution e ON e.plan_id = p.id
                LEFT JOIN matter m ON m.id = p.matter_id
                WHERE p.estimated_minutes IS NOT NULL
                  AND e.duration_minutes IS NOT NULL
                  AND {where.replace('e.created_at', 'e.created_at')}
                GROUP BY p.id
                """,
                params,
            ).fetchall()
        )
        samples = []
        excluded_approx = 0
        for r in rows:
            if r["non_exact"]:
                excluded_approx += 1
                continue
            if r["estimated_minutes"] and r["executed_minutes"] is not None:
                samples.append(
                    {
                        "plan_id": r["plan_id"],
                        "matter_id": r["matter_id"],
                        "estimated_minutes": r["estimated_minutes"],
                        "executed_minutes": r["executed_minutes"],
                        "error_minutes": r["executed_minutes"] - r["estimated_minutes"],
                        "abs_error_minutes": abs(r["executed_minutes"] - r["estimated_minutes"]),
                        "signed_ratio": (r["executed_minutes"] / r["estimated_minutes"])
                        if r["estimated_minutes"]
                        else None,
                    }
                )
        mae = (
            sum(s["abs_error_minutes"] for s in samples) / len(samples) if samples else None
        )
        # do not cancel under/over estimates into a fake accuracy claim
        under = sum(1 for s in samples if s["error_minutes"] < 0)
        over = sum(1 for s in samples if s["error_minutes"] > 0)
        return {
            "metric": metric,
            "range": {"start_at": start_s, "end_at": end_s},
            "unit": "minutes",
            "values": {
                "mean_absolute_error": mae,
                "underestimate_count": over,  # executed > estimated
                "overestimate_count": under,
                "samples": samples,
            },
            "sample_count": len(samples),
            "excluded_count": excluded_approx,
            "excluded_reason": "non_exact_duration_excluded; under/over not netted into accuracy claim",
            "methodology": "only plan↔execution pairs with exact durations and estimated_minutes",
        }

    raise InvalidArgument(f"unknown metric: {metric}")


def _wrap(metric: str, start_s: str | None, end_s: str | None, values: Any, unit: str) -> dict[str, Any]:
    return {
        "metric": metric,
        "range": {"start_at": start_s, "end_at": end_s},
        "unit": unit,
        "values": values,
        "sample_count": len(values) if isinstance(values, list) else None,
        "excluded_count": None,
        "excluded_reason": "rows_without_duration_excluded",
        "methodology": "sum of recorded duration_minutes only; missing records not treated as zero",
    }
