"""Propose a skills inventory and merge it into the singleton profile (ADR-016)."""

from __future__ import annotations

from collections.abc import Callable

from app.db.database import Database
from app.db.repositories.bundles import KnowledgeRepository
from app.db.repositories.profile import ProfileRepository
from app.jobs import JobContext, JobRunner
from app.llm.ollama_client import ModelRole, OllamaClient
from app.llm.prompts import load_prompt
from app.llm.schemas.profile import CandidateProfile, SkillItem, SkillProposal
from app.services.parser import fold_diacritics

PROPOSE_PROMPT = "propose_skills_v1"


def register_profile_handler(runner: JobRunner, db: Database, ollama: OllamaClient) -> None:
    async def handle(ctx: JobContext) -> dict[str, object]:
        bundle_ids = ctx.payload.get("bundle_ids")
        if not isinstance(bundle_ids, list):
            raise ValueError("propose needs bundle_ids")
        selected: list[str] = []
        for item in bundle_ids:
            if not isinstance(item, str):
                raise ValueError("propose needs bundle_ids")
            selected.append(item)
        if not selected:
            raise ValueError("propose needs bundle_ids")
        return await propose_inventory(selected, db=db, ollama=ollama, progress=ctx.progress)

    runner.register("propose_skills", handle, llm=True)


async def propose_inventory(
    bundle_ids: list[str],
    *,
    db: Database,
    ollama: OllamaClient,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, object]:
    def report(value: int, message: str) -> None:
        if progress is not None:
            progress(value, message)

    if not bundle_ids:
        raise ValueError("select at least one bundle")
    knowledge = KnowledgeRepository(db)
    for bundle_id in bundle_ids:
        if knowledge.get_bundle(bundle_id) is None:
            raise ValueError(f"unknown bundle {bundle_id}")
    report(30, "reading bundles")
    excerpt = knowledge.bundle_chunk_excerpt(bundle_ids, limit_chars=8000)
    if not excerpt.strip():
        raise RuntimeError("the selected bundles have no extracted text")
    report(60, "proposing")
    system, prompt_version = load_prompt(PROPOSE_PROMPT)
    proposal = await ollama.structured(ModelRole.fast, excerpt, SkillProposal, system=system)
    repository = ProfileRepository(db)
    merged = merge_proposal(repository.get().profile, proposal, bundle_ids)
    saved = repository.save(merged)
    report(100, "proposed")
    return {
        "updated_at": saved.updated_at,
        "skill_count": len(saved.profile.skills),
        "prompt_version": prompt_version,
    }


def merge_proposal(
    profile: CandidateProfile,
    proposal: SkillProposal,
    bundle_ids: list[str],
) -> CandidateProfile:
    skills = list(profile.skills)
    index = {fold_diacritics(item.skill): position for position, item in enumerate(skills)}
    for proposed in proposal.skills:
        key = fold_diacritics(proposed.skill)
        if not key:
            continue
        if key in index:
            current = skills[index[key]]
            evidence = _merge_ids(current.evidence_bundle_ids, bundle_ids)
            if current.confirmed:
                skills[index[key]] = current.model_copy(update={"evidence_bundle_ids": evidence})
                continue
            skills[index[key]] = current.model_copy(
                update={
                    "years": current.years if proposed.years is None else proposed.years,
                    "last_used": proposed.last_used or current.last_used,
                    "evidence_bundle_ids": evidence,
                }
            )
            continue
        skills.append(
            SkillItem(
                skill=proposed.skill,
                years=proposed.years,
                last_used=proposed.last_used,
                evidence_bundle_ids=list(bundle_ids),
                confirmed=False,
            )
        )
        index[key] = len(skills) - 1
    languages = list(profile.languages)
    known = {fold_diacritics(item.language) for item in languages}
    for language in proposal.languages:
        marker = fold_diacritics(language.language)
        if marker and marker not in known:
            languages.append(language)
            known.add(marker)
    return profile.model_copy(update={"skills": skills, "languages": languages})


def _merge_ids(current: list[str], extra: list[str]) -> list[str]:
    merged: list[str] = []
    for item in [*current, *extra]:
        if item not in merged:
            merged.append(item)
    return merged
