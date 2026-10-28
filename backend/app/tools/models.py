"""Parameter and result models for the single tool registry (ADR-010)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JobResult(BaseModel):
    job_id: str


class IngestJobParams(BaseModel):
    input: str = Field(description="Profesia URL, seven-digit id, or pasted posting text")


class ListProjectsParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class ProjectSummary(BaseModel):
    id: str
    title: str | None = None
    company: str | None = None
    current_status: str
    contract_type: str | None = None


class ListProjectsResult(BaseModel):
    projects: list[ProjectSummary]


class GetProjectParams(BaseModel):
    project_id: str


class GetProjectResult(BaseModel):
    project: dict[str, object]


class QueryBundlesParams(BaseModel):
    query: str = Field(description="Words to find in bundle chunks")
    bundle_ids: list[str] | None = None
    limit: int = Field(default=8, ge=1, le=20)


class ChunkHitModel(BaseModel):
    chunk_id: str
    bundle_id: str
    filename: str
    section_header: str
    excerpt: str


class QueryBundlesResult(BaseModel):
    chunks: list[ChunkHitModel]


class EvaluateMatchParams(BaseModel):
    project_id: str
    bundle_ids: list[str] = Field(min_length=1)


class CalcNetSalaryParams(BaseModel):
    gross: str = Field(description="Monthly gross EUR")
    tax_year: int
    claim_nczd: bool = True
    contract_type: str = "TPP"


class CompilePdfParams(BaseModel):
    project_id: str
    doc_type: Literal["CV", "COVER_LETTER"] = "CV"
    language: Literal["en", "sk"] = "en"
    bundle_ids: list[str] | None = None
    evaluation_id: str | None = None
    raw_markup: str | None = Field(
        default=None,
        description="Advanced path. Compiles this Typst under the same sandbox.",
    )


class FetchTimelineParams(BaseModel):
    project_id: str
    domains: list[str] | None = None
    lookback_days: int | None = None
