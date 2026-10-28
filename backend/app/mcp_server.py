"""FastMCP streamable HTTP on its own port (ADR-010, ADR-013).

The MCP bearer is the only credential accepted here. Tailscale identity is not.
Tool definitions come from the same registry the chat loop uses.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool
from mcp.types import ToolAnnotations
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth import token_matches
from app.runtime import Runtime
from app.tools.handlers import ToolError
from app.tools.registry import REGISTRY, ToolSpec, invoke


class McpBearer:
    """Pure ASGI bearer check so streamable HTTP responses are not buffered."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        presented = _bearer(scope)
        if not token_matches(presented or "", self._token):
            response = JSONResponse({"detail": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_mcp_app(mcp_token: str, runtime: Runtime | None = None):  # type: ignore[no-untyped-def]
    mcp = FastMCP("Sparky_rATS")
    for spec in REGISTRY.values():
        mcp.add_tool(_fastmcp_tool(spec, runtime))
    app = mcp.http_app(
        middleware=[Middleware(McpBearer, token=mcp_token)],
        json_response=True,
    )

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "surface": "mcp",
                "transport": "streamable-http",
                "tools": [spec.name for spec in REGISTRY.values()],
            }
        )

    app.router.routes.insert(0, Route("/health", health, methods=["GET"]))
    return app


def _fastmcp_tool(spec: ToolSpec, runtime: Runtime | None) -> Tool:
    function = _function_for(spec, runtime)
    return Tool.from_function(
        function,
        name=spec.name,
        description=spec.description,
        output_schema=None,
        annotations=ToolAnnotations(
            readOnlyHint=not spec.side_effects,
            destructiveHint=spec.side_effects,
        ),
    )


def _function_for(
    spec: ToolSpec,
    runtime: Runtime | None,
) -> Callable[..., Awaitable[dict[str, object]]]:
    """Build a flat function so FastMCP publishes the Pydantic fields, not a wrapper object."""
    schema = spec.params.model_json_schema()
    properties = schema.get("properties")
    required = set(schema.get("required") or [])
    if not isinstance(properties, dict):
        properties = {}
    parameters: list[str] = []
    names: list[str] = []
    for name, raw in properties.items():
        if not isinstance(name, str) or not isinstance(raw, dict):
            continue
        annotation = _annotation(raw)
        default = raw.get("default", _MISSING)
        if name in required and "default" not in raw:
            parameters.append(f"{name}: {annotation}")
        else:
            parameters.append(f"{name}: {annotation} = {_literal(default)}")
        names.append(name)
    signature = ", ".join(parameters)
    pairs = ", ".join(f"{name!r}: {name}" for name in names)
    source = (
        f"async def {spec.name}({signature}) -> dict[str, object]:\n"
        f"    if _runtime is None:\n"
        f"        raise _ToolError('MCP runtime is not ready')\n"
        f"    return await _invoke({spec.name!r}, {{{pairs}}}, _runtime)\n"
    )
    namespace: dict[str, object] = {
        "_runtime": runtime,
        "_invoke": invoke,
        "_ToolError": ToolError,
    }
    exec(source, namespace)  # noqa: S102 - the signature is built from our own models
    function = namespace[spec.name]
    if not callable(function):
        raise RuntimeError(f"could not build MCP tool {spec.name}")
    function.__doc__ = spec.description
    return function  # type: ignore[return-value]


_MISSING = object()


def _annotation(schema: dict[str, object]) -> str:
    kind = schema.get("type")
    if isinstance(schema.get("anyOf"), list):
        options = schema["anyOf"]
        if isinstance(options, list):
            parts = [
                _annotation(option) for option in options if isinstance(option, dict)
            ]
            non_null = [part for part in parts if part != "None"]
            if "None" in parts and len(non_null) == 1:
                return f"{non_null[0]} | None"
            if parts:
                return " | ".join(parts)
    if kind == "string":
        return "str"
    if kind == "integer":
        return "int"
    if kind == "number":
        return "float"
    if kind == "boolean":
        return "bool"
    if kind == "array":
        items = schema.get("items")
        inner = _annotation(items) if isinstance(items, dict) else "object"
        return f"list[{inner}]"
    if kind == "object":
        return "dict[str, object]"
    return "object"


def _literal(value: object) -> str:
    if value is _MISSING or value is None:
        return "None"
    return repr(value)


def _bearer(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == b"authorization":
            header = value.decode("latin-1")
            scheme, _, token = header.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    return None
