"""Runtime configuration for PTM."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TIMEZONE = "Asia/Shanghai"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    timezone: str
    db_path: Path

    @classmethod
    def from_env(cls, data_dir: str | Path | None = None) -> Settings:
        raw_dir = data_dir or os.environ.get("PTM_DATA_DIR")
        if raw_dir:
            root = Path(raw_dir).expanduser().resolve()
        else:
            root = Path.home() / ".local" / "share" / "ptm"
        tz = os.environ.get("PTM_TIMEZONE", DEFAULT_TIMEZONE)
        root.mkdir(parents=True, exist_ok=True)
        return cls(data_dir=root, timezone=tz, db_path=root / "ptm.sqlite3")


def get_settings(data_dir: str | Path | None = None) -> Settings:
    return Settings.from_env(data_dir)
