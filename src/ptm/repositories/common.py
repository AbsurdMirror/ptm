"""Shared repository helpers."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ptm.database import row_to_dict, rows_to_dicts
from ptm.errors import DuplicateRequest, NotFound, VersionConflict
from ptm.timeutil import new_id, stable_hash, utc_now_iso


TABLE_COLUMNS: dict[str, set[str]] = {
    "category": {
        "id", "name", "description", "parent_id", "status",
        "created_at", "updated_at", "version",
    },
    "matter": {
        "id", "category_id", "title", "description", "status", "priority",
        "due_at", "estimated_effort_minutes", "created_at", "updated_at",
        "completed_at", "version",
    },
    "stage": {
        "id", "matter_id", "parent_stage_id", "title", "description", "status",
        "sequence", "estimated_effort_minutes", "completion_criteria",
        "created_at", "updated_at", "completed_at", "version",
    },
    "plan": {
        "id", "matter_id", "stage_id", "plan_type", "start_at", "end_at",
        "estimated_minutes", "expected_result", "status", "flexibility",
        "created_at", "updated_at", "version",
    },
    "execution": {
        "id", "matter_id", "stage_id", "plan_id", "start_at", "end_at",
        "duration_minutes", "duration_precision", "result", "output", "note",
        "created_at", "updated_at", "version",
    },
    "observation": {
        "id", "type", "content", "source", "matter_id", "execution_id",
        "confidence", "status", "created_at", "updated_at", "version",
    },
    "reminder": {
        "id", "matter_id", "stage_id", "plan_id", "title", "remind_at",
        "status", "attempt_count", "lease_until", "last_attempt_at",
        "submitted_at", "delivered_at", "last_error", "claim_token",
        "created_at", "updated_at", "version",
    },
    "event": {
        "id", "event_type", "entity_type", "entity_id", "occurred_at",
        "source", "raw_text", "before_json", "after_json", "request_id",
    },
}


def get_by_id(conn: sqlite3.Connection, table: str, entity_id: str) -> dict[str, Any] | None:
    if table not in TABLE_COLUMNS:
        raise ValueError(table)
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
    return row_to_dict(row)


def require_by_id(conn: sqlite3.Connection, table: str, entity_id: str) -> dict[str, Any]:
    row = get_by_id(conn, table, entity_id)
    if not row:
        raise NotFound(f"{table} not found", details={"id": entity_id, "entity": table})
    return row


def insert_row(conn: sqlite3.Connection, table: str, data: dict[str, Any]) -> dict[str, Any]:
    cols = [c for c in data if c in TABLE_COLUMNS[table]]
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)
    conn.execute(
        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})",
        [data[c] for c in cols],
    )
    return require_by_id(conn, table, data["id"])


def update_versioned(
    conn: sqlite3.Connection,
    table: str,
    entity_id: str,
    expected_version: int,
    patch: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = require_by_id(conn, table, entity_id)
    if int(current["version"]) != int(expected_version):
        raise VersionConflict(
            "The record was modified",
            details={"current_version": current["version"], "expected_version": expected_version},
        )
    allowed = TABLE_COLUMNS[table] - {"id", "created_at", "version"}
    updates = {k: v for k, v in patch.items() if k in allowed}
    updates["updated_at"] = utc_now_iso()
    updates["version"] = int(current["version"]) + 1
    sets = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE {table} SET {sets} WHERE id = ? AND version = ?",
        [*updates.values(), entity_id, expected_version],
    )
    if conn.total_changes == 0:
        raise VersionConflict(
            "The record was modified",
            details={"current_version": current["version"], "expected_version": expected_version},
        )
    return current, require_by_id(conn, table, entity_id)


def append_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    entity_type: str | None,
    entity_id: str | None,
    source: str = "tool",
    raw_text: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request_id: str | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    event = {
        "id": new_id(),
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "occurred_at": occurred_at or utc_now_iso(),
        "source": source,
        "raw_text": raw_text,
        "before_json": json.dumps(before, ensure_ascii=False) if before is not None else None,
        "after_json": json.dumps(after, ensure_ascii=False) if after is not None else None,
        "request_id": request_id,
    }
    return insert_row(conn, "event", event)


def check_idempotency(
    conn: sqlite3.Connection,
    request_id: str | None,
    tool_name: str,
    payload: Any,
) -> dict[str, Any] | None:
    if not request_id:
        return None
    input_hash = stable_hash(payload)
    row = conn.execute(
        "SELECT * FROM operation_request WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    if not row:
        return None
    if row["input_hash"] != input_hash or row["tool_name"] != tool_name:
        raise DuplicateRequest(
            "request_id reused with different input",
            details={"request_id": request_id},
        )
    return json.loads(row["result_json"])


def save_idempotency(
    conn: sqlite3.Connection,
    request_id: str | None,
    tool_name: str,
    payload: Any,
    result: dict[str, Any],
) -> None:
    if not request_id:
        return
    conn.execute(
        """
        INSERT INTO operation_request(request_id, tool_name, input_hash, result_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            request_id,
            tool_name,
            stable_hash(payload),
            json.dumps(result, ensure_ascii=False, default=str),
            utc_now_iso(),
        ),
    )


def list_rows(
    conn: sqlite3.Connection,
    table: str,
    where: str = "1=1",
    params: list[Any] | tuple[Any, ...] = (),
    order: str = "created_at DESC",
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE {where} ORDER BY {order} LIMIT ?",
        [*params, limit],
    ).fetchall()
    return rows_to_dicts(rows)
