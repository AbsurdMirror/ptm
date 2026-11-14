"""Long-lived MCP stdio session: hundreds of calls, reconnect, FD sanity."""

from __future__ import annotations

import asyncio
import os
import resource
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.hard.evidence import record_requirement


async def _session_burst(data_dir: Path, n_calls: int, *, tag: str) -> dict:
    env = os.environ.copy()
    env["PTM_DATA_DIR"] = str(data_dir)
    env["PATH"] = f"/home/ubuntu/.local/bin:{env.get('PATH','')}"
    server = StdioServerParameters(
        command="uv",
        args=["run", "ptm-mcp"],
        cwd="/home/ubuntu/ptm",
        env=env,
    )
    oks = 0
    errors = 0
    t0 = time.perf_counter()
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            matter_ids: list[str] = []
            for i in range(n_calls):
                if i % 5 == 0:
                    r = await session.call_tool(
                        "ptm_capture",
                        {"content": f"long-{tag}-{i}", "request_id": f"long-c-{tag}-{i}"},
                    )
                    if r.is_error:
                        errors += 1
                    else:
                        oks += 1
                        matter_ids.append(r.structured_content["data"]["id"])
                elif i % 5 == 1 and matter_ids:
                    mid = matter_ids[i % len(matter_ids)]
                    r = await session.call_tool(
                        "ptm_record_execution",
                        {
                            "matter_id": mid,
                            "duration_minutes": 10,
                            "duration_precision": "approximate",
                            "result": "partial",
                            "note": "long",
                            "request_id": f"long-e-{tag}-{i}",
                        },
                    )
                    oks += 0 if r.is_error else 1
                    errors += 1 if r.is_error else 0
                elif i % 5 == 2:
                    r = await session.call_tool(
                        "ptm_agenda",
                        {
                            "date": datetime.now(timezone.utc).date().isoformat(),
                            "timezone": "UTC",
                            "limit": 10,
                        },
                    )
                    oks += 0 if r.is_error else 1
                    errors += 1 if r.is_error else 0
                elif i % 5 == 3:
                    past = (
                        datetime.now(timezone.utc) - timedelta(seconds=1)
                    ).isoformat().replace("+00:00", "Z")
                    r = await session.call_tool(
                        "ptm_reminder",
                        {
                            "action": "create",
                            "title": f"lr-{tag}-{i}",
                            "remind_at": past,
                            "request_id": f"long-r-{tag}-{i}",
                        },
                    )
                    if not r.is_error:
                        oks += 1
                        claim = await session.call_tool(
                            "ptm_reminder_dispatch",
                            {
                                "action": "claim_due",
                                "limit": 5,
                                "request_id": f"long-claim-{tag}-{i}",
                            },
                        )
                        if not claim.is_error and claim.structured_content["data"]["claimed"]:
                            item = claim.structured_content["data"]["claimed"][0]
                            ack = await session.call_tool(
                                "ptm_reminder_dispatch",
                                {
                                    "action": "ack",
                                    "reminder_id": item["id"],
                                    "claim_token": item["claim_token"],
                                    "ack_status": "submitted",
                                    "request_id": f"long-ack-{tag}-{i}",
                                },
                            )
                            oks += 0 if ack.is_error else 1
                            errors += 1 if ack.is_error else 0
                        else:
                            errors += 1 if claim.is_error else 0
                    else:
                        errors += 1
                else:
                    r = await session.call_tool(
                        "ptm_query",
                        {"entity": "matter", "limit": 20, "include_total": True},
                    )
                    oks += 0 if r.is_error else 1
                    errors += 1 if r.is_error else 0
    elapsed = time.perf_counter() - t0
    return {"oks": oks, "errors": errors, "elapsed": elapsed, "matters": len(matter_ids)}


@pytest.mark.asyncio
async def test_long_mcp_session_and_reconnect(tmp_path: Path, evidence_dir: Path) -> None:
    data1 = tmp_path / "long1"
    data1.mkdir()
    soft_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # open FD count approx
    fd_before = len(os.listdir(f"/proc/{os.getpid()}/fd"))

    burst1 = await _session_burst(data1, 300, tag="a")
    # reconnect new session same DB
    burst2 = await _session_burst(data1, 200, tag="b")

    fd_after = len(os.listdir(f"/proc/{os.getpid()}/fd"))
    soft_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    import sqlite3

    conn = sqlite3.connect(str(data1 / "ptm.sqlite3"))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        matters = conn.execute("SELECT COUNT(*) FROM matter").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    finally:
        conn.close()

    total_ok = burst1["oks"] + burst2["oks"]
    total_err = burst1["errors"] + burst2["errors"]
    elapsed = burst1["elapsed"] + burst2["elapsed"]
    # FD leak sanity: allow small slack for pytest/mcp internals
    fd_delta = fd_after - fd_before
    ok = (
        integrity == "ok"
        and total_err == 0
        and matters >= 100
        and events >= matters
        and fd_delta < 50
    )
    record_requirement(
        evidence_dir,
        "R-LONG-MCP-SESSION",
        status="proven" if ok else "failed",
        detail=(
            f"integrity={integrity} matters={matters} events={events} "
            f"oks={total_ok} errs={total_err} fd_delta={fd_delta} rss_kb={soft_before}->{soft_after}"
        ),
        metrics={
            "calls_ok": total_ok,
            "calls_err": total_err,
            "elapsed_sec": round(elapsed, 3),
            "ops_per_sec": round(total_ok / elapsed, 1) if elapsed else 0,
            "fd_delta": fd_delta,
            "matters": matters,
        },
    )
    assert ok
