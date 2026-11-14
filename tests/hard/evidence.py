"""Evidence recorder for hard verification matrix."""

from __future__ import annotations

import json
import time
from pathlib import Path


def record_requirement(
    evidence_dir: Path,
    req_id: str,
    *,
    status: str,
    detail: str,
    metrics: dict | None = None,
) -> None:
    assert status in {"proven", "failed", "out_of_scope"}
    path = evidence_dir / "requirement_matrix.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": req_id,
        "status": status,
        "detail": detail,
        "metrics": metrics or {},
        "ts": time.time(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
