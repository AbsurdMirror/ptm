"""Matter / category / capture services."""

from __future__ import annotations

import sqlite3
from typing import Any

from ptm.errors import InvalidArgument
from ptm.repositories.common import (
    append_event,
    check_idempotency,
    insert_row,
    list_rows,
    require_by_id,
    save_idempotency,
)
from ptm.timeutil import new_id, to_utc_iso, utc_now_iso


def capture(
    conn: sqlite3.Connection,
    *,
    kind: str,
    content: str,
    category_id: str | None = None,
    related_id: str | None = None,
    occurred_at: str | None = None,
    request_id: str | None = None,
    observation_type: str = "feedback",
    source: str = "user",
) -> dict[str, Any]:
    payload = {
        "kind": kind,
        "content": content,
        "category_id": category_id,
        "related_id": related_id,
        "occurred_at": occurred_at,
        "observation_type": observation_type,
        "source": source,
    }
    cached = check_idempotency(conn, request_id, "ptm_capture", payload)
    if cached is not None:
        return cached

    now = utc_now_iso()
    occurred = to_utc_iso(occurred_at) or now

    if kind == "matter":
        if category_id:
            require_by_id(conn, "category", category_id)
        row = insert_row(
            conn,
            "matter",
            {
                "id": new_id(),
                "category_id": category_id,
                "title": content.strip()[:500],
                "description": content.strip() if len(content.strip()) > 500 else None,
                "status": "backlog",
                "priority": None,
                "due_at": None,
                "estimated_effort_minutes": None,
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
                "version": 1,
            },
        )
        append_event(
            conn,
            event_type="matter.created",
            entity_type="matter",
            entity_id=row["id"],
            raw_text=content,
            after=row,
            request_id=request_id,
            occurred_at=occurred,
        )
        result = {
            "id": row["id"],
            "type": "matter",
            "title": row["title"],
            "status": row["status"],
            "version": row["version"],
            "similar_matters": find_similar_matters(conn, content, exclude_id=row["id"]),
        }
    elif kind == "observation":
        matter_id = related_id
        if matter_id:
            require_by_id(conn, "matter", matter_id)
        row = insert_row(
            conn,
            "observation",
            {
                "id": new_id(),
                "type": observation_type,
                "content": content.strip(),
                "source": source,
                "matter_id": matter_id,
                "execution_id": None,
                "confidence": None,
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "version": 1,
            },
        )
        append_event(
            conn,
            event_type="observation.created",
            entity_type="observation",
            entity_id=row["id"],
            raw_text=content,
            after=row,
            request_id=request_id,
            occurred_at=occurred,
        )
        result = {
            "id": row["id"],
            "type": "observation",
            "title": row["content"][:120],
            "status": row["status"],
            "version": row["version"],
            "observation_type": row["type"],
            "source": row["source"],
        }
    else:
        raise InvalidArgument("kind must be matter or observation")

    save_idempotency(conn, request_id, "ptm_capture", payload, result)
    return result


def find_similar_matters(
    conn: sqlite3.Connection, content: str, *, exclude_id: str | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    token = content.strip()[:40]
    if not token:
        return []
    rows = list_rows(
        conn,
        "matter",
        where="title LIKE ? AND status NOT IN ('cancelled')",
        params=(f"%{token}%",),
        order="updated_at DESC",
        limit=limit + 1,
    )
    out = []
    for row in rows:
        if exclude_id and row["id"] == exclude_id:
            continue
        out.append({"id": row["id"], "title": row["title"], "status": row["status"]})
        if len(out) >= limit:
            break
    return out


def create_stage(
    conn: sqlite3.Connection,
    *,
    matter_id: str,
    title: str,
    parent_stage_id: str | None = None,
    description: str | None = None,
    sequence: int | None = None,
    estimated_effort_minutes: int | None = None,
    completion_criteria: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    from ptm.services.validation import would_create_stage_cycle

    require_by_id(conn, "matter", matter_id)
    if parent_stage_id:
        parent = require_by_id(conn, "stage", parent_stage_id)
        if parent["matter_id"] != matter_id:
            raise InvalidArgument("parent stage must belong to same matter")
    now = utc_now_iso()
    stage_id = new_id()
    # temporary insert then cycle check via parent chain (parent already exists)
    if parent_stage_id and would_create_stage_cycle(conn, stage_id, parent_stage_id):
        raise InvalidArgument("stage cycle detected")
    row = insert_row(
        conn,
        "stage",
        {
            "id": stage_id,
            "matter_id": matter_id,
            "parent_stage_id": parent_stage_id,
            "title": title,
            "description": description,
            "status": "backlog",
            "sequence": sequence,
            "estimated_effort_minutes": estimated_effort_minutes,
            "completion_criteria": completion_criteria,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "version": 1,
        },
    )
    append_event(
        conn,
        event_type="stage.created",
        entity_type="stage",
        entity_id=row["id"],
        after=row,
        request_id=request_id,
    )
    return row
