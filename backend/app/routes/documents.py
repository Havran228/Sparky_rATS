"""Generated CV and cover-letter versions (ADR-009, ADR-015)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field, field_validator
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from app.db.database import Database
from app.db.repositories.documents import DocumentRepository, GeneratedDocumentRecord
from app.db.repositories.projects import ProjectRepository
from app.services.doc_generator import resolve_export
from app.services.json_diff import json_diff
from app.services.typst_compile import MAX_RAW_MARKUP_CHARS

router = APIRouter()


class GenerateBody(BaseModel):
    doc_type: Literal["CV", "COVER_LETTER"]
    language: Literal["en", "sk"]
    evaluation_id: str | None = None
    bundle_ids: list[str] = Field(default_factory=list)
    raw_markup: str | None = None

    @field_validator("evaluation_id")
    @classmethod
    def strip_evaluation(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("raw_markup")
    @classmethod
    def strip_markup(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("markup is empty")
        if len(value) > MAX_RAW_MARKUP_CHARS:
            raise ValueError("markup is too large")
        return value


def install_document_routes(app: FastAPI) -> None:
    app.include_router(router)


@router.post("/projects/{project_id}/documents")
async def generate_document(
    project_id: str,
    body: GenerateBody,
    request: Request,
) -> JSONResponse:
    if ProjectRepository(_db(request)).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    job_id = request.app.state.job_runner.enqueue(
        "generate_document",
        {
            "project_id": project_id,
            "doc_type": body.doc_type,
            "language": body.language,
            "evaluation_id": body.evaluation_id,
            "bundle_ids": body.bundle_ids,
            "raw_markup": body.raw_markup,
        },
    )
    return JSONResponse({"job_id": job_id}, status_code=202)


@router.get("/projects/{project_id}/documents")
async def list_documents(project_id: str, request: Request) -> JSONResponse:
    if ProjectRepository(_db(request)).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    records = DocumentRepository(_db(request)).list_for_project(project_id)
    return JSONResponse({"documents": [record.public_dict() for record in records]})


@router.get("/documents/{document_id}")
async def get_document(document_id: str, request: Request) -> JSONResponse:
    record = DocumentRepository(_db(request)).get(document_id)
    if record is None:
        return JSONResponse({"detail": "document not found"}, status_code=404)
    return JSONResponse(record.public_dict(include_source=True))


@router.get("/documents/{document_id}/pdf", response_model=None)
async def document_pdf(document_id: str, request: Request) -> FileResponse | JSONResponse:
    record = DocumentRepository(_db(request)).get(document_id)
    if record is None or record.pdf_path is None:
        return JSONResponse({"detail": "document not found"}, status_code=404)
    try:
        path = resolve_export(record.pdf_path, request.app.state.settings)
    except FileNotFoundError:
        return JSONResponse({"detail": "PDF is missing"}, status_code=404)
    filename = "cover-letter.pdf" if record.doc_type == "COVER_LETTER" else "cv.pdf"
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="inline",
    )


@router.get("/documents/{document_id}/diff/{other_id}")
async def diff_documents(
    document_id: str,
    other_id: str,
    request: Request,
) -> JSONResponse:
    repository = DocumentRepository(_db(request))
    left = repository.get(document_id)
    right = repository.get(other_id)
    if left is None or right is None:
        return JSONResponse({"detail": "document not found"}, status_code=404)
    if left.project_id != right.project_id:
        return JSONResponse({"detail": "documents belong to different projects"}, status_code=400)
    return JSONResponse(
        {
            "left": _identity(left),
            "right": _identity(right),
            "changes": json_diff(left.content, right.content),
        }
    )


@router.post("/documents/{document_id}/submit")
async def submit_document(document_id: str, request: Request) -> JSONResponse:
    if DocumentRepository(_db(request)).get(document_id) is None:
        return JSONResponse({"detail": "document not found"}, status_code=404)
    job_id = request.app.state.job_runner.enqueue(
        "submit_document",
        {"document_id": document_id},
    )
    return JSONResponse({"job_id": job_id}, status_code=202)


def _identity(record: GeneratedDocumentRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "version": record.version,
        "doc_type": record.doc_type,
        "language": record.language,
        "created_at": record.created_at,
    }


def _db(request: Request) -> Database:
    database: Database = request.app.state.db
    return database
