"""Real MCP stdio coverage across more tools."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_mcp_tools_happy_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "mcp-broad"
    data_dir.mkdir()
    env = os.environ.copy()
    env["PTM_DATA_DIR"] = str(data_dir)
    env["PTM_TIMEZONE"] = "Asia/Shanghai"
    server = StdioServerParameters(
        command="uv",
        args=["run", "ptm-mcp"],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            assert len(tools) == 11

            cap = await session.call_tool(
                "ptm_capture", {"content": "MCP broad matter", "request_id": "mb-1"}
            )
            assert cap.is_error is False
            mid = cap.structured_content["data"]["id"]

            schema = await session.call_tool("ptm_schema", {"entity": "matter"})
            assert schema.is_error is False
            assert "status" in schema.structured_content["data"]["enums"]

            start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
            plan = await session.call_tool(
                "ptm_plan",
                {
                    "action": "create",
                    "matter_id": mid,
                    "plan_type": "time_block",
                    "start_at": start,
                    "end_at": end,
                    "request_id": "mb-2",
                },
            )
            assert plan.is_error is False

            agenda = await session.call_tool(
                "ptm_agenda",
                {"date": datetime.now(timezone.utc).date().isoformat(), "timezone": "UTC"},
            )
            assert agenda.is_error is False

            exe = await session.call_tool(
                "ptm_record_execution",
                {
                    "matter_id": mid,
                    "duration_minutes": 25,
                    "duration_precision": "approximate",
                    "result": "partial",
                    "note": "mcp",
                    "request_id": "mb-3",
                },
            )
            assert exe.is_error is False

            review = await session.call_tool(
                "ptm_review",
                {
                    "start_date": (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
                    "end_date": datetime.now(timezone.utc).date().isoformat(),
                    "timezone": "UTC",
                },
            )
            assert review.is_error is False

            stats = await session.call_tool(
                "ptm_stats", {"metric": "completion_summary"}
            )
            assert stats.is_error is False

            q = await session.call_tool(
                "ptm_query",
                {
                    "entity": "matter",
                    "filters": {"all": [{"field": "id", "op": "=", "value": mid}]},
                },
            )
            assert q.is_error is False
            assert q.structured_content["data"]["items"][0]["id"] == mid

            past = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
            rem = await session.call_tool(
                "ptm_reminder",
                {
                    "action": "create",
                    "title": "mcp rem",
                    "remind_at": past,
                    "request_id": "mb-4",
                },
            )
            assert rem.is_error is False
            rid = rem.structured_content["data"]["reminder"]["id"]

            claim = await session.call_tool(
                "ptm_reminder_dispatch",
                {"action": "claim_due", "limit": 5, "request_id": "mb-5"},
            )
            assert claim.is_error is False
            token = next(
                c["claim_token"]
                for c in claim.structured_content["data"]["claimed"]
                if c["id"] == rid
            )
            ack = await session.call_tool(
                "ptm_reminder_dispatch",
                {
                    "action": "ack",
                    "reminder_id": rid,
                    "claim_token": token,
                    "ack_status": "delivered",
                    "request_id": "mb-6",
                },
            )
            assert ack.is_error is False
            assert ack.structured_content["data"]["reminder"]["status"] == "delivered"

    import sqlite3

    conn = sqlite3.connect(str(data_dir / "ptm.sqlite3"))
    try:
        assert conn.execute("SELECT status FROM reminder WHERE id=?", (rid,)).fetchone()[0] == "delivered"
        assert conn.execute("SELECT COUNT(*) FROM execution").fetchone()[0] >= 1
    finally:
        conn.close()
