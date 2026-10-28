"""Project chat. The reply is a job because the tool loop can take a while (ADR-014)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db.repositories.chats import ChatRepository
from app.db.repositories.projects import ProjectRepository
from app.runtime import Runtime
from app.tools.registry import tool_catalog

router = APIRouter()


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    thread_id: str | None = None


def install_chat_routes(app: FastAPI) -> None:
    app.include_router(router)


@router.get("/tools")
async def list_tools() -> JSONResponse:
    return JSONResponse({"tools": tool_catalog()})


@router.post("/projects/{project_id}/chat")
async def post_chat(project_id: str, body: ChatBody, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    if ProjectRepository(runtime.db).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    thread_id = body.thread_id.strip() if body.thread_id else str(uuid.uuid4())
    job_id = runtime.jobs.enqueue(
        "project_chat",
        {"project_id": project_id, "thread_id": thread_id, "message": body.message.strip()},
    )
    return JSONResponse({"job_id": job_id, "thread_id": thread_id}, status_code=202)


@router.get("/projects/{project_id}/chats")
async def list_messages(project_id: str, request: Request, thread_id: str) -> JSONResponse:
    if ProjectRepository(_runtime(request).db).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    records = ChatRepository(_runtime(request).db).list_thread(project_id, thread_id)
    return JSONResponse(
        {"thread_id": thread_id, "messages": [record.public_dict() for record in records]}
    )


@router.get("/projects/{project_id}/threads")
async def list_threads(project_id: str, request: Request) -> JSONResponse:
    if ProjectRepository(_runtime(request).db).get(project_id) is None:
        return JSONResponse({"detail": "project not found"}, status_code=404)
    return JSONResponse({"threads": ChatRepository(_runtime(request).db).list_threads(project_id)})


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime
