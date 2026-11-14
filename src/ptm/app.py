"""Application facade used by MCP tools and CLI."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfoNotFoundError

from pydantic import ValidationError

from ptm.config import Settings, get_settings
from ptm.database import connect, initialize_database, transaction
from ptm.errors import InvalidArgument, PtmError, StorageError
from ptm.schemas import (
    AgendaInput,
    CaptureInput,
    MutateInput,
    PlanInput,
    QueryInput,
    RecordExecutionInput,
    ReminderDispatchInput,
    ReminderInput,
    ReviewInput,
    SchemaInput,
    StatsInput,
    ToolFailure,
    ToolSuccess,
)
from ptm.services.agenda import get_agenda, get_review
from ptm.services.executions import record_execution
from ptm.services.matters import capture
from ptm.services.mutations import mutate
from ptm.services.plans import plan_action
from ptm.services.queries import run_query
from ptm.services.reminders import reminder_action, reminder_dispatch
from ptm.services.schema_info import describe_schema
from ptm.services.statistics import compute_stats


class PtmApp:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        initialize_database(self.settings)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            data = self._dispatch(tool_name, arguments)
            return ToolSuccess(data=data).model_dump()
        except ValidationError as exc:
            return ToolFailure(
                error=InvalidArgument(
                    "invalid tool arguments",
                    details={"errors": exc.errors()},
                ).to_dict()
            ).model_dump()  # type: ignore[arg-type]
        except ZoneInfoNotFoundError as exc:
            return ToolFailure(
                error=InvalidArgument(
                    "unknown timezone",
                    details={"timezone": str(exc)},
                ).to_dict()
            ).model_dump()  # type: ignore[arg-type]
        except PtmError as exc:
            return ToolFailure(error=exc.to_dict()).model_dump()  # type: ignore[arg-type]
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
            return ToolFailure(
                error=StorageError(
                    "storage failure",
                    details={"sqlite": str(exc)},
                ).to_dict()
            ).model_dump()  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            return ToolFailure(
                error={
                    "code": "INTERNAL_ERROR",
                    "message": str(exc),
                    "retryable": False,
                    "details": {},
                }
            ).model_dump()

    def _dispatch(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        with self.connection() as conn:
            with transaction(conn):
                if tool_name == "ptm_capture":
                    inp = CaptureInput.model_validate(arguments)
                    return capture(conn, **inp.model_dump())
                if tool_name == "ptm_agenda":
                    inp = AgendaInput.model_validate(arguments)
                    return get_agenda(conn, **inp.model_dump())
                if tool_name == "ptm_record_execution":
                    inp = RecordExecutionInput.model_validate(arguments)
                    return record_execution(conn, **inp.model_dump())
                if tool_name == "ptm_plan":
                    inp = PlanInput.model_validate(arguments)
                    return plan_action(conn, **inp.model_dump())
                if tool_name == "ptm_review":
                    inp = ReviewInput.model_validate(arguments)
                    return get_review(conn, **inp.model_dump())
                if tool_name == "ptm_query":
                    inp = QueryInput.model_validate(arguments)
                    filters = inp.filters
                    if hasattr(filters, "model_dump"):
                        filters = filters.model_dump(exclude_none=True)
                    return run_query(
                        conn,
                        entity=inp.entity,
                        filters=filters,
                        include=inp.include,
                        order_by=[o.model_dump() for o in inp.order_by],
                        limit=inp.limit,
                        cursor=inp.cursor,
                        fields=inp.fields,
                        include_total=inp.include_total,
                    )
                if tool_name == "ptm_mutate":
                    inp = MutateInput.model_validate(arguments)
                    return mutate(
                        conn,
                        operations=[o.model_dump() for o in inp.operations],
                        dry_run=inp.dry_run,
                        request_id=inp.request_id,
                        preview_token=inp.preview_token,
                        confirmed=inp.confirmed,
                    )
                if tool_name == "ptm_schema":
                    inp = SchemaInput.model_validate(arguments or {})
                    return describe_schema(inp.entity)
                if tool_name == "ptm_stats":
                    inp = StatsInput.model_validate(arguments)
                    return compute_stats(conn, **inp.model_dump())
                if tool_name == "ptm_reminder":
                    inp = ReminderInput.model_validate(arguments)
                    return reminder_action(conn, **inp.model_dump())
                if tool_name == "ptm_reminder_dispatch":
                    inp = ReminderDispatchInput.model_validate(arguments)
                    return reminder_dispatch(conn, **inp.model_dump())
                raise PtmError(f"unknown tool: {tool_name}", code="INVALID_ARGUMENT")


def make_app(data_dir: str | Path | None = None) -> PtmApp:
    return PtmApp(get_settings(data_dir))
