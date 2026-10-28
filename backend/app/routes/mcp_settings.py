"""External MCP servers. Tokens stay in the Fernet store; tools start disabled (ADR-010)."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db.repositories.mcp_servers import McpServerRepository
from app.mcp_client import (
    McpClientError,
    assert_http_url,
    external_tools_enabled,
    list_remote_tools,
)
from app.runtime import Runtime

router = APIRouter()


class ServerBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1)
    token: str | None = None


class WhitelistBody(BaseModel):
    whitelisted: bool


def install_mcp_settings_routes(app: FastAPI) -> None:
    app.include_router(router)


@router.get("/settings/mcp-servers")
async def list_servers(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    repository = McpServerRepository(runtime.db)
    servers = []
    whitelisted = 0
    for server in repository.list_servers():
        tools = repository.list_tools(server.id)
        whitelisted += sum(1 for tool in tools if tool.whitelisted)
        payload = server.public_dict()
        payload["has_token"] = _has_token(runtime, server.id)
        payload["tools"] = [tool.public_dict() for tool in tools]
        servers.append(payload)
    return JSONResponse(
        {
            "servers": servers,
            "external_tools_enabled": external_tools_enabled(whitelisted),
        }
    )


@router.post("/settings/mcp-servers")
async def create_server(body: ServerBody, request: Request) -> JSONResponse:
    try:
        url = assert_http_url(body.url.strip())
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    runtime = _runtime(request)
    saved = McpServerRepository(runtime.db).insert(name=body.name, url=url)
    if body.token and body.token.strip():
        runtime.secrets.put(f"mcp_remote:{saved.id}", {"token": body.token.strip()})
    payload = saved.public_dict()
    payload["has_token"] = _has_token(runtime, saved.id)
    payload["tools"] = []
    return JSONResponse(payload, status_code=201)


@router.delete("/settings/mcp-servers/{server_id}")
async def delete_server(server_id: str, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    deleted = McpServerRepository(runtime.db).delete(server_id)
    if not deleted:
        return JSONResponse({"detail": "server not found"}, status_code=404)
    runtime.secrets.delete(f"mcp_remote:{server_id}")
    return JSONResponse({"deleted": server_id})


@router.post("/settings/mcp-servers/{server_id}/discover")
async def discover(server_id: str, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    server = McpServerRepository(runtime.db).get(server_id)
    if server is None:
        return JSONResponse({"detail": "server not found"}, status_code=404)
    try:
        tools = await list_remote_tools(server.url, _token(runtime, server_id))
    except McpClientError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)
    saved = McpServerRepository(runtime.db).replace_tools(server_id, tools)
    return JSONResponse({"tools": [tool.public_dict() for tool in saved]})


@router.patch("/settings/mcp-servers/{server_id}/tools/{tool_name}")
async def whitelist_tool(
    server_id: str,
    tool_name: str,
    body: WhitelistBody,
    request: Request,
) -> JSONResponse:
    saved = McpServerRepository(_runtime(request).db).set_whitelisted(
        server_id,
        tool_name,
        body.whitelisted,
    )
    if saved is None:
        return JSONResponse({"detail": "tool not found"}, status_code=404)
    return JSONResponse(saved.public_dict())


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


def _token(runtime: Runtime, server_id: str) -> str | None:
    payload = runtime.secrets.get(f"mcp_remote:{server_id}")
    if payload is None:
        return None
    token = payload.get("token")
    return token if isinstance(token, str) and token else None


def _has_token(runtime: Runtime, server_id: str) -> bool:
    return _token(runtime, server_id) is not None
