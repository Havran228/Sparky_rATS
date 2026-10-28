"""Optional client for external MCP servers (ADR-010).

Discovered tools stay disabled until the owner whitelists each one. Calling a
tool that is not whitelisted is the chat loop's job to refuse; this module
only speaks the streamable HTTP protocol.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

_ALIAS = re.compile(r"^ext_([0-9a-f]{32})_(.+)$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_]")


class McpClientError(RuntimeError):
    """An external MCP server did not complete a call."""


def sanitize_tool_name(name: str) -> str:
    cleaned = _UNSAFE.sub("_", name).strip("_")
    return (cleaned or "tool")[:48]


def tool_alias(server_id: str, tool_name: str) -> str:
    return f"ext_{server_id.replace('-', '')}_{sanitize_tool_name(tool_name)}"


def parse_alias(name: str) -> tuple[str, str] | None:
    match = _ALIAS.match(name)
    if match is None:
        return None
    compact, safe_name = match.group(1), match.group(2)
    server_id = (
        f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:32]}"
    )
    return server_id, safe_name


def external_tools_enabled(whitelisted: int) -> bool:
    """True only after the owner has whitelisted at least one external tool."""
    return whitelisted > 0


async def list_remote_tools(
    url: str,
    token: str | None,
    *,
    timeout: float = 20,
) -> list[tuple[str, str, dict[str, object]]]:
    from mcp import ClientSession

    try:
        async with _connection(url, token, timeout) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
    except Exception as exc:
        raise McpClientError(f"{type(exc).__name__}: {exc}") from exc
    found: list[tuple[str, str, dict[str, object]]] = []
    for tool in listed.tools:
        schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {}
        found.append((tool.name, tool.description or "", _object_schema(schema)))
    return found


async def call_remote_tool(
    url: str,
    token: str | None,
    name: str,
    arguments: Mapping[str, object],
    *,
    timeout: float = 30,
) -> dict[str, object]:
    from mcp import ClientSession

    payload = {key: value for key, value in arguments.items()}
    try:
        async with _connection(url, token, timeout) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, payload)
    except Exception as exc:
        raise McpClientError(f"{type(exc).__name__}: {exc}") from exc
    if result.isError:
        raise McpClientError(_content_text(result.content) or "external tool failed")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return {str(key): value for key, value in structured.items()}
    return {"content": _content_text(result.content)}


@asynccontextmanager
async def _connection(
    url: str,
    token: str | None,
    timeout: float,
) -> AsyncIterator[tuple[Any, Any]]:
    from mcp.client.streamable_http import streamable_http_client

    http = httpx.AsyncClient(headers=_headers(token), timeout=timeout)
    try:
        async with streamable_http_client(url, http_client=http) as streams:
            read, write, _session = streams
            yield read, write
    finally:
        await http.aclose()


def _headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _object_schema(schema: object) -> dict[str, object]:
    if isinstance(schema, dict):
        return {str(key): value for key, value in schema.items()}
    return {"type": "object", "properties": {}}


def _content_text(blocks: object) -> str:
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def assert_http_url(url: str) -> str:
    parsed = httpx.URL(url)
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise ValueError("MCP server URL must be http or https")
    return str(parsed)
