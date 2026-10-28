"""Read-only Google client. The transport refuses anything that is not a GET
on a Google API host or a POST to the OAuth token and revoke endpoints (ADR-011).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel, Field

from app.clock import utc_now
from app.config import Settings
from app.secrets_store import SecretsStore

GOOGLE_SERVICE = "google"
GOOGLE_STATE_SERVICE = "google_oauth_state"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
SCOPES = (GMAIL_SCOPE, CALENDAR_SCOPE)
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_STATE_MAX_AGE = timedelta(minutes=15)
_EXPIRY_SKEW = timedelta(seconds=60)


class ReadOnlyViolation(RuntimeError):
    """The Google transport refused a call that is not read-only."""


class GoogleError(RuntimeError):
    """Google could not be reached, or the stored credential cannot be used."""


class GoogleNotConnected(GoogleError):
    """No refresh token is stored."""


class TimelineItem(BaseModel):
    kind: Literal["email", "event"]
    external_id: str
    occurred_at: str
    title: str
    snippet: str
    participants: list[str] = Field(default_factory=list)
    link: str | None = None


class TimelineResult(BaseModel):
    project_id: str
    domains: list[str]
    emails: list[str]
    lookback_days: int
    items: list[TimelineItem]
    warning: str | None = None


class GoogleStatus(BaseModel):
    configured: bool
    connected: bool
    client_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    refresh_obtained_at: str | None = None
    refresh_age_days: float | None = None
    access_expires_at: str | None = None
    redirect_uri: str
    weekly_reconsent: bool


def assert_read_only(method: str, url: str) -> None:
    """Allow POST /token and POST /revoke, and GET on *.googleapis.com. Nothing else."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/") or "/"
    verb = method.upper()
    if parsed.scheme != "https":
        raise ReadOnlyViolation(f"{verb} {url} is not an https Google call")
    if verb == "POST" and host == "oauth2.googleapis.com" and path in {"/token", "/revoke"}:
        return
    if verb == "GET" and _google_api_host(host):
        return
    raise ReadOnlyViolation(f"{verb} {host}{path} is not a read-only Google call")


def _google_api_host(host: str) -> bool:
    return host == "googleapis.com" or host.endswith(".googleapis.com")


class ReadOnlyTransport(httpx.AsyncBaseTransport):
    """Abort before the inner transport sees a forbidden request."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert_read_only(request.method, str(request.url))
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


class GoogleClient:
    def __init__(
        self,
        settings: Settings,
        secrets: SecretsStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._secrets = secrets
        inner = transport or httpx.AsyncHTTPTransport()
        self._http = httpx.AsyncClient(transport=ReadOnlyTransport(inner), timeout=30.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    def status(self) -> GoogleStatus:
        payload = self._secrets.get(GOOGLE_SERVICE) or {}
        client_id = _string(payload, "client_id")
        refresh = _string(payload, "refresh_token")
        obtained = _string(payload, "refresh_obtained_at")
        age = _age_days(obtained) if obtained else None
        scopes = _string_list(payload.get("scopes"))
        return GoogleStatus(
            configured=client_id is not None and _string(payload, "client_secret") is not None,
            connected=refresh is not None,
            client_id=client_id,
            scopes=scopes,
            refresh_obtained_at=obtained,
            refresh_age_days=None if age is None else round(age, 1),
            access_expires_at=_string(payload, "expires_at"),
            redirect_uri=self._settings.google_redirect_uri,
            weekly_reconsent=age is not None and age >= 7,
        )

    def save_client(self, client_id: str, client_secret: str) -> GoogleStatus:
        cleaned_id = client_id.strip()
        cleaned_secret = client_secret.strip()
        if not cleaned_id or not cleaned_secret:
            raise GoogleError("client id and client secret are required")
        current = dict(self._secrets.get(GOOGLE_SERVICE) or {})
        if current.get("client_id") != cleaned_id:
            for key in ("access_token", "refresh_token", "expires_at", "refresh_obtained_at"):
                current.pop(key, None)
        current["client_id"] = cleaned_id
        current["client_secret"] = cleaned_secret
        current["scopes"] = list(SCOPES)
        self._secrets.put(GOOGLE_SERVICE, current)
        return self.status()

    def authorization_url(self) -> str:
        payload = self._secrets.get(GOOGLE_SERVICE) or {}
        client_id = _string(payload, "client_id")
        if client_id is None or _string(payload, "client_secret") is None:
            raise GoogleError("save the OAuth client id and secret before connecting")
        state = secrets.token_urlsafe(24)
        self._secrets.put(GOOGLE_STATE_SERVICE, {"state": state, "created_at": utc_now()})
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": self._settings.google_redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "false",
                "state": state,
            }
        )
        return f"{_AUTH_URL}?{query}"

    async def exchange_code(self, code: str, state: str) -> GoogleStatus:
        self._consume_state(state)
        payload = self._secrets.get(GOOGLE_SERVICE) or {}
        client_id = _string(payload, "client_id")
        client_secret = _string(payload, "client_secret")
        if client_id is None or client_secret is None:
            raise GoogleError("save the OAuth client id and secret before connecting")
        body = await self._form(
            _TOKEN_URL,
            {
                "code": code.strip(),
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": self._settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        self._store_token_response(payload, body, refresh_is_new=True)
        return self.status()

    async def revoke(self) -> dict[str, object]:
        payload = self._secrets.get(GOOGLE_SERVICE) or {}
        token = _string(payload, "refresh_token") or _string(payload, "access_token")
        detail = "nothing to revoke"
        revoked = False
        if token is not None:
            try:
                response = await self._http.post(_REVOKE_URL, data={"token": token})
            except ReadOnlyViolation:
                raise
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
            else:
                revoked = response.status_code == 200
                if not revoked:
                    detail = f"Google revoke returned {response.status_code}"
                else:
                    detail = "revoked"
        self._secrets.delete(GOOGLE_SERVICE)
        self._secrets.delete(GOOGLE_STATE_SERVICE)
        return {"revoked": revoked, "wiped": True, "detail": detail}

    async def timeline(
        self,
        *,
        project_id: str,
        domains: list[str],
        emails: list[str],
        lookback_days: int | None,
    ) -> TimelineResult:
        window = lookback_days if lookback_days is not None else self._settings.google_lookback_days
        if window < 1 or window > 365:
            raise GoogleError("lookback_days must be between 1 and 365")
        cleaned_domains = _domains(domains)
        cleaned_emails = _emails(emails)
        if not cleaned_domains and not cleaned_emails:
            return TimelineResult(
                project_id=project_id,
                domains=[],
                emails=[],
                lookback_days=window,
                items=[],
                warning="Add a company domain or a contact address before reading the timeline.",
            )
        token = await self._access_token()
        items: list[TimelineItem] = []
        warning: str | None = None
        try:
            items.extend(await self._gmail(token, cleaned_domains, cleaned_emails, window))
        except GoogleError as exc:
            warning = str(exc)
        try:
            items.extend(
                await self._calendar(token, cleaned_domains, cleaned_emails, window)
            )
        except GoogleError as exc:
            extra = str(exc)
            warning = extra if warning is None else f"{warning} {extra}"
        items.sort(key=lambda item: item.occurred_at, reverse=True)
        return TimelineResult(
            project_id=project_id,
            domains=cleaned_domains,
            emails=cleaned_emails,
            lookback_days=window,
            items=items,
            warning=warning,
        )

    async def message_text(self, message_id: str) -> tuple[str, str, list[str]]:
        token = await self._access_token()
        payload = await self._get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            token,
            {"format": "full"},
        )
        headers = _headers(payload.get("payload"))
        subject = headers.get("subject") or "(no subject)"
        participants = [headers.get("from", ""), headers.get("to", "")]
        body = _plain_body(payload.get("payload"))
        snippet = _string_value(payload.get("snippet"))
        text = body or snippet or ""
        if not text.strip():
            raise GoogleError("that message has no text body")
        return subject, text, [part for part in participants if part]

    def _consume_state(self, presented: str) -> None:
        stored = self._secrets.get(GOOGLE_STATE_SERVICE)
        expected = _string(stored or {}, "state")
        created = _string(stored or {}, "created_at")
        self._secrets.delete(GOOGLE_STATE_SERVICE)
        if expected is None or not secrets.compare_digest(presented, expected):
            raise GoogleError("OAuth state did not match")
        if created is None or _age_days(created) is None:
            raise GoogleError("OAuth state expired")
        age = _age_days(created)
        if age is None or age * 86400 > _STATE_MAX_AGE.total_seconds():
            raise GoogleError("OAuth state expired")

    def _store_token_response(
        self,
        payload: dict[str, object],
        body: dict[str, object],
        *,
        refresh_is_new: bool,
    ) -> None:
        access = _string(body, "access_token")
        if access is None:
            raise GoogleError("Google did not return an access token")
        updated = dict(payload)
        updated["access_token"] = access
        refresh = _string(body, "refresh_token")
        if refresh is not None:
            updated["refresh_token"] = refresh
        if refresh_is_new or refresh is not None:
            updated["refresh_obtained_at"] = utc_now()
        expires_in = body.get("expires_in")
        seconds = int(expires_in) if isinstance(expires_in, int) else 3600
        expires = datetime.now(UTC) + timedelta(seconds=seconds)
        updated["expires_at"] = expires.strftime("%Y-%m-%dT%H:%M:%SZ")
        updated["scopes"] = list(SCOPES)
        self._secrets.put(GOOGLE_SERVICE, updated)

    async def _access_token(self) -> str:
        payload = self._secrets.get(GOOGLE_SERVICE) or {}
        access = _string(payload, "access_token")
        expires = _string(payload, "expires_at")
        if access is not None and not _expired(expires):
            return access
        refresh = _string(payload, "refresh_token")
        client_id = _string(payload, "client_id")
        client_secret = _string(payload, "client_secret")
        if refresh is None or client_id is None or client_secret is None:
            raise GoogleNotConnected("Google is not connected")
        body = await self._form(
            _TOKEN_URL,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
        self._store_token_response(payload, body, refresh_is_new=False)
        stored = self._secrets.get(GOOGLE_SERVICE) or {}
        renewed = _string(stored, "access_token")
        if renewed is None:
            raise GoogleError("refresh did not produce an access token")
        return renewed

    async def _form(self, url: str, data: dict[str, str]) -> dict[str, object]:
        try:
            response = await self._http.post(url, data=data)
        except ReadOnlyViolation:
            raise
        except Exception as exc:
            raise GoogleError(f"{type(exc).__name__}: {exc}") from exc
        body = _json_object(response)
        if response.status_code >= 400:
            message = (
                _string(body, "error_description")
                or _string(body, "error")
                or response.reason_phrase
            )
            raise GoogleError(f"Google token endpoint returned {response.status_code}: {message}")
        return body

    async def _get(
        self,
        url: str,
        token: str,
        params: dict[str, str] | list[tuple[str, str]],
    ) -> dict[str, object]:
        pairs = list(params.items()) if isinstance(params, dict) else params
        query = cast(
            "list[tuple[str, str | int | float | bool | None]]",
            [(key, value) for key, value in pairs],
        )
        try:
            response = await self._http.get(
                url,
                params=query,
                headers={"Authorization": f"Bearer {token}"},
            )
        except ReadOnlyViolation:
            raise
        except Exception as exc:
            raise GoogleError(f"{type(exc).__name__}: {exc}") from exc
        body = _json_object(response)
        if response.status_code >= 400:
            message = _error_message(body) or response.reason_phrase
            raise GoogleError(f"Google returned {response.status_code}: {message}")
        return body

    async def _gmail(
        self,
        token: str,
        domains: list[str],
        emails: list[str],
        lookback_days: int,
    ) -> list[TimelineItem]:
        query = _gmail_query(domains, emails, lookback_days)
        listed = await self._get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            token,
            {"q": query, "maxResults": "15"},
        )
        raw_messages = listed.get("messages")
        if not isinstance(raw_messages, list):
            return []
        items: list[TimelineItem] = []
        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            message_id = raw.get("id")
            if not isinstance(message_id, str):
                continue
            full = await self._get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                token,
                [
                    ("format", "metadata"),
                    ("metadataHeaders", "From"),
                    ("metadataHeaders", "To"),
                    ("metadataHeaders", "Cc"),
                    ("metadataHeaders", "Subject"),
                    ("metadataHeaders", "Date"),
                ],
            )
            headers = _headers(full.get("payload"))
            participants = [headers[key] for key in ("from", "to", "cc") if key in headers]
            items.append(
                TimelineItem(
                    kind="email",
                    external_id=message_id,
                    occurred_at=headers.get("date") or utc_now(),
                    title=headers.get("subject") or "(no subject)",
                    snippet=_string_value(full.get("snippet")),
                    participants=participants,
                    link=None,
                )
            )
        return items

    async def _calendar(
        self,
        token: str,
        domains: list[str],
        emails: list[str],
        lookback_days: int,
    ) -> list[TimelineItem]:
        now = datetime.now(UTC)
        start = now - timedelta(days=lookback_days)
        listed = await self._get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            token,
            {
                "timeMin": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timeMax": (now + timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "30",
            },
        )
        raw_items = listed.get("items")
        if not isinstance(raw_items, list):
            return []
        found: list[TimelineItem] = []
        for raw in raw_items:
            item = _event_item(raw, domains, emails)
            if item is not None:
                found.append(item)
        return found


def _event_item(
    raw: object,
    domains: list[str],
    emails: list[str],
) -> TimelineItem | None:
    if not isinstance(raw, dict):
        return None
    event_id = raw.get("id")
    if not isinstance(event_id, str):
        return None
    participants = _event_addresses(raw)
    summary = _string_value(raw.get("summary"))
    description = _string_value(raw.get("description"))
    haystack = " ".join([*participants, summary, description]).lower()
    if not _matches(haystack, domains, emails):
        return None
    start = raw.get("start")
    occurred = ""
    if isinstance(start, dict):
        occurred = _string_value(start.get("dateTime")) or _string_value(start.get("date"))
    link = raw.get("htmlLink")
    return TimelineItem(
        kind="event",
        external_id=event_id,
        occurred_at=occurred or utc_now(),
        title=summary or "(no title)",
        snippet=description[:400],
        participants=participants,
        link=link if isinstance(link, str) else None,
    )


def _event_addresses(raw: dict[object, object]) -> list[str]:
    found: list[str] = []
    organizer = raw.get("organizer")
    if isinstance(organizer, dict):
        email = organizer.get("email")
        if isinstance(email, str):
            found.append(email)
    attendees = raw.get("attendees")
    if isinstance(attendees, list):
        for attendee in attendees:
            if isinstance(attendee, dict):
                email = attendee.get("email")
                if isinstance(email, str):
                    found.append(email)
    return found


def _matches(haystack: str, domains: list[str], emails: list[str]) -> bool:
    for email in emails:
        if email.lower() in haystack:
            return True
    for domain in domains:
        if domain.lower() in haystack:
            return True
    return False


def _gmail_query(domains: list[str], emails: list[str], lookback_days: int) -> str:
    parts: list[str] = []
    for domain in domains:
        parts.append(f"from:{domain}")
        parts.append(f"to:{domain}")
    for email in emails:
        parts.append(f"from:{email}")
        parts.append(f"to:{email}")
    return f"newer_than:{lookback_days}d (" + " OR ".join(parts) + ")"


def _headers(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("headers")
    if not isinstance(raw, list):
        return {}
    found: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            found[name.lower()] = value
    return found


def _plain_body(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    mime = payload.get("mimeType")
    body = payload.get("body")
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(mime, str) and mime.startswith("text/plain") and isinstance(data, str):
        return _b64(data)
    parts = payload.get("parts")
    if isinstance(parts, list):
        texts = [_plain_body(part) for part in parts]
        joined = "\n".join(text for text in texts if text.strip())
        if joined:
            return joined
    if isinstance(data, str) and isinstance(mime, str) and mime.startswith("text/"):
        return _b64(data)
    return ""


def _b64(data: str) -> str:
    import base64

    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _domains(values: list[str]) -> list[str]:
    found: list[str] = []
    for value in values:
        cleaned = value.strip().lower().lstrip("@")
        if cleaned and " " not in cleaned and cleaned not in found:
            found.append(cleaned)
    return found


def _emails(values: list[str]) -> list[str]:
    found: list[str] = []
    for value in values:
        cleaned = value.strip().lower()
        if "@" in cleaned and " " not in cleaned and cleaned not in found:
            found.append(cleaned)
    return found


def _string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _json_object(response: httpx.Response) -> dict[str, object]:
    raw: object = response.json()
    if not isinstance(raw, dict):
        raise GoogleError("Google returned a non-object")
    return {str(key): item for key, item in raw.items()}


def _error_message(body: dict[str, object]) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    if isinstance(error, str):
        return error
    return ""


def _expired(expires_at: str | None) -> bool:
    if expires_at is None:
        return True
    parsed = _parse_time(expires_at)
    if parsed is None:
        return True
    return parsed <= datetime.now(UTC) + _EXPIRY_SKEW


def _age_days(stamp: str) -> float | None:
    parsed = _parse_time(stamp)
    if parsed is None:
        return None
    return (datetime.now(UTC) - parsed).total_seconds() / 86400


def _parse_time(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
