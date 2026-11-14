"""Real MCP stdio protocol e2e tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_mcp_list_and_call_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "mcp-data"
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
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            assert names == sorted(
                [
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
                ]
            )

            result = await session.call_tool(
                "ptm_capture",
                {"kind": "matter", "content": "MCP e2e matter", "request_id": "e2e-1"},
            )
            assert result.is_error is False
            payload = result.structured_content
            assert payload is not None
            assert payload["ok"] is True
            matter_id = payload["data"]["id"]

            # business error path
            bad = await session.call_tool(
                "ptm_plan",
                {
                    "action": "update",
                    "plan_id": "00000000-0000-0000-0000-000000000000",
                    "expected_version": 1,
                },
            )
            assert bad.is_error is True
            assert bad.structured_content["ok"] is False
            assert bad.structured_content["error"]["code"] in {"NOT_FOUND", "INVALID_ARGUMENT"}

            q = await session.call_tool(
                "ptm_query",
                {
                    "entity": "matter",
                    "filters": {"all": [{"field": "id", "op": "=", "value": matter_id}]},
                },
            )
            assert q.is_error is False
            assert q.structured_content["data"]["items"][0]["id"] == matter_id

    # prove persistence on disk after process exit
    import sqlite3

    db = data_dir / "ptm.sqlite3"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT title FROM matter WHERE id=?", (matter_id,)).fetchone()
        assert row[0] == "MCP e2e matter"
        events = conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
        assert events >= 1
    finally:
        conn.close()
