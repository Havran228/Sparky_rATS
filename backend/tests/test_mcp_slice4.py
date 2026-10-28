from __future__ import annotations

import socket

import httpx
from fastapi.testclient import TestClient
from tests.conftest import make_settings

from app.main import create_app
from app.mcp_client import external_tools_enabled, parse_alias, tool_alias
from app.mcp_server import create_mcp_app
from app.tools.registry import TOOL_NAMES


def test_mcp_health_requires_the_mcp_bearer_and_lists_tools() -> None:
    app = create_mcp_app("mcp-token")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 401
        ok = client.get("/health", headers={"Authorization": "Bearer mcp-token"})
        assert ok.status_code == 200
        assert ok.json()["transport"] == "streamable-http"
        assert ok.json()["tools"] == list(TOOL_NAMES)
        rejected = client.post("/mcp", headers={"Authorization": "Bearer test-token"})
        assert rejected.status_code == 401
    assert external_tools_enabled(0) is False
    assert external_tools_enabled(1) is True


def test_alias_round_trip() -> None:
    server_id = "12345678-1234-1234-1234-123456789abc"
    alias = tool_alias(server_id, "send.mail")
    assert parse_alias(alias) == (server_id, "send_mail")
    assert parse_alias("list_projects") is None


def test_mcp_client_can_list_and_call_salary(tmp_path, monkeypatch) -> None:
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    port = _free_port()
    settings = make_settings(
        tmp_path,
        monkeypatch,
        enable_mcp_server=True,
        enable_watcher=False,
        mcp_port=port,
    )
    application = create_app(settings)
    with TestClient(application):
        url = f"http://127.0.0.1:{port}/mcp"

        async def smoke() -> tuple[list[str], str]:
            http = httpx.AsyncClient(
                headers={"Authorization": "Bearer mcp-token"},
                timeout=10,
            )
            try:
                async with streamable_http_client(url, http_client=http) as streams:
                    read, write, _session_id = streams
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        called = await session.call_tool(
                            "calc_net_salary",
                            {
                                "gross": "2000",
                                "tax_year": 2026,
                                "claim_nczd": False,
                                "contract_type": "SZCO",
                            },
                        )
            finally:
                await http.aclose()
            names = [tool.name for tool in listed.tools]
            text = ""
            structured = getattr(called, "structuredContent", None)
            if isinstance(structured, dict):
                text = str(structured)
            else:
                text = "".join(getattr(block, "text", "") for block in called.content)
            return names, text

        names, text = asyncio.run(smoke())
    assert "calc_net_salary" in names
    assert "fetch_google_timeline" in names
    assert names.count("calc_net_salary") == 1
    assert "not modeled" in text.lower() or "modeled" in text.lower()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
