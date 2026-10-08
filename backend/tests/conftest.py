from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: object) -> Settings:
    for key in list(os.environ):
        if key.startswith("SPARKY_"):
            monkeypatch.delenv(key, raising=False)
    values: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "api_token": "test-token",
        "mcp_token": "mcp-token",
        "master_key": Fernet.generate_key().decode(),
        "enable_mcp_server": False,
        "enable_watcher": True,
        "ollama_base_url": "http://127.0.0.1:9",
        "tailscale_proxy_hosts": "127.0.0.1,::1",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    return make_settings(tmp_path, monkeypatch)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}
