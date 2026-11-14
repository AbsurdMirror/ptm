"""Error-code surface and light fuzz over IDs / timezones / payloads."""

from __future__ import annotations

import uuid

import pytest

from ptm.app import PtmApp
from ptm.timeutil import day_bounds_utc, parse_datetime, to_utc_iso


ERROR_CODES = {
    "INVALID_ARGUMENT",
    "NOT_FOUND",
    "CONFLICT",
    "VERSION_CONFLICT",
    "DUPLICATE_REQUEST",
    "PREVIEW_EXPIRED",
    "CONFIRMATION_REQUIRED",
    "LIMIT_EXCEEDED",
    "STORAGE_ERROR",
    "TEMPORARY_FAILURE",
    "INTERNAL_ERROR",
}


def test_error_codes_reachable(app: PtmApp) -> None:
    seen: set[str] = set()

    r = app.call("ptm_capture", {"content": ""})
    seen.add(r["error"]["code"])

    r = app.call("ptm_plan", {"action": "update", "plan_id": str(uuid.uuid4()), "expected_version": 1})
    seen.add(r["error"]["code"])  # NOT_FOUND or INVALID via preview? update without preview may be N/A — plan tool doesn't need preview

    mid = app.call("ptm_capture", {"content": "e", "request_id": "err-m"})["data"]["id"]
    app.call("ptm_capture", {"content": "a", "request_id": "dup-err"})
    r = app.call("ptm_capture", {"content": "b", "request_id": "dup-err"})
    seen.add(r["error"]["code"])

    ops = [
        {
            "action": "update",
            "entity": "matter",
            "id": mid,
            "expected_version": 1,
            "data": {"title": "x"},
        }
    ]
    r = app.call("ptm_mutate", {"operations": ops, "confirmed": True})
    seen.add(r["error"]["code"])

    preview = app.call("ptm_mutate", {"operations": ops, "dry_run": True})
    assert preview["ok"]
    tok = preview["data"]["preview_token"]
    with app.connection() as conn:
        from ptm.database import transaction

        with transaction(conn):
            conn.execute(
                "UPDATE preview_token SET expires_at='2000-01-01T00:00:00Z' WHERE token=?",
                (tok,),
            )
    r = app.call("ptm_mutate", {"operations": ops, "preview_token": tok})
    seen.add(r["error"]["code"])

    # limit exceeded via mutate >20 — pydantic may catch as INVALID_ARGUMENT
    big = [{"action": "create", "entity": "matter", "data": {"title": f"t{i}"}} for i in range(21)]
    r = app.call("ptm_mutate", {"operations": big})
    seen.add(r["error"]["code"])

    assert "INVALID_ARGUMENT" in seen
    assert "DUPLICATE_REQUEST" in seen
    assert "CONFIRMATION_REQUIRED" in seen
    assert "PREVIEW_EXPIRED" in seen
    assert seen <= ERROR_CODES | {"NOT_FOUND", "VERSION_CONFLICT", "LIMIT_EXCEEDED"}


@pytest.mark.parametrize(
    "tz",
    ["UTC", "Asia/Shanghai", "America/New_York", "Europe/Berlin"],
)
def test_timezone_day_bounds_roundtrip(tz: str) -> None:
    start, end = day_bounds_utc("2026-06-15", tz)
    assert end > start
    assert (end - start).total_seconds() == 86400
    assert to_utc_iso(start).endswith("Z")


@pytest.mark.parametrize(
    "raw",
    [
        "2026-11-16T20:00:00+08:00",
        "2026-11-16T12:00:00Z",
        "2026-11-16T12:00:00+00:00",
    ],
)
def test_parse_datetime_variants(raw: str) -> None:
    dt = parse_datetime(raw)
    assert dt is not None
    assert dt.tzinfo is not None
    assert to_utc_iso(dt).endswith("Z")


def test_fuzz_random_uuids_not_found(app: PtmApp) -> None:
    for _ in range(10):
        rid = str(uuid.uuid4())
        r = app.call("ptm_reminder", {"action": "get", "reminder_id": rid})
        assert r["ok"] is False
        assert r["error"]["code"] == "NOT_FOUND"


def test_query_rejects_nested_filter_bomb(app: PtmApp) -> None:
    node: dict = {"field": "id", "op": "is_not_null"}
    # nest beyond max depth
    for _ in range(6):
        node = {"all": [node]}
    r = app.call("ptm_query", {"entity": "matter", "filters": node})
    assert r["ok"] is False
    assert r["error"]["code"] in {"LIMIT_EXCEEDED", "INVALID_ARGUMENT"}
