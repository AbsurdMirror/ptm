"""ptm_query / ptm_stats / ptm_schema broad coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ptm.app import PtmApp
from ptm.timeutil import to_utc_iso


def _seed(app: PtmApp) -> dict[str, str]:
    with app.connection() as conn:
        cat = conn.execute("SELECT id FROM category WHERE name='学习'").fetchone()["id"]
    m = app.call(
        "ptm_capture",
        {"content": "查询种子", "category_id": cat, "request_id": "qs-m"},
    )["data"]["id"]
    for i in range(5):
        app.call(
            "ptm_record_execution",
            {
                "matter_id": m,
                "duration_minutes": 30 + i * 10,
                "duration_precision": "approximate",
                "result": "partial",
                "note": f"n{i}",
                "request_id": f"qs-e{i}",
            },
        )
    return {"matter_id": m, "category_id": cat}


def test_query_filters_pagination_includes_and_limits(app: PtmApp) -> None:
    ids = _seed(app)
    q = app.call(
        "ptm_query",
        {
            "entity": "execution",
            "filters": {
                "all": [
                    {"field": "matter_id", "op": "=", "value": ids["matter_id"]},
                    {"field": "duration_minutes", "op": ">=", "value": 40},
                ]
            },
            "order_by": [{"field": "duration_minutes", "direction": "asc"}],
            "limit": 2,
            "include_total": True,
            "include": ["matter"],
            "fields": ["id", "matter_id", "duration_minutes", "result"],
        },
    )
    assert q["ok"]
    assert q["data"]["truncated"] is True
    assert q["data"]["total_count"] >= 3
    assert len(q["data"]["items"]) == 2
    assert q["data"]["next_cursor"]
    assert all(i["duration_minutes"] >= 40 for i in q["data"]["items"])
    assert q["data"]["items"][0]["_includes"]["matter"]["id"] == ids["matter_id"]

    page2 = app.call(
        "ptm_query",
        {
            "entity": "execution",
            "filters": {
                "all": [
                    {"field": "matter_id", "op": "=", "value": ids["matter_id"]},
                    {"field": "duration_minutes", "op": ">=", "value": 40},
                ]
            },
            "order_by": [{"field": "duration_minutes", "direction": "asc"}],
            "limit": 2,
            "cursor": q["data"]["next_cursor"],
        },
    )
    assert page2["ok"]
    ids1 = {i["id"] for i in q["data"]["items"]}
    ids2 = {i["id"] for i in page2["data"]["items"]}
    assert ids1.isdisjoint(ids2)

    # OR / IN
    orq = app.call(
        "ptm_query",
        {
            "entity": "execution",
            "filters": {
                "any": [
                    {"field": "duration_minutes", "op": "=", "value": 30},
                    {"field": "duration_minutes", "op": "in", "value": [70, 999]},
                ]
            },
            "limit": 50,
        },
    )
    assert orq["ok"]
    assert len(orq["data"]["items"]) >= 2

    # invalid field / cursor / write-like entity blocked
    bad_field = app.call(
        "ptm_query",
        {"entity": "execution", "filters": {"all": [{"field": "drop_table", "op": "=", "value": 1}]}},
    )
    assert bad_field["ok"] is False
    assert bad_field["error"]["code"] == "INVALID_ARGUMENT"

    bad_cursor = app.call(
        "ptm_query", {"entity": "execution", "cursor": "not-a-cursor", "limit": 1}
    )
    assert bad_cursor["ok"] is False

    # event is queryable read-only
    ev = app.call("ptm_query", {"entity": "event", "limit": 5, "include_total": True})
    assert ev["ok"]
    assert ev["data"]["total_count"] >= 1


def test_stats_all_metrics(app: PtmApp) -> None:
    ids = _seed(app)
    start = to_utc_iso(datetime.now(timezone.utc) - timedelta(days=1))
    end = to_utc_iso(datetime.now(timezone.utc) + timedelta(days=1))
    metrics = [
        "time_by_category",
        "time_by_matter",
        "time_by_stage",
        "plan_vs_execution",
        "completion_summary",
        "estimate_accuracy",
    ]
    for metric in metrics:
        r = app.call(
            "ptm_stats",
            {
                "metric": metric,
                "start_at": start,
                "end_at": end,
                "category_id": ids["category_id"] if metric == "time_by_category" else None,
                "matter_id": ids["matter_id"] if metric == "time_by_matter" else None,
            },
        )
        assert r["ok"], (metric, r)
        assert r["data"]["metric"] == metric
        assert "methodology" in r["data"] or "values" in r["data"]

    # estimate_accuracy must not invent zero for missing records
    ea = app.call("ptm_stats", {"metric": "estimate_accuracy", "start_at": start, "end_at": end})
    assert "excluded_reason" in ea["data"]


def test_schema_entities_and_enums(app: PtmApp) -> None:
    all_s = app.call("ptm_schema", {})
    assert all_s["ok"]
    assert len(all_s["data"]["tools"]) == 11
    for entity in ["category", "matter", "stage", "plan", "execution", "observation", "reminder", "event"]:
        one = app.call("ptm_schema", {"entity": entity})
        assert one["ok"]
        assert one["data"]["entity"] == entity
        assert isinstance(one["data"]["fields"], list)
        assert "operations" in one["data"]
    plan = app.call("ptm_schema", {"entity": "plan"})
    assert set(plan["data"]["enums"]["plan_type"]) == {"time_block", "time_budget"}
