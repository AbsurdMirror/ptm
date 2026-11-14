"""MCP stdio server exposing 11 PTM tools."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from ptm.app import PtmApp, make_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("ptm.mcp")

_app: PtmApp | None = None


def get_app() -> PtmApp:
    global _app
    if _app is None:
        _app = make_app()
    return _app


def _result(payload: dict[str, Any]) -> CallToolResult:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
        is_error=payload.get("ok") is False,
    )


def create_server() -> MCPServer:
    server = MCPServer(
        name="ptm",
        title="Personal Time Manager",
        description="AI personal time manager MCP server (V0.3)",
        version="0.3.0",
        instructions=(
            "Persist matters, plans, executions, observations, and reminders. "
            "Prefer dedicated tools; use ptm_query/ptm_mutate as controlled fallbacks. "
            "Do not invent exact times for approximate durations."
        ),
    )

    @server.tool(
        name="ptm_capture",
        title="Capture matter or observation",
        description=(
            "Record a new matter or observation from natural language. "
            "Use for new ideas/todos/feedback. Does not invent due dates or merge similar titles. "
            "Do not use for execution logging (use ptm_record_execution)."
        ),
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True),
    )
    def ptm_capture(
        content: str,
        kind: str = "matter",
        category_id: str | None = None,
        related_id: str | None = None,
        occurred_at: str | None = None,
        request_id: str | None = None,
        observation_type: str = "feedback",
        source: str = "user",
    ) -> Any:
        return _result(
            get_app().call(
                "ptm_capture",
                {
                    "kind": kind,
                    "content": content,
                    "category_id": category_id,
                    "related_id": related_id,
                    "occurred_at": occurred_at,
                    "request_id": request_id,
                    "observation_type": observation_type,
                    "source": source,
                },
            )
        )

    @server.tool(
        name="ptm_agenda",
        title="Daily agenda",
        description=(
            "Get plans, overdue plans, due matters, active matters without plan, "
            "and backlog candidates for a date. Read-only. Backlog items are candidates only."
        ),
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
    )
    def ptm_agenda(
        date: str,
        timezone: str = "Asia/Shanghai",
        include_backlog: bool = True,
        limit: int = 20,
    ) -> Any:
        return _result(
            get_app().call(
                "ptm_agenda",
                {
                    "date": date,
                    "timezone": timezone,
                    "include_backlog": include_backlog,
                    "limit": limit,
                },
            )
        )

    @server.tool(
        name="ptm_record_execution",
        title="Record execution",
        description=(
            "Record actual time spent. Allows approximate duration without fabricating start/end. "
            "Does not auto-complete the related matter."
        ),
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True),
    )
    def ptm_record_execution(
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
    ) -> Any:
        return _result(
            get_app().call(
                "ptm_record_execution",
                {
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
                    "request_id": request_id,
                },
            )
        )

    @server.tool(
        name="ptm_plan",
        title="Create or adjust plan",
        description=(
            "Create/update/cancel/complete a plan. Detects conflicts but does not silently cancel others. "
            "Updates require expected_version. Linked reminders that may need sync are returned."
        ),
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True),
    )
    def ptm_plan(
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
    ) -> Any:
        return _result(
            get_app().call(
                "ptm_plan",
                {
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
                    "request_id": request_id,
                    "patch": patch,
                },
            )
        )

    @server.tool(
        name="ptm_review",
        title="Period review data",
        description=(
            "Return factual review stats for a date range: plan status, recorded effort, "
            "category totals, deviations, feedback, stagnant matters. No narrative judgment."
        ),
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
    )
    def ptm_review(
        start_date: str,
        end_date: str,
        timezone: str = "Asia/Shanghai",
        detail: str = "summary",
    ) -> Any:
        return _result(
            get_app().call(
                "ptm_review",
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "timezone": timezone,
                    "detail": detail,
                },
            )
        )

    @server.tool(
        name="ptm_query",
        title="Controlled query",
        description=(
            "Read-only whitelist query across entities with filters, paging, and limited includes. "
            "No writes. Default limit 50, max 200."
        ),
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
    )
    def ptm_query(
        entity: str,
        filters: dict[str, Any] | None = None,
        include: list[str] | None = None,
        order_by: list[dict[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
        fields: list[str] | None = None,
        include_total: bool = False,
    ) -> Any:
        return _result(
            get_app().call(
                "ptm_query",
                {
                    "entity": entity,
                    "filters": filters,
                    "include": include or [],
                    "order_by": order_by or [],
                    "limit": limit,
                    "cursor": cursor,
                    "fields": fields,
                    "include_total": include_total,
                },
            )
        )

    @server.tool(
        name="ptm_mutate",
        title="Atomic batch mutate",
        description=(
            "Controlled atomic batch create/update/archive/set_status with optional dry_run preview_token. "
            "Max 20 ops. Supports $ref to earlier creates. Risky batches need confirmation/preview."
        ),
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True),
    )
    def ptm_mutate(
        operations: list[dict[str, Any]],
        dry_run: bool = False,
        request_id: str | None = None,
        preview_token: str | None = None,
        confirmed: bool = False,
    ) -> Any:
        return _result(
            get_app().call(
                "ptm_mutate",
                {
                    "operations": operations,
                    "dry_run": dry_run,
                    "request_id": request_id,
                    "preview_token": preview_token,
                    "confirmed": confirmed,
                },
            )
        )

    @server.tool(
        name="ptm_schema",
        title="Schema discovery",
        description="Describe entities, writable fields, enums, and supported operations from live model metadata.",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
    )
    def ptm_schema(entity: str | None = None) -> Any:
        return _result(get_app().call("ptm_schema", {"entity": entity}))

    @server.tool(
        name="ptm_stats",
        title="Statistics",
        description=(
            "Descriptive stats: time_by_category/matter/stage, plan_vs_execution, "
            "completion_summary, estimate_accuracy. Missing records are not treated as zero."
        ),
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
    )
    def ptm_stats(
        metric: str,
        start_at: str | None = None,
        end_at: str | None = None,
        category_id: str | None = None,
        matter_id: str | None = None,
    ) -> Any:
        return _result(
            get_app().call(
                "ptm_stats",
                {
                    "metric": metric,
                    "start_at": start_at,
                    "end_at": end_at,
                    "category_id": category_id,
                    "matter_id": matter_id,
                },
            )
        )

    @server.tool(
        name="ptm_reminder",
        title="Manage reminders",
        description="create/update/cancel/get/list reminders. Updates require expected_version.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True),
    )
    def ptm_reminder(
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
    ) -> Any:
        return _result(
            get_app().call(
                "ptm_reminder",
                {
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
                    "request_id": request_id,
                    "patch": patch,
                },
            )
        )

    @server.tool(
        name="ptm_reminder_dispatch",
        title="Dispatch due reminders",
        description=(
            "claim_due atomically leases due reminders; ack confirms submitted/delivered/failed "
            "with claim_token. At-least-once; does not claim exactly-once delivery."
        ),
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True),
    )
    def ptm_reminder_dispatch(
        action: str,
        limit: int = 10,
        lease_seconds: int = 300,
        claim_token: str | None = None,
        reminder_id: str | None = None,
        ack_status: str | None = None,
        error: str | None = None,
        request_id: str | None = None,
    ) -> Any:
        return _result(
            get_app().call(
                "ptm_reminder_dispatch",
                {
                    "action": action,
                    "limit": limit,
                    "lease_seconds": lease_seconds,
                    "claim_token": claim_token,
                    "reminder_id": reminder_id,
                    "ack_status": ack_status,
                    "error": error,
                    "request_id": request_id,
                },
            )
        )

    return server


def main() -> None:
    # stdout reserved for MCP protocol
    logger.info("Starting PTM MCP stdio server")
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
