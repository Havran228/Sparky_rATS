"""One registry, two surfaces: Ollama tool calls and FastMCP (ADR-010)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from app.runtime import Runtime
from app.services.google_client import TimelineResult
from app.services.salary_calc import SalaryBreakdown
from app.tools import handlers
from app.tools.handlers import ToolError
from app.tools.models import (
    CalcNetSalaryParams,
    CompilePdfParams,
    EvaluateMatchParams,
    FetchTimelineParams,
    GetProjectParams,
    GetProjectResult,
    IngestJobParams,
    JobResult,
    ListProjectsParams,
    ListProjectsResult,
    QueryBundlesParams,
    QueryBundlesResult,
)

TOOL_NAMES: tuple[str, ...] = (
    "ingest_job",
    "list_projects",
    "get_project_details",
    "query_knowledge_bundles",
    "evaluate_match",
    "calc_net_salary",
    "compile_tailored_pdf",
    "fetch_google_timeline",
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    side_effects: bool
    params: type[BaseModel]
    result: type[BaseModel]
    handler: Callable[[BaseModel, Runtime], Awaitable[BaseModel]]


def _spec[P: BaseModel, R: BaseModel](
    name: str,
    description: str,
    *,
    side_effects: bool,
    params: type[P],
    result: type[R],
    handler: Callable[[P, Runtime], Awaitable[R]],
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        side_effects=side_effects,
        params=params,
        result=result,
        handler=handler,  # type: ignore[arg-type]
    )


REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        _spec(
            "ingest_job",
            "Queue ingestion of a Profesia link, id, or pasted posting. Returns a job id.",
            side_effects=True,
            params=IngestJobParams,
            result=JobResult,
            handler=handlers.ingest_job,
        ),
        _spec(
            "list_projects",
            "List job projects with title, company, and derived status.",
            side_effects=False,
            params=ListProjectsParams,
            result=ListProjectsResult,
            handler=handlers.list_projects,
        ),
        _spec(
            "get_project_details",
            "Read one project, including requirements, contacts, and pipeline stages.",
            side_effects=False,
            params=GetProjectParams,
            result=GetProjectResult,
            handler=handlers.get_project_details,
        ),
        _spec(
            "query_knowledge_bundles",
            "Search bundle chunks by words. Returns chunk ids the reply may cite.",
            side_effects=False,
            params=QueryBundlesParams,
            result=QueryBundlesResult,
            handler=handlers.query_knowledge_bundles,
        ),
        _spec(
            "evaluate_match",
            "Queue a cited traffic-light evaluation for a project and bundle ids.",
            side_effects=True,
            params=EvaluateMatchParams,
            result=JobResult,
            handler=handlers.evaluate_match,
        ),
        _spec(
            "calc_net_salary",
            "Convert monthly gross EUR to net and employer cost for a stored tax year.",
            side_effects=False,
            params=CalcNetSalaryParams,
            result=SalaryBreakdown,
            handler=handlers.calc_net_salary,
        ),
        _spec(
            "compile_tailored_pdf",
            "Queue a sandboxed CV or cover letter. Returns a job id, not a PDF.",
            side_effects=True,
            params=CompilePdfParams,
            result=JobResult,
            handler=handlers.compile_tailored_pdf,
        ),
        _spec(
            "fetch_google_timeline",
            "Read Gmail and Calendar for a project. Does not attach messages or send mail.",
            side_effects=False,
            params=FetchTimelineParams,
            result=TimelineResult,
            handler=handlers.fetch_google_timeline,
        ),
    )
}


def tool_catalog() -> list[dict[str, object]]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "side_effects": spec.side_effects,
            "parameters": spec.params.model_json_schema(),
        }
        for spec in REGISTRY.values()
    ]


def ollama_tools() -> list[dict[str, object]]:
    tools: list[dict[str, object]] = []
    for spec in REGISTRY.values():
        schema = spec.params.model_json_schema()
        schema.pop("title", None)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": schema,
                },
            }
        )
    return tools


async def invoke(name: str, arguments: dict[str, object], runtime: Runtime) -> dict[str, object]:
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(f"unknown tool {name}")
    try:
        params = spec.params.model_validate(arguments)
    except ValidationError as exc:
        raise ToolError(exc.errors()[0]["msg"] if exc.errors() else "invalid arguments") from exc
    try:
        result = await spec.handler(params, runtime)
    except ToolError:
        raise
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    dumped = result.model_dump(mode="json")
    if not isinstance(dumped, dict):
        raise ToolError("tool result was not an object")
    return {str(key): value for key, value in dumped.items()}
