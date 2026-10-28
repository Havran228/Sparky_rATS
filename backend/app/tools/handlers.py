"""Tool handlers. Each one is the only implementation of that tool (ADR-010)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.db.repositories.bundles import KnowledgeRepository
from app.db.repositories.projects import ProjectRepository
from app.db.repositories.stages import StageRepository
from app.runtime import Runtime
from app.services.google_client import GoogleError, TimelineResult
from app.services.salary_calc import SalaryBreakdown, UnknownTaxYear, calculate, load_tax_params
from app.services.timeline import contact_emails
from app.tools.models import (
    CalcNetSalaryParams,
    ChunkHitModel,
    CompilePdfParams,
    EvaluateMatchParams,
    FetchTimelineParams,
    GetProjectParams,
    GetProjectResult,
    IngestJobParams,
    JobResult,
    ListProjectsParams,
    ListProjectsResult,
    ProjectSummary,
    QueryBundlesParams,
    QueryBundlesResult,
)

_WORD = re.compile(r"[\w]+", re.UNICODE)


class ToolError(RuntimeError):
    """A tool rejected its arguments or could not finish."""


async def ingest_job(params: IngestJobParams, runtime: Runtime) -> JobResult:
    job_id = runtime.jobs.enqueue("ingest_job", {"input": params.input})
    return JobResult(job_id=job_id)


async def list_projects(params: ListProjectsParams, runtime: Runtime) -> ListProjectsResult:
    records = ProjectRepository(runtime.db).list_projects()[: params.limit]
    return ListProjectsResult(
        projects=[
            ProjectSummary(
                id=record.id,
                title=record.title,
                company=record.company,
                current_status=record.current_status,
                contract_type=record.contract_type,
            )
            for record in records
        ]
    )


async def get_project_details(params: GetProjectParams, runtime: Runtime) -> GetProjectResult:
    record = ProjectRepository(runtime.db).get(params.project_id)
    if record is None:
        raise ToolError("project not found")
    payload = record.public_dict()
    raw = payload.get("raw_content_markdown")
    if isinstance(raw, str) and len(raw) > 1500:
        payload["raw_content_markdown"] = raw[:1500] + "…"
    stages = StageRepository(runtime.db).list_for_project(record.id)
    payload["stages"] = [stage.public_dict() for stage in stages]
    return GetProjectResult(project=payload)


async def query_knowledge_bundles(
    params: QueryBundlesParams,
    runtime: Runtime,
) -> QueryBundlesResult:
    match = _fts_query(params.query)
    if not match:
        raise ToolError("query needs at least one word")
    hits = KnowledgeRepository(runtime.db).search_fts_limited(
        match,
        params.bundle_ids or None,
        params.limit,
    )
    return QueryBundlesResult(
        chunks=[
            ChunkHitModel(
                chunk_id=hit.id,
                bundle_id=hit.bundle_id,
                filename=hit.filename,
                section_header=hit.section_header,
                excerpt=hit.content[:500],
            )
            for hit in hits
        ]
    )


async def evaluate_match(params: EvaluateMatchParams, runtime: Runtime) -> JobResult:
    if ProjectRepository(runtime.db).get(params.project_id) is None:
        raise ToolError("project not found")
    job_id = runtime.jobs.enqueue(
        "evaluate_match",
        {
            "project_id": params.project_id,
            "bundle_ids": params.bundle_ids,
            "weights": None,
        },
    )
    return JobResult(job_id=job_id)


async def calc_net_salary(params: CalcNetSalaryParams, runtime: Runtime) -> SalaryBreakdown:
    try:
        gross = Decimal(params.gross)
    except InvalidOperation as exc:
        raise ToolError("gross must be a decimal amount") from exc
    try:
        tax = load_tax_params(runtime.db, params.tax_year)
    except UnknownTaxYear as exc:
        raise ToolError(str(exc)) from exc
    try:
        return calculate(
            gross,
            tax,
            tax_year=params.tax_year,
            claim_nczd=params.claim_nczd,
            contract_type=params.contract_type,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


async def compile_tailored_pdf(params: CompilePdfParams, runtime: Runtime) -> JobResult:
    if ProjectRepository(runtime.db).get(params.project_id) is None:
        raise ToolError("project not found")
    job_id = runtime.jobs.enqueue(
        "generate_document",
        {
            "project_id": params.project_id,
            "doc_type": params.doc_type,
            "language": params.language,
            "evaluation_id": params.evaluation_id,
            "bundle_ids": params.bundle_ids or [],
            "raw_markup": params.raw_markup,
        },
    )
    return JobResult(job_id=job_id)


async def fetch_google_timeline(
    params: FetchTimelineParams,
    runtime: Runtime,
) -> TimelineResult:
    record = ProjectRepository(runtime.db).get(params.project_id)
    if record is None:
        raise ToolError("project not found")
    emails = contact_emails(record.contacts)
    try:
        return await runtime.google.timeline(
            project_id=record.id,
            domains=params.domains or [],
            emails=emails,
            lookback_days=params.lookback_days,
        )
    except GoogleError as exc:
        raise ToolError(str(exc)) from exc


def _fts_query(text: str) -> str:
    words = _WORD.findall(text)
    if not words:
        return ""
    return " OR ".join(f'"{word}"' for word in words[:12])
