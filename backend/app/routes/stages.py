"""Stage CRUD and the Kanban move. current_status is never an input (ADR-017)."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db.repositories.projects import ProjectRepository
from app.runtime import Runtime
from app.services.stages import (
    StageError,
    add_stage,
    column_catalog,
    delete_stage,
    ensure_stages,
    move_project,
    patch_stage,
    reorder_stages,
)

router = APIRouter()


class MoveBody(BaseModel):
    column: str = Field(min_length=1, max_length=32)


class StageBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    status: str = Field(min_length=1, max_length=32)
    notes: str | None = None


class StagePatch(BaseModel):
    name: str | None = None
    notes: str | None = None
    progress: str | None = None


class OrderBody(BaseModel):
    stage_ids: list[str] = Field(min_length=1)


def install_stage_routes(app: FastAPI) -> None:
    app.include_router(router)


@router.get("/board")
async def board(request: Request) -> JSONResponse:
    database = _runtime(request).db
    projects = []
    extras: list[str] = []
    for record in ProjectRepository(database).list_projects():
        ensure_stages(database, record.id)
        refreshed = ProjectRepository(database).get(record.id)
        if refreshed is None:
            continue
        if refreshed.current_status not in extras:
            extras.append(refreshed.current_status)
        projects.append(
            {
                "id": refreshed.id,
                "title": refreshed.title,
                "company": refreshed.company,
                "current_status": refreshed.current_status,
                "contract_type": refreshed.contract_type,
            }
        )
    columns = []
    for column in column_catalog(extras):
        status = column["status"]
        columns.append(
            {
                **column,
                "projects": [item for item in projects if item["current_status"] == status],
            }
        )
    return JSONResponse({"columns": columns})


@router.get("/projects/{project_id}/stages")
async def list_stages(project_id: str, request: Request) -> JSONResponse:
    if ProjectRepository(_runtime(request).db).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    stages = ensure_stages(_runtime(request).db, project_id)
    project = ProjectRepository(_runtime(request).db).get(project_id)
    return JSONResponse(
        {
            "current_status": None if project is None else project.current_status,
            "stages": [stage.public_dict() for stage in stages],
        }
    )


@router.post("/projects/{project_id}/move")
async def move(project_id: str, body: MoveBody, request: Request) -> JSONResponse:
    try:
        project, stages = move_project(_runtime(request).db, project_id, body.column)
    except StageError as exc:
        status = 404 if str(exc) == "project not found" else 422
        return JSONResponse({"detail": str(exc)}, status_code=status)
    return JSONResponse(
        {"project": project.public_dict(), "stages": [stage.public_dict() for stage in stages]}
    )


@router.post("/projects/{project_id}/stages")
async def create_stage(project_id: str, body: StageBody, request: Request) -> JSONResponse:
    try:
        stages = add_stage(
            _runtime(request).db,
            project_id,
            name=body.name,
            status=body.status,
            notes=body.notes,
        )
    except StageError as exc:
        status = 404 if str(exc) == "project not found" else 422
        return JSONResponse({"detail": str(exc)}, status_code=status)
    return JSONResponse({"stages": [stage.public_dict() for stage in stages]}, status_code=201)


@router.patch("/projects/{project_id}/stages/{stage_id}")
async def update_stage(
    project_id: str,
    stage_id: str,
    body: StagePatch,
    request: Request,
) -> JSONResponse:
    fields = body.model_fields_set
    try:
        stages = patch_stage(
            _runtime(request).db,
            project_id,
            stage_id,
            name=body.name,
            notes=body.notes,
            set_notes="notes" in fields,
            progress=body.progress,
        )
    except StageError as exc:
        status = 404 if "not found" in str(exc) else 422
        return JSONResponse({"detail": str(exc)}, status_code=status)
    return JSONResponse({"stages": [stage.public_dict() for stage in stages]})


@router.post("/projects/{project_id}/stages/order")
async def order_stages(project_id: str, body: OrderBody, request: Request) -> JSONResponse:
    try:
        stages = reorder_stages(_runtime(request).db, project_id, body.stage_ids)
    except StageError as exc:
        status = 404 if str(exc) == "project not found" else 422
        return JSONResponse({"detail": str(exc)}, status_code=status)
    return JSONResponse({"stages": [stage.public_dict() for stage in stages]})


@router.delete("/projects/{project_id}/stages/{stage_id}")
async def remove_stage(project_id: str, stage_id: str, request: Request) -> JSONResponse:
    try:
        stages = delete_stage(_runtime(request).db, project_id, stage_id)
    except StageError as exc:
        status = 404 if "not found" in str(exc) else 422
        return JSONResponse({"detail": str(exc)}, status_code=status)
    return JSONResponse({"stages": [stage.public_dict() for stage in stages]})


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime
