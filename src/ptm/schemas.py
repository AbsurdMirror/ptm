"""Pydantic v2 input/output schemas for MCP tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolSuccess(StrictModel):
    ok: Literal[True] = True
    data: Any
    warnings: list[str] = Field(default_factory=list)


class ToolErrorBody(StrictModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ToolFailure(StrictModel):
    ok: Literal[False] = False
    error: ToolErrorBody


# --- capture ---
class CaptureInput(StrictModel):
    kind: Literal["matter", "observation"] = "matter"
    content: str = Field(min_length=1, max_length=4000)
    category_id: str | None = None
    related_id: str | None = None
    occurred_at: str | None = None
    request_id: str | None = None
    observation_type: Literal["feedback", "preference", "pattern"] = "feedback"
    source: Literal["user", "ai", "statistics"] = "user"


# --- agenda ---
class AgendaInput(StrictModel):
    date: str
    timezone: str = "Asia/Shanghai"
    include_backlog: bool = True
    limit: int = Field(default=20, ge=1, le=100)


# --- record execution ---
class RecordExecutionInput(StrictModel):
    matter_id: str | None = None
    stage_id: str | None = None
    plan_id: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    duration_minutes: int | None = Field(default=None, ge=0)
    duration_precision: Literal["exact", "approximate", "unknown"] = "approximate"
    result: Literal["completed", "partial", "interrupted", "unknown"] = "unknown"
    output: str | None = None
    note: str | None = None
    request_id: str | None = None


# --- plan ---
class PlanInput(StrictModel):
    action: Literal["create", "update", "cancel", "complete"]
    plan_id: str | None = None
    matter_id: str | None = None
    stage_id: str | None = None
    plan_type: Literal["time_block", "time_budget"] | None = None
    start_at: str | None = None
    end_at: str | None = None
    estimated_minutes: int | None = Field(default=None, ge=1)
    expected_result: str | None = None
    flexibility: Literal["fixed", "flexible"] = "flexible"
    expected_version: int | None = None
    request_id: str | None = None
    patch: dict[str, Any] | None = None


# --- review ---
class ReviewInput(StrictModel):
    start_date: str
    end_date: str
    timezone: str = "Asia/Shanghai"
    detail: Literal["summary", "standard"] = "summary"


# --- query ---
class QueryFilter(StrictModel):
    field: str
    op: Literal["=", "!=", ">", ">=", "<", "<=", "in", "not_in", "is_null", "is_not_null"]
    value: Any = None


class QueryFilterGroup(StrictModel):
    all: list[QueryFilter | QueryFilterGroup] | None = None
    any: list[QueryFilter | QueryFilterGroup] | None = None


class OrderBy(StrictModel):
    field: str
    direction: Literal["asc", "desc"] = "asc"


class QueryInput(StrictModel):
    entity: Literal[
        "category", "matter", "stage", "plan", "execution", "observation", "event", "reminder"
    ]
    filters: QueryFilterGroup | dict[str, Any] | None = None
    include: list[str] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None
    fields: list[str] | None = None
    include_total: bool = False


# --- mutate ---
class MutateOperation(StrictModel):
    ref: str | None = None
    action: Literal["create", "update", "archive", "set_status"]
    entity: Literal[
        "category", "matter", "stage", "plan", "execution", "observation", "reminder"
    ]
    id: str | None = None
    expected_version: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class MutateInput(StrictModel):
    operations: list[MutateOperation] = Field(min_length=1, max_length=20)
    dry_run: bool = False
    request_id: str | None = None
    preview_token: str | None = None
    confirmed: bool = False


# --- schema ---
class SchemaInput(StrictModel):
    entity: str | None = None


# --- stats ---
class StatsInput(StrictModel):
    metric: Literal[
        "time_by_category",
        "time_by_matter",
        "time_by_stage",
        "plan_vs_execution",
        "completion_summary",
        "estimate_accuracy",
    ]
    start_at: str | None = None
    end_at: str | None = None
    category_id: str | None = None
    matter_id: str | None = None


# --- reminder ---
class ReminderInput(StrictModel):
    action: Literal["create", "update", "cancel", "get", "list"]
    reminder_id: str | None = None
    matter_id: str | None = None
    stage_id: str | None = None
    plan_id: str | None = None
    title: str | None = None
    remind_at: str | None = None
    expected_version: int | None = None
    status: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    request_id: str | None = None
    patch: dict[str, Any] | None = None


class ReminderDispatchInput(StrictModel):
    action: Literal["claim_due", "ack"]
    limit: int = Field(default=10, ge=1, le=50)
    lease_seconds: int = Field(default=300, ge=30, le=3600)
    claim_token: str | None = None
    reminder_id: str | None = None
    ack_status: Literal["submitted", "delivered", "failed"] | None = None
    error: str | None = None
    request_id: str | None = None
