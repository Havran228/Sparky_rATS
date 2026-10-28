"""Evaluation runs and the candidate profile (ADR-008, ADR-016)."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db.database import Database
from app.db.repositories.bundles import KnowledgeRepository
from app.db.repositories.evaluations import EvaluationRecord, EvaluationRepository
from app.db.repositories.profile import ProfileRepository
from app.db.repositories.projects import ProjectRepository
from app.llm.schemas.profile import CandidateProfile

router = APIRouter()


class EvaluateBody(BaseModel):
    bundle_ids: list[str] = Field(min_length=1)
    weights: dict[str, float] | None = None


class ProposeBody(BaseModel):
    bundle_ids: list[str] = Field(min_length=1)


def install_matching_routes(app: FastAPI) -> None:
    app.include_router(router)


@router.post("/projects/{project_id}/evaluate")
async def evaluate_project(project_id: str, body: EvaluateBody, request: Request) -> JSONResponse:
    if ProjectRepository(_db(request)).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    job_id = request.app.state.job_runner.enqueue(
        "evaluate_match",
        {
            "project_id": project_id,
            "bundle_ids": body.bundle_ids,
            "weights": body.weights,
        },
    )
    return JSONResponse({"job_id": job_id}, status_code=202)


@router.get("/projects/{project_id}/evaluations")
async def list_evaluations(project_id: str, request: Request) -> JSONResponse:
    if ProjectRepository(_db(request)).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    repository = EvaluationRepository(_db(request))
    knowledge = KnowledgeRepository(_db(request))
    records = [_hydrate(record, knowledge) for record in repository.list_for_project(project_id)]
    return JSONResponse({"evaluations": records})


@router.get("/evaluations/{evaluation_id}")
async def get_evaluation(evaluation_id: str, request: Request) -> JSONResponse:
    record = EvaluationRepository(_db(request)).get(evaluation_id)
    if record is None:
        return JSONResponse({"detail": "evaluation not found"}, status_code=404)
    return JSONResponse(_hydrate(record, KnowledgeRepository(_db(request))))


@router.get("/profile")
async def get_profile(request: Request) -> JSONResponse:
    return JSONResponse(ProfileRepository(_db(request)).get().public_dict())


@router.put("/profile")
async def put_profile(body: CandidateProfile, request: Request) -> JSONResponse:
    saved = ProfileRepository(_db(request)).save(body)
    return JSONResponse(saved.public_dict())


@router.post("/profile/propose")
async def propose_profile(body: ProposeBody, request: Request) -> JSONResponse:
    job_id = request.app.state.job_runner.enqueue(
        "propose_skills",
        {"bundle_ids": body.bundle_ids},
    )
    return JSONResponse({"job_id": job_id}, status_code=202)


def _hydrate(record: EvaluationRecord, knowledge: KnowledgeRepository) -> dict[str, object]:
    payload = record.public_dict()
    classification = dict(record.classification)
    items = classification.get("items")
    if not isinstance(items, list):
        return payload
    chunk_ids: list[str] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("chunk_ids"), list):
            raw_ids = item["chunk_ids"]
            chunk_ids.extend(chunk_id for chunk_id in raw_ids if isinstance(chunk_id, str))
    by_id = {chunk.id: chunk for chunk in knowledge.chunks_by_ids(chunk_ids)}
    hydrated: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        citations: list[dict[str, object]] = []
        raw_ids = item.get("chunk_ids")
        if isinstance(raw_ids, list):
            for chunk_id in raw_ids:
                if not isinstance(chunk_id, str):
                    continue
                chunk = by_id.get(chunk_id)
                if chunk is None:
                    citations.append({"chunk_id": chunk_id, "missing": True})
                    continue
                citations.append(
                    {
                        "chunk_id": chunk.id,
                        "filename": chunk.filename,
                        "section_header": chunk.section_header,
                        "excerpt": chunk.content[:500],
                        "missing": False,
                    }
                )
        copied = dict(item)
        copied["citations"] = citations
        hydrated.append(copied)
    classification["items"] = hydrated
    payload["classification"] = classification
    return payload


def _db(request: Request) -> Database:
    database: Database = request.app.state.db
    return database
