"""Controlled read-only query engine."""

from __future__ import annotations

import base64
import json
import sqlite3
from typing import Any

from ptm.database import rows_to_dicts
from ptm.errors import InvalidArgument, LimitExceeded
from ptm.models import QUERYABLE_ENTITIES
from ptm.repositories.common import TABLE_COLUMNS, get_by_id
from ptm.timeutil import stable_hash


MAX_FILTER_DEPTH = 3
INCLUDE_MAP: dict[str, dict[str, tuple[str, str]]] = {
    "execution": {
        "matter": ("matter", "matter_id"),
        "plan": ("plan", "plan_id"),
        "stage": ("stage", "stage_id"),
    },
    "plan": {
        "matter": ("matter", "matter_id"),
        "stage": ("stage", "stage_id"),
    },
    "stage": {
        "matter": ("matter", "matter_id"),
    },
    "matter": {
        "category": ("category", "category_id"),
    },
    "observation": {
        "matter": ("matter", "matter_id"),
        "execution": ("execution", "execution_id"),
    },
    "reminder": {
        "matter": ("matter", "matter_id"),
        "plan": ("plan", "plan_id"),
        "stage": ("stage", "stage_id"),
    },
}


def run_query(
    conn: sqlite3.Connection,
    *,
    entity: str,
    filters: dict[str, Any] | None = None,
    include: list[str] | None = None,
    order_by: list[dict[str, str]] | None = None,
    limit: int = 50,
    cursor: str | None = None,
    fields: list[str] | None = None,
    include_total: bool = False,
) -> dict[str, Any]:
    if entity not in QUERYABLE_ENTITIES:
        raise InvalidArgument(f"entity not queryable: {entity}")
    if limit > 200:
        raise LimitExceeded("limit max is 200")

    offset = 0
    if cursor:
        offset = _decode_cursor(cursor, entity)

    where_sql, params = _compile_filters(entity, filters, depth=0)
    order_sql = _compile_order(entity, order_by)
    cols = TABLE_COLUMNS[entity] if entity != "event" else TABLE_COLUMNS["event"]
    select_cols = "*"
    if fields:
        bad = [f for f in fields if f not in cols]
        if bad:
            raise InvalidArgument("unknown fields", details={"fields": bad})
        # always keep id for includes
        keep = list(dict.fromkeys(["id", *fields]))
        select_cols = ", ".join(keep)

    sql = f"SELECT {select_cols} FROM {entity} WHERE {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?"
    rows = rows_to_dicts(conn.execute(sql, [*params, limit + 1, offset]).fetchall())
    truncated = len(rows) > limit
    items = rows[:limit]

    include = include or []
    if include:
        if len(include) > 5:
            raise LimitExceeded("too many includes")
        for item in items:
            item["_includes"] = {}
            for name in include:
                if name == "observations" and entity in {"matter", "execution"}:
                    if entity == "matter":
                        obs = rows_to_dicts(
                            conn.execute(
                                "SELECT * FROM observation WHERE matter_id = ? AND status = 'active' LIMIT 20",
                                (item["id"],),
                            ).fetchall()
                        )
                    else:
                        obs = rows_to_dicts(
                            conn.execute(
                                "SELECT * FROM observation WHERE execution_id = ? LIMIT 20",
                                (item["id"],),
                            ).fetchall()
                        )
                    item["_includes"]["observations"] = obs
                    continue
                mapping = INCLUDE_MAP.get(entity, {}).get(name)
                if not mapping:
                    raise InvalidArgument(f"include not allowed: {name}")
                table, fk = mapping
                fk_val = item.get(fk)
                item["_includes"][name] = get_by_id(conn, table, fk_val) if fk_val else None

    total_count = None
    if include_total:
        total_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM {entity} WHERE {where_sql}", params
        ).fetchone()["c"]

    next_cursor = None
    if truncated:
        next_cursor = _encode_cursor(entity, offset + limit)

    return {
        "items": items,
        "next_cursor": next_cursor,
        "total_count": total_count,
        "truncated": truncated,
    }


def _compile_filters(
    entity: str, filters: dict[str, Any] | None, depth: int
) -> tuple[str, list[Any]]:
    if depth > MAX_FILTER_DEPTH:
        raise LimitExceeded("filter nesting too deep")
    if not filters:
        return "1=1", []
    if "all" in filters and filters["all"] is not None:
        parts = []
        params: list[Any] = []
        for child in filters["all"]:
            sql, p = _compile_filters(entity, child if isinstance(child, dict) else child.model_dump(), depth + 1) if _is_group(child) else _compile_leaf(entity, child)
            parts.append(f"({sql})")
            params.extend(p)
        return (" AND ".join(parts) if parts else "1=1"), params
    if "any" in filters and filters["any"] is not None:
        parts = []
        params = []
        for child in filters["any"]:
            sql, p = _compile_filters(entity, child if isinstance(child, dict) else child.model_dump(), depth + 1) if _is_group(child) else _compile_leaf(entity, child)
            parts.append(f"({sql})")
            params.extend(p)
        return (" OR ".join(parts) if parts else "1=0"), params
    # flat leaf-like dict with field/op/value
    if "field" in filters:
        return _compile_leaf(entity, filters)
    raise InvalidArgument("invalid filters")


def _is_group(obj: Any) -> bool:
    if isinstance(obj, dict):
        return "all" in obj or "any" in obj
    return hasattr(obj, "all") or hasattr(obj, "any")


def _compile_leaf(entity: str, leaf: Any) -> tuple[str, list[Any]]:
    if hasattr(leaf, "model_dump"):
        leaf = leaf.model_dump()
    field = leaf["field"]
    op = leaf["op"]
    value = leaf.get("value")
    cols = TABLE_COLUMNS[entity]
    if field not in cols:
        raise InvalidArgument(f"field not allowed: {field}")
    if op == "=":
        return f"{field} = ?", [value]
    if op == "!=":
        return f"{field} != ?", [value]
    if op == ">":
        return f"{field} > ?", [value]
    if op == ">=":
        return f"{field} >= ?", [value]
    if op == "<":
        return f"{field} < ?", [value]
    if op == "<=":
        return f"{field} <= ?", [value]
    if op == "in":
        if not isinstance(value, list) or not value:
            raise InvalidArgument("in requires non-empty list")
        placeholders = ", ".join("?" for _ in value)
        return f"{field} IN ({placeholders})", list(value)
    if op == "not_in":
        if not isinstance(value, list) or not value:
            raise InvalidArgument("not_in requires non-empty list")
        placeholders = ", ".join("?" for _ in value)
        return f"{field} NOT IN ({placeholders})", list(value)
    if op == "is_null":
        return f"{field} IS NULL", []
    if op == "is_not_null":
        return f"{field} IS NOT NULL", []
    raise InvalidArgument(f"unsupported op: {op}")


def _compile_order(entity: str, order_by: list[dict[str, str]] | None) -> str:
    cols = TABLE_COLUMNS[entity]
    if not order_by:
        default = "created_at" if "created_at" in cols else "id"
        return f"{default} DESC"
    parts = []
    for item in order_by:
        field = item["field"] if isinstance(item, dict) else item.field
        direction = (item["direction"] if isinstance(item, dict) else item.direction).upper()
        if field not in cols:
            raise InvalidArgument(f"order field not allowed: {field}")
        if direction not in {"ASC", "DESC"}:
            raise InvalidArgument("invalid order direction")
        parts.append(f"{field} {direction}")
    return ", ".join(parts)


def _encode_cursor(entity: str, offset: int) -> str:
    payload = {"e": entity, "o": offset, "h": stable_hash({"e": entity, "o": offset})[:16]}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(cursor: str, entity: str) -> int:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        if payload.get("e") != entity:
            raise InvalidArgument("cursor entity mismatch")
        expected = stable_hash({"e": payload["e"], "o": payload["o"]})[:16]
        if payload.get("h") != expected:
            raise InvalidArgument("invalid cursor")
        return int(payload["o"])
    except InvalidArgument:
        raise
    except Exception as exc:
        raise InvalidArgument("invalid cursor") from exc
