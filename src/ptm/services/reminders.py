"""Reminder CRUD and dispatch state machine."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from ptm.errors import InvalidArgument, NotFound, VersionConflict
from ptm.repositories.common import (
    append_event,
    check_idempotency,
    get_by_id,
    insert_row,
    list_rows,
    require_by_id,
    save_idempotency,
    update_versioned,
)
from ptm.timeutil import new_id, to_utc_iso, utc_now_iso


def reminder_action(
    conn: sqlite3.Connection,
    *,
    action: str,
    reminder_id: str | None = None,
    matter_id: str | None = None,
    stage_id: str | None = None,
    plan_id: str | None = None,
    title: str | None = None,
    remind_at: str | None = None,
    expected_version: int | None = None,
    status: str | None = None,
    limit: int = 50,
    request_id: str | None = None,
    patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "action": action,
        "reminder_id": reminder_id,
        "matter_id": matter_id,
        "stage_id": stage_id,
        "plan_id": plan_id,
        "title": title,
        "remind_at": remind_at,
        "expected_version": expected_version,
        "status": status,
        "limit": limit,
        "patch": patch,
    }
    if action in {"create", "update", "cancel"}:
        cached = check_idempotency(conn, request_id, "ptm_reminder", payload)
        if cached is not None:
            return cached

    if action == "create":
        if not title or not remind_at:
            raise InvalidArgument("title and remind_at required")
        if matter_id:
            require_by_id(conn, "matter", matter_id)
        if stage_id:
            require_by_id(conn, "stage", stage_id)
        if plan_id:
            require_by_id(conn, "plan", plan_id)
        now = utc_now_iso()
        row = insert_row(
            conn,
            "reminder",
            {
                "id": new_id(),
                "matter_id": matter_id,
                "stage_id": stage_id,
                "plan_id": plan_id,
                "title": title,
                "remind_at": to_utc_iso(remind_at),
                "status": "pending",
                "attempt_count": 0,
                "lease_until": None,
                "last_attempt_at": None,
                "submitted_at": None,
                "delivered_at": None,
                "last_error": None,
                "claim_token": None,
                "created_at": now,
                "updated_at": now,
                "version": 1,
            },
        )
        append_event(
            conn,
            event_type="reminder.created",
            entity_type="reminder",
            entity_id=row["id"],
            after=row,
            request_id=request_id,
        )
        result = {"reminder": row}
        save_idempotency(conn, request_id, "ptm_reminder", payload, result)
        return result

    if action == "get":
        if not reminder_id:
            raise InvalidArgument("reminder_id required")
        return {"reminder": require_by_id(conn, "reminder", reminder_id)}

    if action == "list":
        clauses = ["1=1"]
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if matter_id:
            clauses.append("matter_id = ?")
            params.append(matter_id)
        rows = list_rows(
            conn,
            "reminder",
            where=" AND ".join(clauses),
            params=params,
            order="remind_at ASC",
            limit=limit,
        )
        return {"items": rows, "count": len(rows)}

    if action in {"update", "cancel"}:
        if not reminder_id or expected_version is None:
            raise InvalidArgument("reminder_id and expected_version required")
        updates = dict(patch or {})
        if action == "cancel":
            updates["status"] = "cancelled"
            updates["claim_token"] = None
            updates["lease_until"] = None
        else:
            if title is not None:
                updates["title"] = title
            if remind_at is not None:
                updates["remind_at"] = to_utc_iso(remind_at)
            if matter_id is not None:
                updates["matter_id"] = matter_id
            if stage_id is not None:
                updates["stage_id"] = stage_id
            if plan_id is not None:
                updates["plan_id"] = plan_id
        before, after = update_versioned(conn, "reminder", reminder_id, expected_version, updates)
        append_event(
            conn,
            event_type=f"reminder.{action}",
            entity_type="reminder",
            entity_id=reminder_id,
            before=before,
            after=after,
            request_id=request_id,
        )
        result = {"reminder": after}
        save_idempotency(conn, request_id, "ptm_reminder", payload, result)
        return result

    raise InvalidArgument(f"unsupported action: {action}")


def reminder_dispatch(
    conn: sqlite3.Connection,
    *,
    action: str,
    limit: int = 10,
    lease_seconds: int = 300,
    claim_token: str | None = None,
    reminder_id: str | None = None,
    ack_status: str | None = None,
    error: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "action": action,
        "limit": limit,
        "lease_seconds": lease_seconds,
        "claim_token": claim_token,
        "reminder_id": reminder_id,
        "ack_status": ack_status,
        "error": error,
    }
    cached = check_idempotency(conn, request_id, "ptm_reminder_dispatch", payload)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    now_s = to_utc_iso(now)

    if action == "claim_due":
        # recover expired leases back to pending/failed
        conn.execute(
            """
            UPDATE reminder
            SET status = CASE WHEN attempt_count > 0 THEN 'failed' ELSE 'pending' END,
                lease_until = NULL,
                claim_token = NULL,
                updated_at = ?,
                version = version + 1
            WHERE status = 'claimed' AND lease_until IS NOT NULL AND lease_until < ?
            """,
            (now_s, now_s),
        )
        due = list_rows(
            conn,
            "reminder",
            where="status IN ('pending', 'failed') AND remind_at <= ?",
            params=(now_s,),
            order="remind_at ASC",
            limit=limit,
        )
        claimed = []
        skipped_conflicts = 0
        lease_until = to_utc_iso(now + timedelta(seconds=lease_seconds))
        for item in due:
            token = new_id()
            try:
                before, after = update_versioned(
                    conn,
                    "reminder",
                    item["id"],
                    item["version"],
                    {
                        "status": "claimed",
                        "claim_token": token,
                        "lease_until": lease_until,
                        "last_attempt_at": now_s,
                        "attempt_count": int(item["attempt_count"]) + 1,
                    },
                )
            except VersionConflict:
                # Another dispatcher won the race; skip rather than failing the batch.
                skipped_conflicts += 1
                continue
            append_event(
                conn,
                event_type="reminder.claimed",
                entity_type="reminder",
                entity_id=item["id"],
                before=before,
                after=after,
                request_id=request_id,
            )
            claimed.append(
                {
                    "id": after["id"],
                    "title": after["title"],
                    "remind_at": after["remind_at"],
                    "claim_token": token,
                    "lease_until": lease_until,
                    "attempt_count": after["attempt_count"],
                    "matter_id": after["matter_id"],
                    "plan_id": after["plan_id"],
                    "version": after["version"],
                }
            )
        result = {
            "claimed": claimed,
            "count": len(claimed),
            "skipped_conflicts": skipped_conflicts,
        }
        save_idempotency(conn, request_id, "ptm_reminder_dispatch", payload, result)
        return result

    if action == "ack":
        if not claim_token or not reminder_id or not ack_status:
            raise InvalidArgument("reminder_id, claim_token, ack_status required")
        if ack_status not in {"submitted", "delivered", "failed"}:
            raise InvalidArgument("invalid ack_status")
        row = get_by_id(conn, "reminder", reminder_id)
        if not row:
            raise NotFound("reminder not found", details={"id": reminder_id})
        if row["status"] != "claimed" or row["claim_token"] != claim_token:
            raise InvalidArgument(
                "invalid or expired claim_token",
                details={"status": row["status"]},
            )
        if row["lease_until"] and row["lease_until"] < now_s:
            raise InvalidArgument("claim lease expired")

        updates: dict[str, Any] = {"status": ack_status, "claim_token": None, "lease_until": None}
        if ack_status == "submitted":
            updates["submitted_at"] = now_s
        elif ack_status == "delivered":
            updates["delivered_at"] = now_s
            if not row["submitted_at"]:
                updates["submitted_at"] = now_s
        elif ack_status == "failed":
            updates["last_error"] = error or "delivery_failed"

        before, after = update_versioned(conn, "reminder", reminder_id, row["version"], updates)
        append_event(
            conn,
            event_type=f"reminder.ack_{ack_status}",
            entity_type="reminder",
            entity_id=reminder_id,
            before=before,
            after=after,
            request_id=request_id,
        )
        result = {"reminder": after}
        save_idempotency(conn, request_id, "ptm_reminder_dispatch", payload, result)
        return result

    raise InvalidArgument(f"unsupported action: {action}")
