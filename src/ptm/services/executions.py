"""Execution recording service."""

from __future__ import annotations

import sqlite3
from typing import Any

from ptm.repositories.common import (
    append_event,
    check_idempotency,
    insert_row,
    save_idempotency,
)
from ptm.services.validation import validate_execution_fields
from ptm.timeutil import new_id, to_utc_iso, utc_now_iso


def record_execution(
    conn: sqlite3.Connection,
    *,
    matter_id: str | None = None,
    stage_id: str | None = None,
    plan_id: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    duration_minutes: int | None = None,
    duration_precision: str = "approximate",
    result: str = "unknown",
    output: str | None = None,
    note: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "matter_id": matter_id,
        "stage_id": stage_id,
        "plan_id": plan_id,
        "start_at": start_at,
        "end_at": end_at,
        "duration_minutes": duration_minutes,
        "duration_precision": duration_precision,
        "result": result,
        "output": output,
        "note": note,
    }
    cached = check_idempotency(conn, request_id, "ptm_record_execution", payload)
    if cached is not None:
        return cached

    validated = validate_execution_fields(
        {
            **payload,
            "start_at": to_utc_iso(start_at),
            "end_at": to_utc_iso(end_at),
        },
        conn,
    )
    now = utc_now_iso()
    row = insert_row(
        conn,
        "execution",
        {
            "id": new_id(),
            "matter_id": validated.get("matter_id"),
            "stage_id": validated.get("stage_id"),
            "plan_id": validated.get("plan_id"),
            "start_at": validated.get("start_at"),
            "end_at": validated.get("end_at"),
            "duration_minutes": validated.get("duration_minutes"),
            "duration_precision": validated["duration_precision"],
            "result": result,
            "output": output,
            "note": note,
            "created_at": now,
            "updated_at": now,
            "version": 1,
        },
    )
    append_event(
        conn,
        event_type="execution.created",
        entity_type="execution",
        entity_id=row["id"],
        after=row,
        request_id=request_id,
        raw_text=note,
    )
    out = {
        "id": row["id"],
        "version": row["version"],
        "matter_id": row["matter_id"],
        "plan_id": row["plan_id"],
        "duration_minutes": row["duration_minutes"],
        "duration_precision": row["duration_precision"],
        "result": row["result"],
        "matter_auto_completed": False,
    }
    save_idempotency(conn, request_id, "ptm_record_execution", payload, out)
    return out
