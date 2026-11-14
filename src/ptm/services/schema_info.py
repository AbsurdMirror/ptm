"""Capability discovery derived from live model definitions."""

from __future__ import annotations

from typing import Any

from ptm.config import SCHEMA_VERSION
from ptm.models import (
    CategoryStatus,
    DurationPrecision,
    ExecutionResult,
    Flexibility,
    MatterStatus,
    ObservationSource,
    ObservationStatus,
    ObservationType,
    PlanStatus,
    PlanType,
    Priority,
    QUERYABLE_ENTITIES,
    ReminderStatus,
    StageStatus,
)
from ptm.repositories.common import TABLE_COLUMNS


ENTITY_META: dict[str, dict[str, Any]] = {
    "category": {
        "operations": ["create", "update", "archive", "set_status", "query"],
        "enums": {"status": [e.value for e in CategoryStatus]},
        "writable": ["name", "description", "parent_id", "status"],
        "relations": {"parent": "category"},
    },
    "matter": {
        "operations": ["create", "update", "archive", "set_status", "query"],
        "enums": {
            "status": [e.value for e in MatterStatus],
            "priority": [e.value for e in Priority],
        },
        "writable": [
            "category_id", "title", "description", "status", "priority",
            "due_at", "estimated_effort_minutes", "completed_at",
        ],
        "relations": {"category": "category", "stages": "stage", "plans": "plan", "executions": "execution"},
    },
    "stage": {
        "operations": ["create", "update", "archive", "set_status", "query"],
        "enums": {"status": [e.value for e in StageStatus]},
        "writable": [
            "matter_id", "parent_stage_id", "title", "description", "status",
            "sequence", "estimated_effort_minutes", "completion_criteria", "completed_at",
        ],
        "relations": {"matter": "matter", "parent": "stage"},
    },
    "plan": {
        "operations": ["create", "update", "archive", "set_status", "query"],
        "enums": {
            "plan_type": [e.value for e in PlanType],
            "status": [e.value for e in PlanStatus],
            "flexibility": [e.value for e in Flexibility],
        },
        "writable": [
            "matter_id", "stage_id", "plan_type", "start_at", "end_at",
            "estimated_minutes", "expected_result", "status", "flexibility",
        ],
        "relations": {"matter": "matter", "stage": "stage", "executions": "execution"},
    },
    "execution": {
        "operations": ["create", "update", "query"],
        "enums": {
            "duration_precision": [e.value for e in DurationPrecision],
            "result": [e.value for e in ExecutionResult],
        },
        "writable": [
            "matter_id", "stage_id", "plan_id", "start_at", "end_at",
            "duration_minutes", "duration_precision", "result", "output", "note",
        ],
        "relations": {"matter": "matter", "stage": "stage", "plan": "plan"},
    },
    "observation": {
        "operations": ["create", "update", "set_status", "query"],
        "enums": {
            "type": [e.value for e in ObservationType],
            "source": [e.value for e in ObservationSource],
            "status": [e.value for e in ObservationStatus],
        },
        "writable": [
            "type", "content", "source", "matter_id", "execution_id", "confidence", "status",
        ],
        "relations": {"matter": "matter", "execution": "execution", "evidence": "observation_evidence"},
    },
    "event": {
        "operations": ["query"],
        "enums": {},
        "writable": [],
        "relations": {},
        "notes": "append-only; not mutable via tools",
    },
    "reminder": {
        "operations": ["create", "update", "cancel", "query", "dispatch"],
        "enums": {"status": [e.value for e in ReminderStatus]},
        "writable": ["matter_id", "stage_id", "plan_id", "title", "remind_at", "status"],
        "relations": {"matter": "matter", "stage": "stage", "plan": "plan"},
    },
}


def describe_schema(entity: str | None = None) -> dict[str, Any]:
    if entity:
        if entity not in ENTITY_META:
            return {
                "schema_version": SCHEMA_VERSION,
                "error": f"unknown entity: {entity}",
                "entities": sorted(ENTITY_META),
            }
        meta = ENTITY_META[entity]
        fields = sorted(TABLE_COLUMNS.get(entity, set()))
        return {
            "schema_version": SCHEMA_VERSION,
            "entity": entity,
            "fields": fields,
            "writable_fields": meta["writable"],
            "enums": meta["enums"],
            "operations": meta["operations"],
            "relations": meta["relations"],
            "queryable": entity in QUERYABLE_ENTITIES,
            "notes": meta.get("notes"),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "entities": {
            name: {
                "operations": meta["operations"],
                "writable_fields": meta["writable"],
                "enums": meta["enums"],
                "queryable": name in QUERYABLE_ENTITIES,
            }
            for name, meta in ENTITY_META.items()
        },
        "tools": [
            "ptm_capture",
            "ptm_agenda",
            "ptm_record_execution",
            "ptm_plan",
            "ptm_review",
            "ptm_query",
            "ptm_mutate",
            "ptm_schema",
            "ptm_stats",
            "ptm_reminder",
            "ptm_reminder_dispatch",
        ],
    }
