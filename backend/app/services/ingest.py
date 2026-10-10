"""Turn a Profesia link, id, or pasted posting into a project (ADR-006, ADR-018)."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.config import Settings
from app.db.database import Database
from app.db.repositories.projects import ProjectRepository
from app.jobs import JobContext, JobRunner
from app.llm.ollama_client import ModelRole, OllamaClient
from app.llm.prompts import load_prompt
from app.llm.schemas.job_posting import JobPostingExtract, SalaryExtract, SalaryPeriod
from app.services import scraper
from app.services.salary_text import parse_salary_text
from app.services.scraper import InputKind, classify, profesia_url_for

PROMPT_NAME = "extract_job_v1"
Progress = Callable[[int, str], None]


def register_ingest_handler(
    runner: JobRunner,
    db: Database,
    ollama: OllamaClient,
    settings: Settings,
) -> None:
    async def handle(ctx: JobContext) -> dict[str, object]:
        raw = ctx.payload.get("input")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("ingest input must be a non-empty string")
        return await ingest_input(
            raw,
            db=db,
            ollama=ollama,
            data_dir=settings.data_dir,
            progress=ctx.progress,
        )

    runner.register("ingest_job", handle, llm=True)


async def ingest_input(
    raw: str,
    *,
    db: Database,
    ollama: OllamaClient,
    data_dir: Path,
    progress: Progress | None = None,
) -> dict[str, object]:
    def report(value: int, message: str) -> None:
        if progress is not None:
            progress(value, message)

    classified = classify(raw)
    report(10, "classified")
    if classified.kind is InputKind.FILE:
        name = classified.filename or "upload"
        mime = classified.mime_type or "unknown type"
        raise RuntimeError(
            f"File parsing for {name} ({mime}) arrives in slice 2. "
            "Paste the posting text or a link."
        )

    markdown = raw.strip()
    job_posting: dict[str, object] | None = None
    source_ref = raw.strip()
    if classified.kind is InputKind.PROFESIA_ID:
        source_ref = profesia_url_for(classified.profesia_id or "")
    elif classified.url:
        source_ref = classified.url

    if classified.kind in {InputKind.PROFESIA_ID, InputKind.PROFESIA_URL, InputKind.OTHER_URL}:
        report(30, "fetching")
        fetched = await scraper.fetch_posting(
            source_ref,
            cache_dir=data_dir / "bundles" / "_raw",
        )
        markdown = fetched.markdown
        job_posting = fetched.job_posting

    report(60, "extracting")
    system, prompt_version = load_prompt(PROMPT_NAME)
    extract = await ollama.structured(
        ModelRole.fast,
        _prompt(markdown, job_posting),
        JobPostingExtract,
        system=system,
    )
    extract = _fill_gaps(extract, markdown, job_posting)
    report(85, "saving")
    saved = ProjectRepository(db).insert(
        source_kind=classified.kind.value,
        source_ref=source_ref,
        company=extract.company or _org_name(job_posting),
        title=extract.title or _string(job_posting, "title"),
        contract_type=extract.contract_type,
        salary=extract.salary.model_dump(),
        language=extract.language,
        requirements={
            "mandatory": extract.mandatory_requirements,
            "preferred": extract.preferred_requirements,
        },
        offerings=extract.offerings,
        contacts=extract.contacts,
        dates={"posted": extract.date_posted, "valid_through": extract.valid_through},
        raw_content_markdown=markdown,
        model_name=ollama.model_name(ModelRole.fast),
        prompt_version=prompt_version,
    )
    report(100, "saved")
    return {"project_id": saved.id}


def _prompt(markdown: str, job_posting: dict[str, object] | None) -> str:
    body = markdown.strip()
    if len(body) > 12000:
        body = body[:12000]
    structured = ""
    if job_posting is not None:
        structured = json.dumps(job_posting, ensure_ascii=False, default=str)
    return f"Posting text:\n{body}\n\nStructured JSON-LD:\n{structured or '{}'}"


def _fill_gaps(
    extract: JobPostingExtract,
    markdown: str,
    job_posting: dict[str, object] | None,
) -> JobPostingExtract:
    salary = extract.salary
    if salary.minimum is None and salary.maximum is None:
        from_json = _salary_from_json_ld(job_posting)
        parsed = parse_salary_text(salary.raw) or _salary_line(markdown)
        donor = from_json or parsed
        if donor is not None:
            salary = donor
    elif salary.raw and salary.basis == "unknown":
        parsed = parse_salary_text(salary.raw)
        if parsed is not None and parsed.basis != "unknown":
            salary = salary.model_copy(update={"basis": parsed.basis, "period": parsed.period})
    company = extract.company or _org_name(job_posting)
    title = extract.title or _string(job_posting, "title")
    return extract.model_copy(update={"salary": salary, "company": company, "title": title})


def _salary_line(markdown: str) -> SalaryExtract | None:
    for line in markdown.splitlines():
        if any(token in line.lower() for token in ("eur", "€", "od ", "brutto", "netto", "mzda")):
            parsed = parse_salary_text(line)
            if parsed is not None:
                return parsed
    return None


def _salary_from_json_ld(job: dict[str, object] | None) -> SalaryExtract | None:
    if job is None:
        return None
    base = job.get("baseSalary")
    if not isinstance(base, dict):
        return None
    currency = base.get("currency")
    value = base.get("value")
    minimum: str | None = None
    maximum: str | None = None
    unit = ""
    if isinstance(value, dict):
        minimum = _number(value.get("minValue")) or _number(value.get("value"))
        maximum = _number(value.get("maxValue"))
        unit_text = value.get("unitText")
        unit = unit_text if isinstance(unit_text, str) else ""
    else:
        minimum = _number(value)
    period: SalaryPeriod = "month"
    if "HOUR" in unit.upper():
        period = "hour"
    elif "YEAR" in unit.upper():
        period = "year"
    if minimum is None and maximum is None:
        return None
    return SalaryExtract(
        minimum=minimum,
        maximum=maximum,
        currency=currency if isinstance(currency, str) else "EUR",
        period=period,
        basis="unknown",
        raw=unit,
    )


def _org_name(job: dict[str, object] | None) -> str | None:
    if job is None:
        return None
    org = job.get("hiringOrganization")
    if isinstance(org, dict):
        name = org.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _string(job: dict[str, object] | None, key: str) -> str | None:
    if job is None:
        return None
    value = job.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _number(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str, Decimal)):
        text = str(value).strip()
        if not text:
            return None
        try:
            return format(Decimal(text), "f")
        except InvalidOperation:
            return None
    return None
