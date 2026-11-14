"""Machine-checkable V0.3 requirement falsification matrix.

Each test proves or fails a named requirement and appends evidence JSONL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ptm.app import PtmApp
from ptm.timeutil import to_utc_iso
from tests.hard.evidence import record_requirement


def _ok(app: PtmApp, tool: str, args: dict) -> dict:
    r = app.call(tool, args)
    assert r["ok"] is True, r
    return r["data"]


def test_R_seed_six_categories(hard_app: PtmApp, evidence_dir: Path) -> None:
    with hard_app.connection() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM category WHERE status='active'")}
    expected = {"工作", "学习", "运动健康", "日常生活", "社交关系", "娱乐休闲"}
    if names == expected:
        record_requirement(
            evidence_dir,
            "R-CAT-SEED-6",
            status="proven",
            detail=f"seed categories={sorted(names)}",
        )
    else:
        record_requirement(
            evidence_dir,
            "R-CAT-SEED-6",
            status="failed",
            detail=f"got={sorted(names)} expected={sorted(expected)}",
        )
        pytest.fail("seed categories mismatch")


def test_R_version_starts_at_1_and_increments(hard_app: PtmApp, evidence_dir: Path) -> None:
    d = _ok(hard_app, "ptm_capture", {"content": "ver", "request_id": "mx-v1"})
    with hard_app.connection() as conn:
        v1 = conn.execute("SELECT version FROM matter WHERE id=?", (d["id"],)).fetchone()[0]
    ops = [
        {
            "action": "update",
            "entity": "matter",
            "id": d["id"],
            "expected_version": v1,
            "data": {"title": "ver2"},
        }
    ]
    prev = _ok(hard_app, "ptm_mutate", {"operations": ops, "dry_run": True})
    sub = _ok(
        hard_app,
        "ptm_mutate",
        {"operations": ops, "preview_token": prev["preview_token"], "request_id": "mx-v2"},
    )
    with hard_app.connection() as conn:
        v2 = conn.execute("SELECT version FROM matter WHERE id=?", (d["id"],)).fetchone()[0]
    if v1 == 1 and v2 == 2:
        record_requirement(
            evidence_dir, "R-VERSION-INC", status="proven", detail=f"v1={v1} v2={v2}"
        )
    else:
        record_requirement(
            evidence_dir, "R-VERSION-INC", status="failed", detail=f"v1={v1} v2={v2}"
        )
        pytest.fail("version invariant broken")


def test_R_idempotency_same_and_conflict(hard_app: PtmApp, evidence_dir: Path) -> None:
    a = _ok(hard_app, "ptm_capture", {"content": "idem", "request_id": "mx-idem"})
    b = _ok(hard_app, "ptm_capture", {"content": "idem", "request_id": "mx-idem"})
    c = hard_app.call("ptm_capture", {"content": "other", "request_id": "mx-idem"})
    with hard_app.connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM matter WHERE title='idem'").fetchone()[0]
    ok = a["id"] == b["id"] and n == 1 and c["ok"] is False and c["error"]["code"] == "DUPLICATE_REQUEST"
    record_requirement(
        evidence_dir,
        "R-IDEMPOTENCY",
        status="proven" if ok else "failed",
        detail=f"same_id={a['id']==b['id']} count={n} clash={c.get('error')}",
    )
    assert ok


def test_R_optimistic_lock_no_overwrite(hard_app: PtmApp, evidence_dir: Path) -> None:
    d = _ok(hard_app, "ptm_capture", {"content": "lock", "request_id": "mx-lock"})
    ops_bad = [
        {
            "action": "update",
            "entity": "matter",
            "id": d["id"],
            "expected_version": 999,
            "data": {"title": "hack"},
        }
    ]
    prev = hard_app.call("ptm_mutate", {"operations": ops_bad, "dry_run": True})
    ok = prev["ok"] is False and prev["error"]["code"] == "VERSION_CONFLICT"
    with hard_app.connection() as conn:
        title = conn.execute("SELECT title FROM matter WHERE id=?", (d["id"],)).fetchone()[0]
    ok = ok and title == "lock"
    record_requirement(
        evidence_dir,
        "R-OPTIMISTIC-LOCK",
        status="proven" if ok else "failed",
        detail=f"code={prev.get('error')} title={title}",
    )
    assert ok


def test_R_execution_no_fabricate_and_no_auto_complete(hard_app: PtmApp, evidence_dir: Path) -> None:
    m = _ok(hard_app, "ptm_capture", {"content": "ex", "request_id": "mx-ex"})
    e = _ok(
        hard_app,
        "ptm_record_execution",
        {
            "matter_id": m["id"],
            "duration_minutes": 120,
            "duration_precision": "approximate",
            "result": "partial",
            "note": "大约两小时",
            "request_id": "mx-ex1",
        },
    )
    with hard_app.connection() as conn:
        row = conn.execute(
            "SELECT start_at, end_at, duration_precision FROM execution WHERE id=?",
            (e["id"],),
        ).fetchone()
        status = conn.execute("SELECT status FROM matter WHERE id=?", (m["id"],)).fetchone()[0]
    ok = (
        row["start_at"] is None
        and row["end_at"] is None
        and row["duration_precision"] == "approximate"
        and status != "completed"
        and e["matter_auto_completed"] is False
    )
    record_requirement(
        evidence_dir,
        "R-EXEC-APPROX-NO-FABRICATE",
        status="proven" if ok else "failed",
        detail=f"row={dict(row)} matter_status={status}",
    )
    assert ok


def test_R_plan_types_and_conflict_no_silent_cancel(hard_app: PtmApp, evidence_dir: Path) -> None:
    m = _ok(hard_app, "ptm_capture", {"content": "plan", "request_id": "mx-p"})
    start = datetime(2027, 1, 1, 10, 0, tzinfo=timezone.utc)
    p1 = _ok(
        hard_app,
        "ptm_plan",
        {
            "action": "create",
            "matter_id": m["id"],
            "plan_type": "time_block",
            "start_at": to_utc_iso(start),
            "end_at": to_utc_iso(start + timedelta(hours=1)),
            "request_id": "mx-p1",
        },
    )
    p2 = _ok(
        hard_app,
        "ptm_plan",
        {
            "action": "create",
            "matter_id": m["id"],
            "plan_type": "time_block",
            "start_at": to_utc_iso(start + timedelta(minutes=30)),
            "end_at": to_utc_iso(start + timedelta(hours=1, minutes=30)),
            "request_id": "mx-p2",
        },
    )
    budget = _ok(
        hard_app,
        "ptm_plan",
        {
            "action": "create",
            "matter_id": m["id"],
            "plan_type": "time_budget",
            "estimated_minutes": 45,
            "request_id": "mx-pb",
        },
    )
    with hard_app.connection() as conn:
        planned = conn.execute(
            "SELECT COUNT(*) FROM plan WHERE matter_id=? AND status='planned'", (m["id"],)
        ).fetchone()[0]
    ok = len(p2["conflicts"]) >= 1 and planned == 3 and budget["plan"]["start_at"] is None
    record_requirement(
        evidence_dir,
        "R-PLAN-CONFLICT-NO-SILENT-CANCEL",
        status="proven" if ok else "failed",
        detail=f"conflicts={len(p2['conflicts'])} planned={planned}",
    )
    assert ok


def test_R_reminder_state_machine(hard_app: PtmApp, evidence_dir: Path) -> None:
    past = to_utc_iso(datetime.now(timezone.utc) - timedelta(minutes=1))
    rem = _ok(
        hard_app,
        "ptm_reminder",
        {"action": "create", "title": "due", "remind_at": past, "request_id": "mx-r1"},
    )
    claim = _ok(hard_app, "ptm_reminder_dispatch", {"action": "claim_due", "limit": 5, "request_id": "mx-rc"})
    item = next(c for c in claim["claimed"] if c["id"] == rem["reminder"]["id"])
    # wrong token
    bad = hard_app.call(
        "ptm_reminder_dispatch",
        {
            "action": "ack",
            "reminder_id": item["id"],
            "claim_token": "wrong",
            "ack_status": "delivered",
            "request_id": "mx-bad",
        },
    )
    ack = _ok(
        hard_app,
        "ptm_reminder_dispatch",
        {
            "action": "ack",
            "reminder_id": item["id"],
            "claim_token": item["claim_token"],
            "ack_status": "delivered",
            "request_id": "mx-ack",
        },
    )
    with hard_app.connection() as conn:
        row = conn.execute(
            "SELECT status, delivered_at FROM reminder WHERE id=?", (item["id"],)
        ).fetchone()
    ok = (
        bad["ok"] is False
        and row["status"] == "delivered"
        and row["delivered_at"] is not None
        and ack["reminder"]["status"] == "delivered"
    )
    record_requirement(
        evidence_dir,
        "R-REMINDER-FSM",
        status="proven" if ok else "failed",
        detail=f"bad={bad.get('error')} status={row['status']}",
    )
    assert ok


def test_R_query_whitelist_and_limits(hard_app: PtmApp, evidence_dir: Path) -> None:
    _ok(hard_app, "ptm_capture", {"content": "q", "request_id": "mx-q"})
    bad_field = hard_app.call(
        "ptm_query",
        {"entity": "matter", "filters": {"all": [{"field": "';password", "op": "=", "value": "x"}]}},
    )
    bad_entity = hard_app.call("ptm_query", {"entity": "operation_request"})  # type: ignore[arg-type]
    over = hard_app.call("ptm_query", {"entity": "matter", "limit": 201})
    ok = (
        bad_field["ok"] is False
        and bad_field["error"]["code"] == "INVALID_ARGUMENT"
        and bad_entity["ok"] is False
        and over["ok"] is False
    )
    record_requirement(
        evidence_dir,
        "R-QUERY-WHITELIST-LIMIT",
        status="proven" if ok else "failed",
        detail=f"field={bad_field.get('error')} entity={bad_entity.get('error')} over={over.get('error')}",
    )
    assert ok


def test_R_mutate_preview_required_confirmed_insufficient(
    hard_app: PtmApp, evidence_dir: Path
) -> None:
    m = _ok(hard_app, "ptm_capture", {"content": "prev", "request_id": "mx-prev"})
    ops = [
        {
            "action": "update",
            "entity": "matter",
            "id": m["id"],
            "expected_version": 1,
            "data": {"title": "new"},
        }
    ]
    denied = hard_app.call("ptm_mutate", {"operations": ops, "confirmed": True})
    prev = _ok(hard_app, "ptm_mutate", {"operations": ops, "dry_run": True})
    # replay same token after success should fail second submit without idempotency? first submit
    sub = _ok(
        hard_app,
        "ptm_mutate",
        {"operations": ops, "preview_token": prev["preview_token"], "request_id": "mx-prev-s"},
    )
    # reuse spent/invalidated token with new request_id
    reuse = hard_app.call(
        "ptm_mutate",
        {"operations": ops, "preview_token": prev["preview_token"], "request_id": "mx-prev-reuse"},
    )
    ok = (
        denied["error"]["code"] == "CONFIRMATION_REQUIRED"
        and sub["results"][0]["version"] == 2
        and reuse["ok"] is False
        and reuse["error"]["code"] in {"PREVIEW_EXPIRED", "VERSION_CONFLICT"}
    )
    record_requirement(
        evidence_dir,
        "R-MUTATE-PREVIEW-TOKEN",
        status="proven" if ok else "failed",
        detail=f"denied={denied.get('error')} reuse={reuse.get('error')}",
    )
    assert ok


def test_R_event_same_txn_and_immutable(hard_app: PtmApp, evidence_dir: Path) -> None:
    m = _ok(hard_app, "ptm_capture", {"content": "evt", "request_id": "mx-evt"})
    with hard_app.connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM event WHERE request_id='mx-evt' AND entity_id=?",
            (m["id"],),
        ).fetchone()[0]
    # cannot mutate event entity
    bad = hard_app.call(
        "ptm_mutate",
        {
            "operations": [
                {"action": "create", "entity": "event", "data": {"event_type": "x"}}  # type: ignore[list-item]
            ]
        },
    )
    ok = n >= 1 and bad["ok"] is False
    record_requirement(
        evidence_dir,
        "R-EVENT-APPEND-ONLY",
        status="proven" if ok else "failed",
        detail=f"events={n} mutate_event={bad.get('error')}",
    )
    assert ok


def test_R_eleven_tools_discoverable_mcp(tmp_path: Path, evidence_dir: Path, monkeypatch) -> None:
    import asyncio
    import os

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    data = tmp_path / "mcp-matrix"
    data.mkdir()
    monkeypatch.setenv("PTM_DATA_DIR", str(data))

    async def _run() -> list[str]:
        env = os.environ.copy()
        env["PTM_DATA_DIR"] = str(data)
        server = StdioServerParameters(
            command="uv",
            args=["run", "ptm-mcp"],
            cwd="/home/ubuntu/ptm",
            env=env,
        )
        async with stdio_client(server) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = await session.list_tools()
                return sorted(t.name for t in tools.tools)

    names = asyncio.run(_run())
    expected = sorted(
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
    ok = names == expected
    record_requirement(
        evidence_dir,
        "R-MCP-11-TOOLS",
        status="proven" if ok else "failed",
        detail=f"names={names}",
    )
    assert ok


def test_R_cli_backup_restore_roundtrip(tmp_path: Path, evidence_dir: Path) -> None:
    from typer.testing import CliRunner

    from ptm.app import PtmApp
    from ptm.cli import app as cli_app
    from ptm.config import Settings

    runner = CliRunner()
    data = tmp_path / "cli"
    backup = tmp_path / "b.sqlite3"
    runner.invoke(cli_app, ["init", "--data-dir", str(data)])
    app = PtmApp(Settings.from_env(data))
    mid = _ok(app, "ptm_capture", {"content": "cli-r", "request_id": "mx-cli"})["id"]
    runner.invoke(cli_app, ["backup", str(backup), "--data-dir", str(data)])
    _ok(app, "ptm_capture", {"content": "after", "request_id": "mx-cli2"})
    r = runner.invoke(cli_app, ["restore", str(backup), "--data-dir", str(data)])
    app2 = PtmApp(Settings.from_env(data))
    with app2.connection() as conn:
        titles = [x[0] for x in conn.execute("SELECT title FROM matter").fetchall()]
    ok = r.exit_code == 0 and "cli-r" in titles and "after" not in titles and mid
    record_requirement(
        evidence_dir,
        "R-CLI-BACKUP-RESTORE",
        status="proven" if ok else "failed",
        detail=f"exit={r.exit_code} titles={titles}",
    )
    assert ok


def test_R_out_of_scope_grok_notification(evidence_dir: Path) -> None:
    record_requirement(
        evidence_dir,
        "R-GROK-NOTIFY-E2E",
        status="out_of_scope",
        detail="Requires target Grok Bot platform; not verifiable on this VM alone (V0.3 §8.4/§15.4).",
    )


def test_R_out_of_scope_exactly_once(evidence_dir: Path) -> None:
    record_requirement(
        evidence_dir,
        "R-EXACTLY-ONCE",
        status="out_of_scope",
        detail="V0.3 explicitly does not promise exactly-once delivery.",
    )
