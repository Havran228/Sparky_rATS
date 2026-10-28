"""Google OAuth, the read-only timeline, and the clipboard buffer (ADR-011)."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db.repositories.projects import ProjectRepository
from app.runtime import Runtime
from app.services.google_client import GoogleError, ReadOnlyViolation
from app.services.timeline import attach_message, contact_emails

router = APIRouter()


class ClientBody(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)


class ExchangeBody(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)


class TimelineBody(BaseModel):
    domains: list[str] = Field(default_factory=list)
    lookback_days: int | None = None


class AttachBody(BaseModel):
    message_id: str = Field(min_length=1)
    bundle_id: str | None = None


class DraftBody(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)


def install_google_routes(app: FastAPI) -> None:
    app.include_router(router)


@router.get("/google/status")
async def google_status(request: Request) -> JSONResponse:
    return JSONResponse(_runtime(request).google.status().model_dump(mode="json"))


@router.put("/google/client")
async def save_client(body: ClientBody, request: Request) -> JSONResponse:
    try:
        status = _runtime(request).google.save_client(body.client_id, body.client_secret)
    except GoogleError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(status.model_dump(mode="json"))


@router.get("/google/authorize")
async def authorize(request: Request) -> JSONResponse:
    try:
        url = _runtime(request).google.authorization_url()
    except GoogleError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    redirect = _runtime(request).settings.google_redirect_uri
    return JSONResponse({"url": url, "redirect_uri": redirect})


@router.post("/google/exchange")
async def exchange(body: ExchangeBody, request: Request) -> JSONResponse:
    try:
        status = await _runtime(request).google.exchange_code(body.code, body.state)
    except (GoogleError, ReadOnlyViolation) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(status.model_dump(mode="json"))


@router.post("/google/revoke")
async def revoke(request: Request) -> JSONResponse:
    try:
        result = await _runtime(request).google.revoke()
    except ReadOnlyViolation as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(result)


@router.post("/projects/{project_id}/timeline")
async def timeline(project_id: str, body: TimelineBody, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    project = ProjectRepository(runtime.db).get(project_id)
    if project is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    try:
        result = await runtime.google.timeline(
            project_id=project_id,
            domains=body.domains,
            emails=contact_emails(project.contacts),
            lookback_days=body.lookback_days,
        )
    except (GoogleError, ReadOnlyViolation) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(result.model_dump(mode="json"))


@router.post("/projects/{project_id}/timeline/attach")
async def attach(project_id: str, body: AttachBody, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    if ProjectRepository(runtime.db).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    try:
        result = await attach_message(
            project_id=project_id,
            message_id=body.message_id,
            bundle_id=body.bundle_id,
            db=runtime.db,
            google=runtime.google,
            ollama=runtime.ollama,
            vectors=runtime.vectors,
        )
    except (GoogleError, ReadOnlyViolation, ValueError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(result.model_dump(mode="json"))


@router.get("/projects/{project_id}/drafts")
async def list_drafts(project_id: str, request: Request) -> JSONResponse:
    if ProjectRepository(_runtime(request).db).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    drafts = _runtime(request).staging.list_for_project(project_id)
    return JSONResponse({"drafts": [draft.public_dict() for draft in drafts]})


@router.post("/projects/{project_id}/drafts")
async def create_draft(project_id: str, body: DraftBody, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    if ProjectRepository(runtime.db).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    try:
        draft = runtime.staging.put(project_id=project_id, subject=body.subject, body=body.body)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(draft.public_dict(), status_code=201)


@router.delete("/drafts/{draft_id}")
async def delete_draft(draft_id: str, request: Request) -> JSONResponse:
    deleted = _runtime(request).staging.delete(draft_id)
    if not deleted:
        return JSONResponse({"detail": "draft not found"}, status_code=404)
    return JSONResponse({"deleted": draft_id})


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime
