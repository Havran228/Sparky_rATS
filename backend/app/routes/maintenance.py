"""Authenticated backup and restore (ADR-026)."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import Settings
from app.db.database import Database
from app.services.backup import (
    BackupError,
    BackupNotFound,
    create_backup,
    list_backups,
    restore_vectors,
)
from app.vector_store import VectorStore

router = APIRouter()


class BackupBody(BaseModel):
    retention_days: int = Field(default=14, ge=1, le=3650)


class RestoreBody(BaseModel):
    name: str = Field(pattern=r"^\d{8}T\d{6}Z$")
    replace: bool = False


def install_maintenance_routes(app: FastAPI) -> None:
    app.include_router(router)


@router.get("/maintenance/backups")
async def get_backups(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    return JSONResponse({"backups": list_backups(settings)})


@router.post("/maintenance/backup")
async def post_backup(body: BackupBody, request: Request) -> JSONResponse:
    db, vectors, settings = _parts(request)
    try:
        report = create_backup(db, vectors, settings, retention_days=body.retention_days)
    except BackupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=_status(exc))
    return JSONResponse(report.public_dict(), status_code=201)


@router.post("/maintenance/restore")
async def post_restore(body: RestoreBody, request: Request) -> JSONResponse:
    if not body.replace:
        return JSONResponse(
            {"detail": "set replace to true to rebuild the vector collection from the snapshot"},
            status_code=422,
        )
    _db, vectors, settings = _parts(request)
    try:
        points = restore_vectors(vectors, settings, body.name)
    except BackupNotFound as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
    except BackupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=_status(exc))
    return JSONResponse({"name": body.name, "points": points, "replaced": True})


def _parts(request: Request) -> tuple[Database, VectorStore, Settings]:
    return request.app.state.db, request.app.state.vectors, request.app.state.settings


def _status(exc: BackupError) -> int:
    text = str(exc)
    if "already exists" in text or "another backup" in text:
        return 409
    return 422
