"""Hybrid retrieval, cited traffic lights, and the stored match score (ADR-008)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.config import Settings
from app.db.database import Database
from app.db.repositories.bundles import ChunkHit, KnowledgeRepository
from app.db.repositories.evaluations import EvaluationRepository
from app.db.repositories.profile import ProfileRepository
from app.db.repositories.projects import ProjectRepository
from app.jobs import JobContext, JobRunner
from app.llm.ollama_client import ModelRole, OllamaClient, OllamaError
from app.llm.prompts import load_prompt
from app.llm.schemas.matching import JudgementBatch, RequirementKeywords
from app.services.ats_engine import (
    AliasDictionary,
    ClassifiedItem,
    Keyword,
    count_forms,
    fts_query,
    lexical_report,
    load_aliases,
    match_percentage,
    reciprocal_rank_fusion,
    verify_citation,
)
from app.services.parser import fold_diacritics
from app.vector_store import VECTOR_SIZE, VectorStore

log = logging.getLogger(__name__)

KEYWORD_PROMPT = "extract_keywords_v1"
CLASSIFY_PROMPT = "classify_match_v1"


def register_evaluation_handler(
    runner: JobRunner,
    db: Database,
    ollama: OllamaClient,
    vectors: VectorStore,
    settings: Settings,
) -> None:
    async def handle(ctx: JobContext) -> dict[str, object]:
        project_id = ctx.payload.get("project_id")
        bundle_ids = ctx.payload.get("bundle_ids")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("evaluate needs a project_id")
        if not isinstance(bundle_ids, list):
            raise ValueError("evaluate needs bundle_ids")
        selected: list[str] = []
        for item in bundle_ids:
            if not isinstance(item, str):
                raise ValueError("evaluate needs bundle_ids")
            selected.append(item)
        if not selected:
            raise ValueError("evaluate needs bundle_ids")
        return await evaluate_project(
            project_id,
            bundle_ids=selected,
            weights=ctx.payload.get("weights"),
            db=db,
            ollama=ollama,
            vectors=vectors,
            settings=settings,
            progress=ctx.progress,
        )

    runner.register("evaluate_match", handle, llm=True)


async def evaluate_project(
    project_id: str,
    *,
    bundle_ids: list[str],
    weights: object,
    db: Database,
    ollama: OllamaClient,
    vectors: VectorStore,
    settings: Settings,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, object]:
    def report(value: int, message: str) -> None:
        if progress is not None:
            progress(value, message)

    if not bundle_ids:
        raise ValueError("select at least one bundle")
    project = ProjectRepository(db).get(project_id)
    if project is None:
        raise ValueError("project not found")
    knowledge = KnowledgeRepository(db)
    for bundle_id in bundle_ids:
        if knowledge.get_bundle(bundle_id) is None:
            raise ValueError(f"unknown bundle {bundle_id}")
    chosen_weights = resolve_weights(weights, settings)
    report(15, "extracting keywords")
    keywords = await extract_keywords(project.requirements, project.offerings, ollama)
    if not keywords:
        raise RuntimeError("the model returned no keywords to score")
    report(35, "lexical match")
    aliases = load_aliases()
    lexical = lexical_report(
        keywords,
        bundle_ids=bundle_ids,
        repository=knowledge,
        aliases=aliases,
    )
    report(55, "retrieving")
    retrieved: dict[str, list[ChunkHit]] = {}
    for keyword in keywords:
        retrieved[keyword.term] = await retrieve_for_keyword(
            keyword,
            bundle_ids=bundle_ids,
            knowledge=knowledge,
            vectors=vectors,
            ollama=ollama,
            settings=settings,
            aliases=aliases,
        )
    confirmed = [
        skill.skill for skill in ProfileRepository(db).get().profile.skills if skill.confirmed
    ]
    report(75, "classifying")
    system, prompt_version = load_prompt(CLASSIFY_PROMPT)
    judgement = await ollama.structured(
        ModelRole.heavy,
        _classification_prompt(keywords, retrieved, confirmed),
        JudgementBatch,
        system=system,
    )
    items = _align(keywords, judgement, retrieved)
    score = match_percentage(items, chosen_weights)
    report(90, "saving")
    saved = EvaluationRepository(db).insert(
        project_id=project_id,
        lexical_report=lexical.as_dict(),
        classification={
            "bundle_ids": bundle_ids,
            "items": [item.as_dict() for item in items],
            "profile_skills_used": confirmed,
            "keyword_prompt_version": KEYWORD_PROMPT,
        },
        match_percentage=score,
        score_weights=chosen_weights,
        model_name=ollama.model_name(ModelRole.heavy),
        prompt_version=prompt_version,
    )
    report(100, "scored")
    return {"evaluation_id": saved.id, "match_percentage": score}


async def extract_keywords(
    requirements: dict[str, object] | None,
    offerings: list[object] | None,
    ollama: OllamaClient,
) -> list[Keyword]:
    mandatory = _strings(None if requirements is None else requirements.get("mandatory"))
    preferred = _strings(None if requirements is None else requirements.get("preferred"))
    offered = _strings(offerings)
    if not mandatory and not preferred and not offered:
        raise RuntimeError("the project has no requirements or offerings to match")
    system, _version = load_prompt(KEYWORD_PROMPT)
    prompt = (
        "Mandatory:\n"
        + _lines(mandatory)
        + "\nPreferred:\n"
        + _lines(preferred)
        + "\nOfferings:\n"
        + _lines(offered)
    )
    extracted = await ollama.structured(
        ModelRole.fast,
        prompt,
        RequirementKeywords,
        system=system,
    )
    keywords: list[Keyword] = []
    seen: set[tuple[str, str]] = set()
    for item in extracted.keywords:
        marker = (fold_diacritics(item.term), item.group)
        if marker in seen:
            continue
        seen.add(marker)
        keywords.append(Keyword(term=item.term, group=item.group, category=item.category))
    return keywords


async def retrieve_for_keyword(
    keyword: Keyword,
    *,
    bundle_ids: list[str],
    knowledge: KnowledgeRepository,
    vectors: VectorStore,
    ollama: OllamaClient,
    settings: Settings,
    aliases: AliasDictionary,
) -> list[ChunkHit]:
    forms = aliases.forms(keyword.term)
    fts_hits = knowledge.search_fts(fts_query(forms), bundle_ids)
    fts_ids: list[str] = []
    seen: set[str] = set()
    for hit in fts_hits:
        if count_forms(hit.content, forms) <= 0 or hit.id in seen:
            continue
        seen.add(hit.id)
        fts_ids.append(hit.id)
    dense_ids = await _dense_ids(
        keyword.term,
        bundle_ids,
        vectors,
        ollama,
        settings.retrieval_top_k,
    )
    fused = reciprocal_rank_fusion(
        [dense_ids, fts_ids],
        k=settings.rrf_k,
        limit=settings.retrieval_top_k,
    )
    by_id = {hit.id: hit for hit in knowledge.chunks_by_ids(fused)}
    return [by_id[chunk_id] for chunk_id in fused if chunk_id in by_id]


def resolve_weights(raw: object, settings: Settings) -> dict[str, float]:
    weights = dict(settings.score_weights())
    if raw is None:
        return weights
    if not isinstance(raw, dict):
        raise ValueError("weights must be an object")
    for key in ("mandatory", "preferred", "offerings"):
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"weight {key} must be a number")
        if float(value) < 0:
            raise ValueError(f"weight {key} must be non-negative")
        weights[key] = float(value)
    if sum(weights.values()) <= 0:
        raise ValueError("score weights must sum to more than zero")
    return weights


async def _dense_ids(
    term: str,
    bundle_ids: list[str],
    vectors: VectorStore,
    ollama: OllamaClient,
    limit: int,
) -> list[str]:
    try:
        embedded = await ollama.embed([term])
    except OllamaError as exc:
        log.info("dense retrieval skipped: %s", exc)
        return []
    if not embedded or len(embedded[0]) != VECTOR_SIZE:
        return []
    hits = vectors.search(embedded[0], limit=limit, bundle_ids=bundle_ids)
    return [hit.id for hit in hits]


def _align(
    keywords: list[Keyword],
    judgement: JudgementBatch,
    retrieved: dict[str, list[ChunkHit]],
) -> list[ClassifiedItem]:
    by_term = {fold_diacritics(item.term): item for item in judgement.items}
    aligned: list[ClassifiedItem] = []
    for keyword in keywords:
        allowed = {chunk.id for chunk in retrieved.get(keyword.term, [])}
        found = by_term.get(fold_diacritics(keyword.term))
        if found is None:
            aligned.append(
                verify_citation(
                    term=keyword.term,
                    group=keyword.group,
                    category=keyword.category,
                    light="RED",
                    rationale="The model omitted this requirement.",
                    chunk_ids=[],
                    allowed_ids=allowed,
                )
            )
            continue
        aligned.append(
            verify_citation(
                term=keyword.term,
                group=keyword.group,
                category=keyword.category,
                light=found.light,
                rationale=found.rationale,
                chunk_ids=found.chunk_ids,
                allowed_ids=allowed,
            )
        )
    return aligned


def _classification_prompt(
    keywords: list[Keyword],
    retrieved: dict[str, list[ChunkHit]],
    confirmed: list[str],
) -> str:
    lines = ["Owner-confirmed skills (context only, not citations):"]
    if confirmed:
        lines.extend(f"- {skill}" for skill in confirmed)
    else:
        lines.append("- none")
    for keyword in keywords:
        lines.append(f"\nRequirement: {keyword.term} ({keyword.group}, {keyword.category})")
        chunks = retrieved.get(keyword.term, [])
        if not chunks:
            lines.append("Retrieved chunks: none")
            continue
        lines.append("Retrieved chunks:")
        for chunk in chunks:
            excerpt = " ".join(chunk.content.split())
            if len(excerpt) > 600:
                excerpt = excerpt[:600]
            header = chunk.section_header or "-"
            lines.append(
                f"- CHUNK id={chunk.id} file={chunk.filename} section={header}\n  {excerpt}"
            )
    return "\n".join(lines)


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _lines(items: list[str]) -> str:
    if not items:
        return "- none"
    return "\n".join(f"- {item}" for item in items)
