"""Hard-suite fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ptm.app import PtmApp
from ptm.config import Settings

EVIDENCE_DIR = Path(os.environ.get("PTM_HARD_EVIDENCE", "/tmp/ptm-hard-evidence"))


@pytest.fixture(scope="session")
def evidence_dir() -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    matrix = EVIDENCE_DIR / "requirement_matrix.jsonl"
    if matrix.exists():
        matrix.unlink()
    return EVIDENCE_DIR


@pytest.fixture()
def hard_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PtmApp:
    data = tmp_path / "hard-data"
    data.mkdir()
    monkeypatch.setenv("PTM_DATA_DIR", str(data))
    monkeypatch.setenv("PTM_TIMEZONE", "Asia/Shanghai")
    return PtmApp(Settings.from_env(data))


@pytest.fixture()
def hard_settings(tmp_path: Path) -> Settings:
    data = tmp_path / "hard-settings"
    data.mkdir()
    return Settings.from_env(data)