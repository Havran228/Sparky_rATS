from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth import resolve_principal
from tests.conftest import make_settings


def test_health_is_public_and_bearer_opens_the_api(
    client: TestClient, auth_header: dict[str, str]
) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["db"]["ok"] is True
    missing = "00000000-0000-0000-0000-000000000000"
    denied = client.get(f"/jobs/{missing}")
    assert denied.status_code == 401
    allowed = client.get(f"/jobs/{missing}", headers=auth_header)
    assert allowed.status_code == 404
    wrong = client.get(f"/jobs/{missing}", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401


def test_tailscale_header_is_ignored_unless_the_peer_is_the_proxy(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    spoofed = {"Tailscale-User-Login": "ada@example.com"}
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/jobs/{missing}", headers=spoofed).status_code == 401

    trusted = make_settings(
        tmp_path,
        monkeypatch,
        data_dir=tmp_path / "trusted",
        tailscale_proxy_hosts="testclient",
    )
    from app.main import create_app

    with TestClient(create_app(trusted)) as trusted_client:
        response = trusted_client.get(f"/jobs/{missing}", headers=spoofed)
    assert response.status_code == 404


def test_resolve_principal_rejects_empty_login() -> None:
    request = _request("127.0.0.1", [(b"tailscale-user-login", b"  ")])
    assert resolve_principal(request, api_token="secret", proxy_hosts={"127.0.0.1"}) is None


def test_resolve_principal_accepts_proxy_and_bearer() -> None:
    proxied = _request("127.0.0.1", [(b"tailscale-user-login", b"ada@example.com")])
    principal = resolve_principal(proxied, api_token="secret", proxy_hosts={"127.0.0.1"})
    assert principal is not None and principal.method == "tailscale"
    assert principal.login == "ada@example.com"

    bearer = _request("10.0.0.8", [(b"authorization", b"Bearer secret")])
    by_token = resolve_principal(bearer, api_token="secret", proxy_hosts={"127.0.0.1"})
    assert by_token is not None and by_token.method == "bearer"

    forwarded = _request(
        "10.0.0.8",
        [
            (b"tailscale-user-login", b"ada@example.com"),
            (b"x-forwarded-for", b"127.0.0.1"),
        ],
    )
    assert resolve_principal(forwarded, api_token="secret", proxy_hosts={"127.0.0.1"}) is None


def _request(host: str, headers: list[tuple[bytes, bytes]]):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/jobs/x",
        "raw_path": b"/jobs/x",
        "query_string": b"",
        "headers": headers,
        "client": (host, 1234),
        "server": ("test", 80),
    }
    return Request(scope)
