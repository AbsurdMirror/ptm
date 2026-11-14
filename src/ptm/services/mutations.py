"""Atomic batch mutate service."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ptm.errors import (
    ConfirmationRequired,
    InvalidArgument,
    LimitExceeded,
    PreviewExpired,
)
from ptm.models import MUTABLE_ENTITIES
from ptm.repositories.common import (
    append_event,
    check_idempotency,
    insert_row,
    require_by_id,
    save_idempotency,
    update_versioned,
)
from ptm.services.matters import create_stage
from ptm.services.validation import (
    ensure_stage_belongs_to_matter,
    validate_execution_fields,
    validate_plan_fields,
    would_create_category_cycle,
    would_create_stage_cycle,
)
from ptm.timeutil import new_id, stable_hash, to_utc_iso, utc_now_iso


RISKY_ACTIONS = frozenset({"archive", "set_status"})
RISKY_STATUSES = frozenset({"cancelled", "completed", "archived"})


def mutate(
    conn: sqlite3.Connection,
    *,
    operations: list[dict[str, Any]],
    dry_run: bool = False,
    request_id: str | None = None,
    preview_token: str | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    if len(operations) > 20:
        raise LimitExceeded("max 20 operations per batch")
    # Idempotency key ignores confirmed (UI signal) but includes token + ops + dry_run.
    payload = {
        "operations": operations,
        "dry_run": dry_run,
        "preview_token": preview_token,
    }
    cached = check_idempotency(conn, request_id, "ptm_mutate", payload)
    if cached is not None:
        return cached

    risky = _is_risky(operations)
    touches_existing = _touches_existing(operations)
    # V0.3: confirmed=true alone is not authorization. Risky or existing-record
    # batches must go through dry_run → preview_token after the user saw the plan.
    needs_preview = (risky or touches_existing) and not dry_run
    if needs_preview and not preview_token:
        raise ConfirmationRequired(
            "batch update/archive requires dry_run preview_token after user confirmation",
            details={
                "hint": "set dry_run=true, show diffs to user, then submit with preview_token",
                "risky": risky,
                "touches_existing": touches_existing,
                "confirmed_ignored_without_preview": bool(confirmed),
            },
        )

    if preview_token and not dry_run:
        _validate_preview_token(conn, preview_token, operations)

    refs: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    diffs: list[dict[str, Any]] = []

    if dry_run:
        # Capture pre-mutation versions BEFORE simulation so submit can detect drift.
        baseline_versions = _collect_versions(conn, operations)
        conn.execute("SAVEPOINT mutate_preview")
        try:
            for op in operations:
                item = _apply_one(conn, op, refs, request_id=request_id, simulate=True)
                results.append(item["result"])
                diffs.append(item["diff"])
            from datetime import datetime, timedelta, timezone

            token = new_id()
            exp = (
                (datetime.now(timezone.utc) + timedelta(minutes=15))
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            snapshot = {"operations": operations, "versions": baseline_versions}
            out = {
                "dry_run": True,
                "preview_token": token,
                "expires_at": exp,
                "results": results,
                "diffs": diffs,
                "confirmation_required": risky or touches_existing,
                "impact_count": len(results),
            }
        finally:
            conn.execute("ROLLBACK TO mutate_preview")
            conn.execute("RELEASE mutate_preview")
        conn.execute(
            """
            INSERT INTO preview_token(token, request_hash, snapshot_json, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                out["preview_token"],
                stable_hash(operations),
                json.dumps(snapshot, ensure_ascii=False),
                out["expires_at"],
                utc_now_iso(),
            ),
        )
        return out

    for op in operations:
        item = _apply_one(conn, op, refs, request_id=request_id, simulate=False)
        results.append(item["result"])
        diffs.append(item["diff"])

    out = {"dry_run": False, "results": results, "diffs": diffs, "refs": refs}
    save_idempotency(conn, request_id, "ptm_mutate", payload, out)
    if preview_token:
        conn.execute("DELETE FROM preview_token WHERE token = ?", (preview_token,))
    return out


def _touches_existing(operations: list[dict[str, Any]]) -> bool:
    return any(op.get("action") in {"update", "archive", "set_status"} for op in operations)


def _is_risky(operations: list[dict[str, Any]]) -> bool:
    """Destructive / high-impact batches that must be previewed to the user."""
    for op in operations:
        data = op.get("data") or {}
        if op.get("action") in RISKY_ACTIONS:
            return True
        if data.get("status") in RISKY_STATUSES:
            return True
        if op.get("entity") == "plan" and data.get("flexibility") == "fixed":
            return True
    return False


def _collect_versions(
    conn: sqlite3.Connection, operations: list[dict[str, Any]]
) -> dict[str, int]:
    versions: dict[str, int] = {}
    for op in operations:
        eid = op.get("id")
        if not eid:
            continue
        entity = op["entity"]
        row = require_by_id(conn, entity, eid)
        versions[f"{entity}:{eid}"] = int(row["version"])
    return versions


def _validate_preview_token(
    conn: sqlite3.Connection, token: str, operations: list[dict[str, Any]]
) -> None:
    row = conn.execute("SELECT * FROM preview_token WHERE token = ?", (token,)).fetchone()
    if not row:
        raise PreviewExpired("preview_token not found")
    if row["expires_at"] < utc_now_iso():
        raise PreviewExpired("preview_token expired")
    if row["request_hash"] != stable_hash(operations):
        raise PreviewExpired("preview_token does not match operations")
    snapshot = json.loads(row["snapshot_json"])
    for key, ver in snapshot.get("versions", {}).items():
        entity, eid = key.split(":", 1)
        current = require_by_id(conn, entity, eid)
        if int(current["version"]) != int(ver):
            raise PreviewExpired(
                "preview object version changed",
                details={"entity": entity, "id": eid, "expected": ver, "current": current["version"]},
            )


def _resolve_refs(data: dict[str, Any], refs: dict[str, str]) -> dict[str, Any]:
    out = {}
    for k, v in data.items():
        if isinstance(v, str) and v.startswith("$"):
            ref = v[1:]
            if ref not in refs:
                raise InvalidArgument(f"unknown ref: {v}")
            out[k] = refs[ref]
        else:
            out[k] = v
    return out


def _apply_one(
    conn: sqlite3.Connection,
    op: dict[str, Any],
    refs: dict[str, str],
    *,
    request_id: str | None,
    simulate: bool,
) -> dict[str, Any]:
    entity = op["entity"]
    action = op["action"]
    if entity not in MUTABLE_ENTITIES:
        raise InvalidArgument(f"entity not mutable: {entity}")
    data = _resolve_refs(dict(op.get("data") or {}), refs)

    if action == "create":
        row = _create_entity(conn, entity, data, request_id=request_id)
        if op.get("ref"):
            refs[op["ref"]] = row["id"]
        return {
            "result": {"ref": op.get("ref"), "action": action, "entity": entity, "id": row["id"], "version": row["version"]},
            "diff": {"before": None, "after": row},
        }

    eid = op.get("id") or data.get("id")
    if not eid:
        raise InvalidArgument("id required for update/archive/set_status")
    expected = op.get("expected_version")
    if expected is None:
        raise InvalidArgument("expected_version required")

    if action == "archive":
        status_field = "status"
        archive_value = "archived" if entity == "category" else "cancelled"
        if entity == "matter":
            archive_value = "cancelled"
        data = {status_field: archive_value}
    elif action == "set_status":
        if "status" not in data:
            raise InvalidArgument("status required for set_status")
        data = {"status": data["status"], **{k: v for k, v in data.items() if k in {"completed_at"}}}

    patch = _normalize_patch(entity, data)
    if entity == "category" and "parent_id" in patch:
        if would_create_category_cycle(conn, eid, patch.get("parent_id")):
            raise InvalidArgument("category cycle detected")
    if entity == "stage" and "parent_stage_id" in patch:
        if would_create_stage_cycle(conn, eid, patch.get("parent_stage_id")):
            raise InvalidArgument("stage cycle detected")
        if patch.get("parent_stage_id"):
            parent = require_by_id(conn, "stage", patch["parent_stage_id"])
            current = require_by_id(conn, "stage", eid)
            if parent["matter_id"] != current["matter_id"]:
                raise InvalidArgument("parent stage must belong to same matter")

    before, after = update_versioned(conn, entity, eid, expected, patch)
    append_event(
        conn,
        event_type=f"{entity}.{action}",
        entity_type=entity,
        entity_id=eid,
        before=before,
        after=after,
        request_id=request_id,
    )
    return {
        "result": {"ref": op.get("ref"), "action": action, "entity": entity, "id": after["id"], "version": after["version"]},
        "diff": {"before": before, "after": after},
    }


def _normalize_patch(entity: str, data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for key in ("start_at", "end_at", "due_at", "remind_at", "completed_at"):
        if key in out and out[key] is not None:
            out[key] = to_utc_iso(out[key])
    return out


def _create_entity(
    conn: sqlite3.Connection, entity: str, data: dict[str, Any], request_id: str | None
) -> dict[str, Any]:
    now = utc_now_iso()
    if entity == "matter":
        row = insert_row(
            conn,
            "matter",
            {
                "id": new_id(),
                "category_id": data.get("category_id"),
                "title": data["title"],
                "description": data.get("description"),
                "status": data.get("status", "backlog"),
                "priority": data.get("priority"),
                "due_at": to_utc_iso(data.get("due_at")),
                "estimated_effort_minutes": data.get("estimated_effort_minutes"),
                "created_at": now,
                "updated_at": now,
                "completed_at": to_utc_iso(data.get("completed_at")),
                "version": 1,
            },
        )
    elif entity == "stage":
        row = create_stage(
            conn,
            matter_id=data["matter_id"],
            title=data["title"],
            parent_stage_id=data.get("parent_stage_id"),
            description=data.get("description"),
            sequence=data.get("sequence"),
            estimated_effort_minutes=data.get("estimated_effort_minutes"),
            completion_criteria=data.get("completion_criteria"),
            request_id=request_id,
        )
        return row
    elif entity == "plan":
        require_by_id(conn, "matter", data["matter_id"])
        ensure_stage_belongs_to_matter(conn, data.get("stage_id"), data["matter_id"])
        pdata = {
            "plan_type": data["plan_type"],
            "start_at": to_utc_iso(data.get("start_at")),
            "end_at": to_utc_iso(data.get("end_at")),
            "estimated_minutes": data.get("estimated_minutes"),
        }
        validate_plan_fields(pdata)
        row = insert_row(
            conn,
            "plan",
            {
                "id": new_id(),
                "matter_id": data["matter_id"],
                "stage_id": data.get("stage_id"),
                "plan_type": data["plan_type"],
                "start_at": pdata["start_at"],
                "end_at": pdata["end_at"],
                "estimated_minutes": data.get("estimated_minutes"),
                "expected_result": data.get("expected_result"),
                "status": data.get("status", "planned"),
                "flexibility": data.get("flexibility", "flexible"),
                "created_at": now,
                "updated_at": now,
                "version": 1,
            },
        )
    elif entity == "execution":
        validated = validate_execution_fields(
            {
                **data,
                "start_at": to_utc_iso(data.get("start_at")),
                "end_at": to_utc_iso(data.get("end_at")),
                "duration_precision": data.get("duration_precision", "approximate"),
            },
            conn,
        )
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
                "result": data.get("result", "unknown"),
                "output": data.get("output"),
                "note": data.get("note"),
                "created_at": now,
                "updated_at": now,
                "version": 1,
            },
        )
    elif entity == "observation":
        row = insert_row(
            conn,
            "observation",
            {
                "id": new_id(),
                "type": data["type"],
                "content": data["content"],
                "source": data.get("source", "user"),
                "matter_id": data.get("matter_id"),
                "execution_id": data.get("execution_id"),
                "confidence": data.get("confidence"),
                "status": data.get("status", "active"),
                "created_at": now,
                "updated_at": now,
                "version": 1,
            },
        )
        for ev in data.get("evidence") or []:
            conn.execute(
                """
                INSERT INTO observation_evidence(id, observation_id, evidence_type, evidence_id, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    row["id"],
                    ev.get("evidence_type", "note"),
                    ev.get("evidence_id"),
                    ev.get("note"),
                    now,
                ),
            )
    elif entity == "reminder":
        row = insert_row(
            conn,
            "reminder",
            {
                "id": new_id(),
                "matter_id": data.get("matter_id"),
                "stage_id": data.get("stage_id"),
                "plan_id": data.get("plan_id"),
                "title": data["title"],
                "remind_at": to_utc_iso(data["remind_at"]),
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
    elif entity == "category":
        if data.get("parent_id"):
            require_by_id(conn, "category", data["parent_id"])
        row = insert_row(
            conn,
            "category",
            {
                "id": new_id(),
                "name": data["name"],
                "description": data.get("description"),
                "parent_id": data.get("parent_id"),
                "status": data.get("status", "active"),
                "created_at": now,
                "updated_at": now,
                "version": 1,
            },
        )
        if would_create_category_cycle(conn, row["id"], row.get("parent_id")):
            raise InvalidArgument("category cycle detected")
    else:
        raise InvalidArgument(f"unsupported create entity: {entity}")

    append_event(
        conn,
        event_type=f"{entity}.created",
        entity_type=entity,
        entity_id=row["id"],
        after=row,
        request_id=request_id,
    )
    return row
