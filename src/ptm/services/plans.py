"""Plan service."""

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
    update_versioned,
)
from ptm.services.validation import (
    derive_is_overdue,
    ensure_stage_belongs_to_matter,
    find_plan_conflicts,
    validate_plan_fields,
)
from ptm.timeutil import new_id, to_utc_iso, utc_now_iso


def plan_action(
    conn: sqlite3.Connection,
    *,
    action: str,
    plan_id: str | None = None,
    matter_id: str | None = None,
    stage_id: str | None = None,
    plan_type: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    estimated_minutes: int | None = None,
    expected_result: str | None = None,
    flexibility: str = "flexible",
    expected_version: int | None = None,
    request_id: str | None = None,
    patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "action": action,
        "plan_id": plan_id,
        "matter_id": matter_id,
        "stage_id": stage_id,
        "plan_type": plan_type,
        "start_at": start_at,
        "end_at": end_at,
        "estimated_minutes": estimated_minutes,
        "expected_result": expected_result,
        "flexibility": flexibility,
        "expected_version": expected_version,
        "patch": patch,
    }
    cached = check_idempotency(conn, request_id, "ptm_plan", payload)
    if cached is not None:
        return cached

    if action == "create":
        result = _create_plan(
            conn,
            matter_id=matter_id,
            stage_id=stage_id,
            plan_type=plan_type,
            start_at=start_at,
            end_at=end_at,
            estimated_minutes=estimated_minutes,
            expected_result=expected_result,
            flexibility=flexibility,
            request_id=request_id,
        )
    elif action in {"update", "cancel", "complete"}:
        if not plan_id or expected_version is None:
            raise InvalidArgument("plan_id and expected_version required")
        result = _mutate_plan(
            conn,
            action=action,
            plan_id=plan_id,
            expected_version=expected_version,
            start_at=start_at,
            end_at=end_at,
            estimated_minutes=estimated_minutes,
            expected_result=expected_result,
            flexibility=flexibility,
            plan_type=plan_type,
            stage_id=stage_id,
            patch=patch,
            request_id=request_id,
        )
    else:
        raise InvalidArgument(f"unsupported action: {action}")

    save_idempotency(conn, request_id, "ptm_plan", payload, result)
    return result


def _create_plan(
    conn: sqlite3.Connection,
    *,
    matter_id: str | None,
    stage_id: str | None,
    plan_type: str | None,
    start_at: str | None,
    end_at: str | None,
    estimated_minutes: int | None,
    expected_result: str | None,
    flexibility: str,
    request_id: str | None,
) -> dict[str, Any]:
    if not matter_id:
        raise InvalidArgument("matter_id required to create plan")
    if not plan_type:
        raise InvalidArgument("plan_type required")
    require_by_id(conn, "matter", matter_id)
    ensure_stage_belongs_to_matter(conn, stage_id, matter_id)
    data = {
        "plan_type": plan_type,
        "start_at": to_utc_iso(start_at),
        "end_at": to_utc_iso(end_at),
        "estimated_minutes": estimated_minutes,
    }
    validate_plan_fields(data)
    conflicts = find_plan_conflicts(conn, start_at=data["start_at"], end_at=data["end_at"])
    now = utc_now_iso()
    row = insert_row(
        conn,
        "plan",
        {
            "id": new_id(),
            "matter_id": matter_id,
            "stage_id": stage_id,
            "plan_type": plan_type,
            "start_at": data["start_at"],
            "end_at": data["end_at"],
            "estimated_minutes": estimated_minutes,
            "expected_result": expected_result,
            "status": "planned",
            "flexibility": flexibility,
            "created_at": now,
            "updated_at": now,
            "version": 1,
        },
    )
    append_event(
        conn,
        event_type="plan.created",
        entity_type="plan",
        entity_id=row["id"],
        after=row,
        request_id=request_id,
    )
    # activate matter if backlog
    matter = require_by_id(conn, "matter", matter_id)
    if matter["status"] == "backlog":
        before, after = update_versioned(
            conn, "matter", matter_id, matter["version"], {"status": "active"}
        )
        append_event(
            conn,
            event_type="matter.status_changed",
            entity_type="matter",
            entity_id=matter_id,
            before=before,
            after=after,
            request_id=request_id,
        )
    return {
        "plan": {**row, "is_overdue": derive_is_overdue(row)},
        "conflicts": [
            {"id": c["id"], "matter_id": c["matter_id"], "start_at": c["start_at"], "end_at": c["end_at"]}
            for c in conflicts
        ],
        "linked_reminders": [],
    }


def _mutate_plan(
    conn: sqlite3.Connection,
    *,
    action: str,
    plan_id: str,
    expected_version: int,
    start_at: str | None,
    end_at: str | None,
    estimated_minutes: int | None,
    expected_result: str | None,
    flexibility: str | None,
    plan_type: str | None,
    stage_id: str | None,
    patch: dict[str, Any] | None,
    request_id: str | None,
) -> dict[str, Any]:
    current = require_by_id(conn, "plan", plan_id)
    updates: dict[str, Any] = dict(patch or {})
    if action == "cancel":
        updates["status"] = "cancelled"
    elif action == "complete":
        updates["status"] = "completed"
    else:
        if start_at is not None:
            updates["start_at"] = to_utc_iso(start_at)
        if end_at is not None:
            updates["end_at"] = to_utc_iso(end_at)
        if estimated_minutes is not None:
            updates["estimated_minutes"] = estimated_minutes
        if expected_result is not None:
            updates["expected_result"] = expected_result
        if flexibility is not None:
            updates["flexibility"] = flexibility
        if plan_type is not None:
            updates["plan_type"] = plan_type
        if stage_id is not None:
            ensure_stage_belongs_to_matter(conn, stage_id, current["matter_id"])
            updates["stage_id"] = stage_id
    merged = {**current, **updates}
    validate_plan_fields(
        {
            "plan_type": merged["plan_type"],
            "start_at": merged.get("start_at"),
            "end_at": merged.get("end_at"),
            "estimated_minutes": merged.get("estimated_minutes"),
        }
    )
    conflicts = find_plan_conflicts(
        conn,
        start_at=merged.get("start_at"),
        end_at=merged.get("end_at"),
        exclude_plan_id=plan_id,
    )
    before, after = update_versioned(conn, "plan", plan_id, expected_version, updates)
    append_event(
        conn,
        event_type=f"plan.{action}",
        entity_type="plan",
        entity_id=plan_id,
        before=before,
        after=after,
        request_id=request_id,
    )
    linked = list_rows(
        conn,
        "reminder",
        where="plan_id = ? AND status IN ('pending', 'claimed', 'failed')",
        params=(plan_id,),
        limit=20,
    )
    return {
        "plan": {**after, "is_overdue": derive_is_overdue(after)},
        "conflicts": [
            {"id": c["id"], "matter_id": c["matter_id"], "start_at": c["start_at"], "end_at": c["end_at"]}
            for c in conflicts
        ],
        "linked_reminders": [
            {"id": r["id"], "title": r["title"], "remind_at": r["remind_at"], "status": r["status"], "version": r["version"]}
            for r in linked
        ],
    }
