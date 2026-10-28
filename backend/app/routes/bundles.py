"""Bundle library, upload, and the inbox ingest job (ADR-005, ADR-006)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Form, UploadFile
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import Settings
from app.db.database import Database
from app.db.repositories.bundles import DOC_TYPES, KnowledgeRepository
from app.db.repositories.projects import ProjectRepository
from app.vector_store import VectorStore

router = APIRouter()
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_UPLOAD = File(...)
_DOC_TYPE = Form("OTHER")


class BundleBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str
    project_id: str | None = None


class BundlePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: str | None = None
    project_id: str | None = None
    clear_project: bool = False


def install_bundle_routes(app: FastAPI) -> None:
    app.include_router(router)


@router.post("/bundles")
async def create_bundle(body: BundleBody, request: Request) -> JSONResponse:
    project_id = body.project_id
    if project_id and ProjectRepository(_db(request)).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    try:
        record = KnowledgeRepository(_db(request)).insert_bundle(
            name=body.name,
            kind=body.kind,
            project_id=project_id,
        )
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(record.public_dict(), status_code=201)


@router.get("/bundles")
async def list_bundles(request: Request) -> JSONResponse:
    repository = KnowledgeRepository(_db(request))
    bundles = []
    for bundle in repository.list_bundles():
        payload = bundle.public_dict()
        payload["document_count"] = len(repository.list_documents(bundle.id))
        bundles.append(payload)
    return JSONResponse({"bundles": bundles})


@router.get("/bundles/{bundle_id}")
async def get_bundle(bundle_id: str, request: Request) -> JSONResponse:
    repository = KnowledgeRepository(_db(request))
    bundle = repository.get_bundle(bundle_id)
    if bundle is None:
        return JSONResponse({"detail": "bundle not found"}, status_code=404)
    payload = bundle.public_dict()
    documents = repository.list_documents(bundle_id)
    payload["documents"] = [document.public_dict() for document in documents]
    return JSONResponse(payload)


@router.patch("/bundles/{bundle_id}")
async def patch_bundle(bundle_id: str, body: BundlePatch, request: Request) -> JSONResponse:
    if body.project_id and ProjectRepository(_db(request)).get(body.project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    try:
        record = KnowledgeRepository(_db(request)).update_bundle(
            bundle_id,
            name=body.name,
            kind=body.kind,
            project_id=body.project_id,
            set_project_id=body.clear_project or body.project_id is not None,
        )
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    if record is None:
        return JSONResponse({"detail": "bundle not found"}, status_code=404)
    return JSONResponse(record.public_dict())


@router.delete("/bundles/{bundle_id}")
async def delete_bundle(bundle_id: str, request: Request) -> JSONResponse:
    repository = KnowledgeRepository(_db(request))
    bundle = repository.get_bundle(bundle_id)
    if bundle is None:
        return JSONResponse({"detail": "bundle not found"}, status_code=404)
    documents = repository.list_documents(bundle_id)
    repository.delete_bundle(bundle_id)
    vectors: VectorStore = request.app.state.vectors
    for document in documents:
        vectors.delete_by_document(document.id)
        if document.file_path:
            Path(document.file_path).unlink(missing_ok=True)
    return JSONResponse({"deleted": bundle_id})


@router.post("/bundles/{bundle_id}/documents")
async def upload_document(
    bundle_id: str,
    request: Request,
    file: UploadFile = _UPLOAD,
    doc_type: str = _DOC_TYPE,
) -> JSONResponse:
    repository = KnowledgeRepository(_db(request))
    if repository.get_bundle(bundle_id) is None:
        return JSONResponse({"detail": "bundle not found"}, status_code=404)
    if doc_type not in DOC_TYPES:
        return JSONResponse({"detail": f"unknown doc_type {doc_type}"}, status_code=422)
    data = await file.read()
    if not data:
        return JSONResponse({"detail": "file is empty"}, status_code=422)
    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse({"detail": "file is larger than 20 MB"}, status_code=413)
    settings: Settings = request.app.state.settings
    directory = settings.bundles_dir / bundle_id
    directory.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(file.filename or "upload")
    path = directory / f"{uuid.uuid4().hex}_{safe}"
    path.write_bytes(data)
    job_id = request.app.state.job_runner.enqueue(
        "ingest_file",
        {
            "path": str(path),
            "filename": file.filename or safe,
            "doc_type": doc_type,
            "bundle_id": bundle_id,
            "mime_type": file.content_type,
            "source": "upload",
        },
    )
    return JSONResponse({"job_id": job_id}, status_code=202)


@router.get("/bundles/{bundle_id}/documents/{document_id}")
async def get_document(bundle_id: str, document_id: str, request: Request) -> JSONResponse:
    repository = KnowledgeRepository(_db(request))
    document = repository.get_document(document_id)
    if document is None or document.bundle_id != bundle_id:
        return JSONResponse({"detail": "document not found"}, status_code=404)
    payload = document.public_dict(include_text=True)
    payload["chunks"] = [
        {
            "id": chunk.id,
            "section_header": chunk.section_header,
            "token_count": len(chunk.content.split()),
            "excerpt": chunk.content[:400],
        }
        for chunk in repository.document_chunks(document_id)
    ]
    return JSONResponse(payload)


@router.delete("/bundles/{bundle_id}/documents/{document_id}")
async def delete_document(bundle_id: str, document_id: str, request: Request) -> JSONResponse:
    repository = KnowledgeRepository(_db(request))
    document = repository.get_document(document_id)
    if document is None or document.bundle_id != bundle_id:
        return JSONResponse({"detail": "document not found"}, status_code=404)
    repository.delete_document(document_id)
    vectors: VectorStore = request.app.state.vectors
    vectors.delete_by_document(document_id)
    if document.file_path:
        Path(document.file_path).unlink(missing_ok=True)
    return JSONResponse({"deleted": document_id})


def _db(request: Request) -> Database:
    database: Database = request.app.state.db
    return database


def _safe_name(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return cleaned or "upload"
