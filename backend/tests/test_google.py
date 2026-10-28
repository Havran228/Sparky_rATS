from __future__ import annotations

import base64

import httpx
import pytest

from app.db.repositories.bundles import KnowledgeRepository
from app.db.repositories.projects import ProjectRepository
from app.services.google_client import (
    GMAIL_SCOPE,
    GoogleClient,
    ReadOnlyTransport,
    ReadOnlyViolation,
    assert_read_only,
)


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", "https://oauth2.googleapis.com/token"),
        ("POST", "https://oauth2.googleapis.com/revoke"),
        ("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages"),
        ("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events"),
        ("GET", "https://oauth2.googleapis.com/token"),
    ],
)
def test_read_only_allows_oauth_post_and_api_get(method: str, url: str) -> None:
    assert_read_only(method, url)


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", "https://gmail.googleapis.com/batch"),
        ("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"),
        ("POST", "https://oauth2.googleapis.com/token/extra"),
        ("POST", "https://www.googleapis.com/upload/drive/v3/files"),
        ("PUT", "https://www.googleapis.com/calendar/v3/calendars/primary/events/e1"),
        ("PATCH", "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1"),
        ("DELETE", "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1"),
        ("GET", "https://example.com/inbox"),
        ("POST", "http://oauth2.googleapis.com/token"),
        ("GET", "https://gmail.googleapis.com.evil.example/messages"),
        ("POST", "https://oauth2.googleapis.com.evil.example/token"),
    ],
)
def test_read_only_rejects_writes_and_foreign_hosts(method: str, url: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        assert_read_only(method, url)


@pytest.mark.asyncio
async def test_transport_does_not_forward_a_forbidden_call() -> None:
    seen = False

    def inner(_request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen = True
        return httpx.Response(200)

    transport = ReadOnlyTransport(httpx.MockTransport(inner))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(ReadOnlyViolation):
            await http.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send")
        allowed = await http.get("https://gmail.googleapis.com/gmail/v1/users/me/profile")
    assert allowed.status_code == 200
    assert seen is True


def test_authorize_exchange_revoke_and_token_age(client, auth_header) -> None:
    runtime = client.app.state.runtime
    runtime.google = _google(runtime, _token_transport())
    saved = client.put(
        "/google/client",
        headers=auth_header,
        json={"client_id": "client-id", "client_secret": "client-secret"},
    )
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["connected"] is False
    assert GMAIL_SCOPE in saved.json()["scopes"]
    assert "client-secret" not in saved.text

    started = client.get("/google/authorize", headers=auth_header)
    assert started.status_code == 200
    url = httpx.URL(started.json()["url"])
    assert url.host == "accounts.google.com"
    assert "gmail.readonly" in url.params["scope"]
    assert "drive" not in url.params["scope"]

    exchanged = client.post(
        "/google/exchange",
        headers=auth_header,
        json={"code": "auth-code", "state": url.params["state"]},
    )
    assert exchanged.status_code == 200
    body = exchanged.json()
    assert body["connected"] is True
    assert body["refresh_age_days"] == 0.0
    assert "refresh_token" not in exchanged.text

    reused = client.post(
        "/google/exchange",
        headers=auth_header,
        json={"code": "auth-code", "state": url.params["state"]},
    )
    assert reused.status_code == 422

    revoked = client.post("/google/revoke", headers=auth_header)
    assert revoked.status_code == 200
    assert revoked.json()["wiped"] is True
    assert client.get("/google/status", headers=auth_header).json()["configured"] is False


def test_timeline_reads_mail_and_attach_stores_the_thread(client, auth_header) -> None:
    runtime = client.app.state.runtime
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.copy_with(query=None)}")
        assert request.method == "GET"
        path = request.url.path
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1", "threadId": "t1"}]})
        if path.endswith("/messages/m1"):
            return httpx.Response(200, json=_message())
        if path.endswith("/events"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "e1",
                            "summary": "Screening with Acme",
                            "description": "intro",
                            "htmlLink": "https://calendar.google.com/event?eid=e1",
                            "start": {"dateTime": "2026-03-01T09:00:00Z"},
                            "attendees": [{"email": "ada@acme.com"}],
                        },
                        {
                            "id": "e2",
                            "summary": "Dentist",
                            "start": {"dateTime": "2026-03-02T09:00:00Z"},
                            "attendees": [{"email": "clinic@example.org"}],
                        },
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"message": path}})

    runtime.secrets.put(
        "google",
        {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": "2099-01-01T00:00:00Z",
            "refresh_obtained_at": "2026-10-01T00:00:00Z",
            "scopes": [GMAIL_SCOPE],
        },
    )
    runtime.google = _google(runtime, handler)
    project = ProjectRepository(runtime.db).insert(
        source_kind="text",
        source_ref=None,
        company="Acme",
        title="Platform",
        contract_type="TPP",
        salary=None,
        language="en",
        requirements=None,
        offerings=None,
        contacts=["Ada <ada@acme.com>"],
        dates=None,
        raw_content_markdown=None,
        model_name=None,
        prompt_version=None,
    )
    timeline = client.post(
        f"/projects/{project.id}/timeline",
        headers=auth_header,
        json={"domains": ["acme.com"], "lookback_days": 30},
    )
    assert timeline.status_code == 200
    body = timeline.json()
    assert body["emails"] == ["ada@acme.com"]
    kinds = {(item["kind"], item["external_id"]) for item in body["items"]}
    assert ("email", "m1") in kinds
    assert ("event", "e1") in kinds
    assert ("event", "e2") not in kinds
    assert all(not call.startswith("POST") for call in calls)

    attached = client.post(
        f"/projects/{project.id}/timeline/attach",
        headers=auth_header,
        json={"message_id": "m1"},
    )
    assert attached.status_code == 200
    assert attached.json()["duplicate"] is False
    document = KnowledgeRepository(runtime.db).get_document(attached.json()["document_id"])
    assert document is not None
    assert document.doc_type == "EMAIL_THREAD"
    assert document.extracted_text is not None
    assert "See you Tuesday" in document.extracted_text

    again = client.post(
        f"/projects/{project.id}/timeline/attach",
        headers=auth_header,
        json={"message_id": "m1"},
    )
    assert again.json()["duplicate"] is True
    assert again.json()["document_id"] == document.id


def test_clipboard_draft_is_copied_locally_and_not_stored(client, auth_header) -> None:
    project = ProjectRepository(client.app.state.db).insert(
        source_kind="text",
        source_ref=None,
        company=None,
        title="Role",
        contract_type=None,
        salary=None,
        language=None,
        requirements=None,
        offerings=None,
        contacts=None,
        dates=None,
        raw_content_markdown=None,
        model_name=None,
        prompt_version=None,
    )
    created = client.post(
        f"/projects/{project.id}/drafts",
        headers=auth_header,
        json={"subject": "Re: Platform", "body": "Thank you for the note."},
    )
    assert created.status_code == 201
    listed = client.get(f"/projects/{project.id}/drafts", headers=auth_header)
    assert listed.json()["drafts"][0]["body"] == "Thank you for the note."
    stored = client.app.state.db.query("SELECT service_name FROM system_credentials")
    assert all("draft" not in str(row["service_name"]) for row in stored)
    deleted = client.delete(f"/drafts/{created.json()['id']}", headers=auth_header)
    assert deleted.status_code == 200
    assert client.get(f"/projects/{project.id}/drafts", headers=auth_header).json()["drafts"] == []


def test_timeline_without_google_does_not_call_out(client, auth_header) -> None:
    project = ProjectRepository(client.app.state.db).insert(
        source_kind="text",
        source_ref=None,
        company=None,
        title="Role",
        contract_type=None,
        salary=None,
        language=None,
        requirements=None,
        offerings=None,
        contacts=None,
        dates=None,
        raw_content_markdown=None,
        model_name=None,
        prompt_version=None,
    )
    response = client.post(
        f"/projects/{project.id}/timeline",
        headers=auth_header,
        json={"domains": ["acme.com"]},
    )
    assert response.status_code == 422


def _google(runtime, handler) -> GoogleClient:
    return GoogleClient(runtime.settings, runtime.secrets, transport=httpx.MockTransport(handler))


def _token_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/revoke":
            return httpx.Response(200)
        assert request.url.path == "/token"
        form = request.read().decode()
        assert "client_secret" in form
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    return handler


def _message() -> dict[str, object]:
    encoded = base64.urlsafe_b64encode(b"See you Tuesday.").decode().rstrip("=")
    return {
        "id": "m1",
        "snippet": "See you Tuesday",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Screening"},
                {"name": "From", "value": "Ada <ada@acme.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Date", "value": "Tue, 3 Mar 2026 10:00:00 +0000"},
            ],
            "body": {"data": encoded},
        },
    }

