"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ptm.app import PtmApp
from ptm.config import Settings


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ptm-data"
    d.mkdir()
    return d


@pytest.fixture()
def settings(data_dir: Path) -> Settings:
    return Settings.from_env(data_dir)


@pytest.fixture()
def app(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> PtmApp:
    monkeypatch.setenv("PTM_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("PTM_TIMEZONE", "Asia/Shanghai")
    return PtmApp(settings)


@pytest.fixture()
def db_conn(app: PtmApp):
    with app.connection() as conn:
        yield conn
