"""MCP protocol edge cases over real stdio."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_mcp_invalid_args_and_idempotent_write(tmp_path: Path) -> None:
    data_dir = tmp_path / "mcp-edge"
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
            by_name = {t.name: t for t in tools.tools}
            assert "ptm_capture" in by_name
            assert by_name["ptm_agenda"].annotations is not None
            assert by_name["ptm_agenda"].annotations.read_only_hint is True
            assert by_name["ptm_mutate"].annotations.destructive_hint is True

            bad = await session.call_tool("ptm_capture", {"content": ""})
            assert bad.is_error is True
            assert bad.structured_content["ok"] is False
            assert bad.structured_content["error"]["code"] == "INVALID_ARGUMENT"

            first = await session.call_tool(
                "ptm_capture",
                {"content": "edge matter", "request_id": "mcp-edge-1"},
            )
            assert first.is_error is False
            mid = first.structured_content["data"]["id"]

            replay = await session.call_tool(
                "ptm_capture",
                {"content": "edge matter", "request_id": "mcp-edge-1"},
            )
            assert replay.is_error is False
            assert replay.structured_content["data"]["id"] == mid

            clash = await session.call_tool(
                "ptm_capture",
                {"content": "different", "request_id": "mcp-edge-1"},
            )
            assert clash.is_error is True
            assert clash.structured_content["error"]["code"] == "DUPLICATE_REQUEST"

            # mutate create-only via MCP then query
            mut = await session.call_tool(
                "ptm_mutate",
                {
                    "operations": [
                        {
                            "ref": "ex",
                            "action": "create",
                            "entity": "execution",
                            "data": {
                                "matter_id": mid,
                                "duration_minutes": 30,
                                "duration_precision": "approximate",
                                "result": "partial",
                                "note": "mcp",
                            },
                        }
                    ],
                    "request_id": "mcp-mut-1",
                },
            )
            assert mut.is_error is False
            assert mut.structured_content["data"]["refs"]["ex"]

            # unknown tool → MCP error result (distinct from business ok:false payload)
            unknown = await session.call_tool("ptm_not_a_tool", {})
            assert unknown.is_error is True
            # business envelope is absent or not ok:true
            sc = unknown.structured_content
            assert sc is None or sc.get("ok") is not True

    import sqlite3

    conn = sqlite3.connect(str(data_dir / "ptm.sqlite3"))
    try:
        n = conn.execute("SELECT COUNT(*) FROM matter WHERE id=?", (mid,)).fetchone()[0]
        assert n == 1
        n_ex = conn.execute("SELECT COUNT(*) FROM execution").fetchone()[0]
        assert n_ex == 1
    finally:
        conn.close()
