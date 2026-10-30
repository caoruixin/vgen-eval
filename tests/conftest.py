from __future__ import annotations

import os
from pathlib import Path

import pytest

from vgen.catalog.registry import Registry
from vgen.config import load_settings


@pytest.fixture(autouse=True)
def isolated_probe_overlay(tmp_path_factory, monkeypatch):
    """Tests must not depend on the live probe results in vgen/catalog/probe_status.json."""
    monkeypatch.setenv("VGEN_PROBE_STATUS", str(tmp_path_factory.mktemp("probe") / "none.json"))


@pytest.fixture()
def mock_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VGEN_MOCK", "1")
    monkeypatch.setenv("VGEN_DATA_DIR", str(tmp_path / "data"))
    env = tmp_path / ".env"
    env.write_text("TOKENBAY_REQUESTS_PER_MINUTE=15\n")
    return load_settings(env)


@pytest.fixture()
def registry() -> Registry:
    return Registry.load()


@pytest.fixture()
def db_ready(mock_settings):
    from vgen import db

    db.init_db(mock_settings.db_path)
    return mock_settings
