"""MCP HTTP surface on its own port (ADR-010, ADR-013).

Slice 0 reserves the port and requires a bearer token. FastMCP and the tool
registry replace this app in slice 4. Tailscale identity is not accepted here;
automation uses the MCP bearer, which can differ from the UI token.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth import token_matches


def create_mcp_app(mcp_token: str) -> FastAPI:
    app = FastAPI(title="Sparky_rATS MCP", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def bearer_only(request: Request, call_next):  # type: ignore[no-untyped-def]
        presented = _bearer(request)
        if not token_matches(presented or "", mcp_token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "surface": "mcp", "tools": []}

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def not_ready(full_path: str) -> JSONResponse:
        return JSONResponse(
            {"detail": "MCP tools are not available until slice 4", "path": full_path},
            status_code=501,
        )

    return app


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()
