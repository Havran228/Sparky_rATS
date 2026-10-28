"""Project ingestion and the gross-to-net calculator (ADR-006, ADR-007, ADR-014)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import Settings
from app.db.database import Database
from app.db.repositories.projects import ProjectRepository
from app.services.doc_generator import resolve_export
from app.services.salary_calc import UnknownTaxYear, calculate, list_tax_years, load_tax_params

router = APIRouter()


class IngestBody(BaseModel):
    input: str = Field(min_length=1)


class SalaryBody(BaseModel):
    gross: Decimal
    tax_year: int
    claim_nczd: bool = True
    contract_type: str = "TPP"


def install_project_routes(app: FastAPI) -> None:
    app.include_router(router)


@router.post("/projects/ingest")
async def ingest_project(body: IngestBody, request: Request) -> JSONResponse:
    job_id = request.app.state.job_runner.enqueue("ingest_job", {"input": body.input})
    return JSONResponse({"job_id": job_id}, status_code=202)


@router.get("/projects")
async def list_projects(request: Request) -> JSONResponse:
    records = ProjectRepository(_db(request)).list_projects()
    return JSONResponse({"projects": [record.public_dict() for record in records]})


@router.get("/projects/{project_id}")
async def get_project(project_id: str, request: Request) -> JSONResponse:
    record = ProjectRepository(_db(request)).get(project_id)
    if record is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    return JSONResponse(record.public_dict())


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, request: Request) -> JSONResponse:
    database = _db(request)
    paths = ProjectRepository(database).delete(project_id)
    if paths is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    _unlink_unreferenced_pdfs(paths, database, request.app.state.settings)
    return JSONResponse({"deleted": project_id})


@router.get("/salary/years")
async def salary_years(request: Request) -> JSONResponse:
    return JSONResponse({"years": list_tax_years(_db(request))})


@router.post("/salary/net")
async def salary_net(body: SalaryBody, request: Request) -> JSONResponse:
    try:
        params = load_tax_params(_db(request), body.tax_year)
    except UnknownTaxYear as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
    try:
        breakdown = calculate(
            body.gross,
            params,
            tax_year=body.tax_year,
            claim_nczd=body.claim_nczd,
            contract_type=body.contract_type,
        )
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(breakdown.model_dump(mode="json"))


def _db(request: Request) -> Database:
    database: Database = request.app.state.db
    return database


def _unlink_unreferenced_pdfs(paths: list[str], db: Database, settings: Settings) -> None:
    for raw in paths:
        still_used = db.query_one(
            "SELECT 1 AS n FROM generated_documents WHERE pdf_path = ? LIMIT 1",
            (raw,),
        )
        if still_used is not None:
            continue
        try:
            path = resolve_export(raw, settings)
        except FileNotFoundError:
            continue
        Path(path).unlink(missing_ok=True)
