from __future__ import annotations

from fastapi.testclient import TestClient

from app.mcp_client import external_tools_enabled
from app.mcp_server import create_mcp_app


def test_mcp_placeholder_requires_its_own_bearer() -> None:
    app = create_mcp_app("mcp-token")
    client = TestClient(app)
    assert client.get("/health").status_code == 401
    ok = client.get("/health", headers={"Authorization": "Bearer mcp-token"})
    assert ok.status_code == 200
    assert ok.json()["tools"] == []
    missing = client.post("/tools/ingest_job", headers={"Authorization": "Bearer mcp-token"})
    assert missing.status_code == 501
    assert external_tools_enabled() is False
