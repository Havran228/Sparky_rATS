"""Tailscale identity headers and bearer fallback (ADR-013)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings

PUBLIC_PATHS = frozenset({("/health", "GET")})


@dataclass(frozen=True)
class Principal:
    method: str
    login: str


def resolve_principal(
    request: Request,
    *,
    api_token: str | None,
    proxy_hosts: set[str],
) -> Principal | None:
    client_host = request.client.host if request.client is not None else ""
    login = request.headers.get("tailscale-user-login", "").strip()
    if login and client_host in proxy_hosts:
        return Principal(method="tailscale", login=login)
    presented = _bearer(request)
    if presented is not None and token_matches(presented, api_token):
        return Principal(method="bearer", login="bearer")
    return None


def token_matches(presented: str, expected: str | None) -> bool:
    if not expected or not presented:
        return False
    return secrets.compare_digest(presented, expected)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


class AuthMiddleware:
    """Pure ASGI middleware so SSE responses are not buffered."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope["method"])
        path = str(scope["path"])
        if method == "OPTIONS" or (path, method) in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        fastapi_app = scope.get("app")
        token = getattr(getattr(fastapi_app, "state", None), "api_token", None)
        principal = resolve_principal(
            request,
            api_token=token if isinstance(token, str) else None,
            proxy_hosts=self._settings.proxy_hosts(),
        )
        if principal is None:
            response = JSONResponse({"detail": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        request.state.principal = principal
        await self.app(scope, receive, send)
