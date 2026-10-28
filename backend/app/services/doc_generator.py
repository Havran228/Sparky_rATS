"""Ground a document model, compile it, and store the version (ADR-009, ADR-015).

The heavy role proposes the prose. The profile wins for the header, languages,
and the skill allow-list. Employers and schools that do not appear in the
supplied evidence are dropped before Typst sees them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.db.database import Database
from app.db.repositories.bundles import ChunkDraft, KnowledgeRepository
from app.db.repositories.documents import DocumentRepository, GeneratedDocumentRecord
from app.db.repositories.evaluations import EvaluationRecord, EvaluationRepository
from app.db.repositories.profile import ProfileRepository
from app.db.repositories.projects import ProjectRecord, ProjectRepository
from app.jobs import JobContext, JobRunner
from app.llm.ollama_client import ModelRole, OllamaClient
from app.llm.prompts import load_prompt
from app.llm.schemas.documents import (
    CoverLetterModel,
    EducationEntry,
    ExperienceEntry,
    ResumeContact,
    ResumeLanguage,
    ResumeModel,
    SkillGroup,
)
from app.llm.schemas.profile import CandidateProfile
from app.services.ats_engine import (
    AliasDictionary,
    Keyword,
    count_forms,
    lexical_report_text,
    load_aliases,
)
from app.services.bundle_ingest import embed_document
from app.services.parser import ParsedSection, chunk_sections, fold_diacritics
from app.services.typst_compile import (
    CompileError,
    compile_raw_markup,
    compile_template,
    extract_pdf_text,
)
from app.vector_store import VectorStore

RESUME_PROMPT = "tailor_resume_v1"
LETTER_PROMPT = "cover_letter_v1"
EVIDENCE_CHAR_LIMIT = 12_000
_LANGUAGE_NAME = {"en": "English", "sk": "Slovak"}
_GROUPS = frozenset({"mandatory", "preferred", "offering"})


@dataclass(frozen=True)
class Grounding:
    dropped_skills: list[str]
    dropped_experience: list[str]
    dropped_education: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "dropped_skills": self.dropped_skills,
            "dropped_experience": self.dropped_experience,
            "dropped_education": self.dropped_education,
            "contact_source": "candidate_profile",
            "languages_source": "candidate_profile",
        }


def register_document_handlers(
    runner: JobRunner,
    db: Database,
    ollama: OllamaClient,
    vectors: VectorStore,
    settings: Settings,
) -> None:
    async def generate(ctx: JobContext) -> dict[str, object]:
        return await generate_document(
            ctx.payload,
            db=db,
            ollama=ollama,
            settings=settings,
            progress=ctx.progress,
            cancelled=ctx.raise_if_cancelled,
        )

    async def submit(ctx: JobContext) -> dict[str, object]:
        document_id = ctx.payload.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("submit needs a document_id")
        return await submit_document(
            document_id,
            db=db,
            ollama=ollama,
            vectors=vectors,
            settings=settings,
            progress=ctx.progress,
        )

    runner.register("generate_document", generate, llm=True)
    runner.register("submit_document", submit, llm=True)


async def generate_document(
    payload: dict[str, object],
    *,
    db: Database,
    ollama: OllamaClient,
    settings: Settings,
    progress: Callable[[int, str], None] | None = None,
    cancelled: Callable[[], None] | None = None,
) -> dict[str, object]:
    def report(value: int, message: str) -> None:
        if cancelled is not None:
            cancelled()
        if progress is not None:
            progress(value, message)

    project_id = _required_str(payload, "project_id")
    doc_type = _required_str(payload, "doc_type")
    language = _required_str(payload, "language")
    if doc_type not in {"CV", "COVER_LETTER"}:
        raise ValueError("doc_type must be CV or COVER_LETTER")
    if language not in _LANGUAGE_NAME:
        raise ValueError("language must be en or sk")
    bundle_ids = _string_list(payload.get("bundle_ids"))
    evaluation_id = payload.get("evaluation_id")
    if evaluation_id is not None and not isinstance(evaluation_id, str):
        raise ValueError("evaluation_id must be a string")
    raw_markup = payload.get("raw_markup")
    if raw_markup is not None and not isinstance(raw_markup, str):
        raise ValueError("raw_markup must be a string")

    report(10, "loading")
    project = ProjectRepository(db).get(project_id)
    if project is None:
        raise ValueError("project not found")
    profile = ProfileRepository(db).get().profile
    contact_from_profile(profile)
    confirmed = [skill.skill for skill in profile.skills if skill.confirmed and skill.skill.strip()]
    if not confirmed:
        raise ValueError("confirm at least one skill on the profile before generating")
    knowledge = KnowledgeRepository(db)
    for bundle_id in bundle_ids:
        if knowledge.get_bundle(bundle_id) is None:
            raise ValueError(f"unknown bundle {bundle_id}")
    chosen_evaluation = evaluation_id if isinstance(evaluation_id, str) else None
    evaluation = _evaluation(db, project_id, chosen_evaluation)
    aliases = load_aliases()

    if isinstance(raw_markup, str):
        report(40, "compiling")
        compiled = await asyncio.to_thread(
            compile_raw_markup,
            raw_markup,
            language=language,
            settings=settings,
        )
        content: dict[str, object] = {"mode": "raw_markup", "markup": raw_markup}
        template = "raw"
        model_name = None
        prompt_version = None
        grounding = None
        used_evaluation = evaluation.id if evaluation is not None else None
    else:
        evidence = collect_evidence(
            knowledge,
            evaluation,
            bundle_ids,
            limit=EVIDENCE_CHAR_LIMIT,
        )
        if not evidence:
            raise ValueError(
                "select a bundle with text, or a match that cited chunks, before generating"
            )
        report(40, "writing")
        if doc_type == "CV":
            system, prompt_version = load_prompt(RESUME_PROMPT)
            proposed = await ollama.structured(
                ModelRole.heavy,
                _resume_prompt(project, profile, evidence, language),
                ResumeModel,
                system=system,
            )
            grounded, grounding = ground_resume(proposed, profile, evidence, aliases)
            content = grounded.model_dump(mode="json")
        else:
            system, prompt_version = load_prompt(LETTER_PROMPT)
            proposed_letter = await ollama.structured(
                ModelRole.heavy,
                _letter_prompt(project, profile, evidence, language),
                CoverLetterModel,
                system=system,
            )
            letter = ground_cover_letter(proposed_letter, profile, letter_date(settings))
            grounding = Grounding(dropped_skills=[], dropped_experience=[], dropped_education=[])
            content = letter.model_dump(mode="json")
        template = template_name(doc_type, language)
        model_name = ollama.model_name(ModelRole.heavy)
        used_evaluation = evaluation.id if evaluation is not None else None
        report(70, "compiling")
        compiled = await asyncio.to_thread(
            compile_template,
            template,
            model_json=json.dumps(content, ensure_ascii=False),
            language=language,
            settings=settings,
        )

    report(85, "round-trip")
    pdf_path = store_pdf(compiled.pdf, settings.exports_dir)
    roundtrip = build_roundtrip(
        compiled.text,
        keywords_for_roundtrip(project, evaluation),
        aliases,
        grounding,
    )
    report(95, "saving")
    saved = DocumentRepository(db).insert(
        project_id=project_id,
        evaluation_id=used_evaluation,
        doc_type=doc_type,
        language=language,
        template=template,
        content=content,
        typst_source=compiled.source,
        pdf_path=str(pdf_path),
        ats_roundtrip_report=roundtrip,
        model_name=model_name,
        prompt_version=prompt_version,
    )
    report(100, "stored")
    missing = roundtrip.get("mandatory_missing")
    return {
        "document_id": saved.id,
        "version": saved.version,
        "pdf_path": saved.pdf_path,
        "mandatory_missing": missing if isinstance(missing, list) else [],
        "dropped_skills": [] if grounding is None else grounding.dropped_skills,
    }


async def submit_document(
    document_id: str,
    *,
    db: Database,
    ollama: OllamaClient,
    vectors: VectorStore,
    settings: Settings,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, object]:
    def report(value: int, message: str) -> None:
        if progress is not None:
            progress(value, message)

    report(15, "loading")
    record = DocumentRepository(db).get(document_id)
    if record is None:
        raise ValueError("document not found")
    if record.pdf_path is None:
        raise ValueError("document has no PDF")
    pdf_path = resolve_export(record.pdf_path, settings)
    text = await asyncio.to_thread(extract_pdf_text, pdf_path, settings)
    if not text.strip():
        raise ValueError("the PDF has no text layer, so it cannot be cited later")
    project = ProjectRepository(db).get(record.project_id)
    if project is None:
        raise ValueError("project not found")
    knowledge = KnowledgeRepository(db)
    bundle = knowledge.active_bundle_for_project(record.project_id)
    if bundle is None:
        bundle = knowledge.insert_bundle(
            name=_bundle_name(project),
            kind="active_application",
            project_id=record.project_id,
        )
    data = pdf_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    existing = knowledge.find_in_bundle_by_hash(bundle.id, digest)
    embedded = False
    embed_error: str | None = None
    if existing is None:
        report(45, "copying")
        dest_dir = settings.bundles_dir / bundle.id
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = _export_filename(record)
        dest = dest_dir / f"{digest[:12]}-{filename}"
        dest.write_bytes(data)
        sections = [ParsedSection(header=record.doc_type, text=text)]
        chunks = chunk_sections(sections, target_tokens=settings.chunk_target_tokens)
        drafts = [
            ChunkDraft(
                chunk_index=chunk.chunk_index,
                section_header=chunk.section_header,
                content=chunk.content,
                content_hash=chunk.content_hash,
                token_count=chunk.token_count,
                language=record.language,
                doc_type=record.doc_type,
            )
            for chunk in chunks
        ]
        report(70, "saving")
        document = knowledge.add_document(
            bundle_id=bundle.id,
            filename=filename,
            doc_type=record.doc_type,
            language=record.language,
            extracted_text=text,
            content_hash=digest,
            file_path=str(dest),
            mime_type="application/pdf",
            project_id=bundle.project_id,
            chunks=drafts,
        )
        report(85, "embedding")
        embedded, embed_error = await embed_document(
            document,
            bundle.project_id,
            ollama,
            vectors,
            knowledge,
        )
    else:
        document = existing
    saved = DocumentRepository(db).set_submitted(record.id, True)
    if saved is None:
        raise RuntimeError("submitted document disappeared")
    report(100, "submitted")
    result: dict[str, object] = {
        "document_id": saved.id,
        "bundle_id": bundle.id,
        "bundle_document_id": document.id,
        "embedded": embedded,
        "duplicate": existing is not None,
    }
    if embed_error:
        result["embed_error"] = embed_error
    return result


def ground_resume(
    model: ResumeModel,
    profile: CandidateProfile,
    evidence: str,
    aliases: AliasDictionary,
) -> tuple[ResumeModel, Grounding]:
    confirmed = [skill.skill for skill in profile.skills if skill.confirmed and skill.skill.strip()]
    kept_groups: list[SkillGroup] = []
    dropped_skills: list[str] = []
    for group in model.skills:
        kept_items = [item for item in group.items if matches_confirmed(item, confirmed, aliases)]
        dropped_skills.extend(item for item in group.items if item not in kept_items)
        if kept_items:
            kept_groups.append(SkillGroup(name=group.name, items=kept_items))
    kept_jobs: list[ExperienceEntry] = []
    dropped_experience: list[str] = []
    for job in model.experience:
        if mentioned(job.company, evidence):
            kept_jobs.append(job)
        else:
            dropped_experience.append(job.company)
    kept_education: list[EducationEntry] = []
    dropped_education: list[str] = []
    for item in model.education:
        if mentioned(item.institution, evidence):
            kept_education.append(item)
        else:
            dropped_education.append(item.institution)
    languages = [
        ResumeLanguage(language=item.language, level=item.cefr) for item in profile.languages
    ]
    grounded = model.model_copy(
        update={
            "contact": contact_from_profile(profile),
            "summary": " ".join(model.summary.split()),
            "skills": kept_groups,
            "experience": kept_jobs,
            "education": kept_education,
            "languages": languages,
        }
    )
    return grounded, Grounding(
        dropped_skills=dropped_skills,
        dropped_experience=dropped_experience,
        dropped_education=dropped_education,
    )


def ground_cover_letter(
    model: CoverLetterModel,
    profile: CandidateProfile,
    today: str,
) -> CoverLetterModel:
    contact = contact_from_profile(profile)
    return model.model_copy(update={"contact": contact, "signature": contact.name, "date": today})


def contact_from_profile(profile: CandidateProfile) -> ResumeContact:
    name = (profile.identity.name or "").strip()
    if not name:
        raise ValueError("confirm your name on the profile before generating")
    return ResumeContact(
        name=name,
        email=profile.contact.email,
        phone=profile.contact.phone,
        location=profile.contact.location,
        links=list(profile.links),
    )


def matches_confirmed(term: str, confirmed: list[str], aliases: AliasDictionary) -> bool:
    if not term.strip() or not confirmed:
        return False
    term_forms = {fold_diacritics(form) for form in aliases.forms(term)}
    term_forms.add(fold_diacritics(term))
    for skill in confirmed:
        skill_forms = {fold_diacritics(form) for form in aliases.forms(skill)}
        skill_forms.add(fold_diacritics(skill))
        if term_forms & skill_forms:
            return True
    return False


def mentioned(needle: str, haystack: str) -> bool:
    if not needle.strip() or not haystack.strip():
        return False
    return count_forms(haystack, [needle]) > 0


def collect_evidence(
    knowledge: KnowledgeRepository,
    evaluation: EvaluationRecord | None,
    bundle_ids: list[str],
    *,
    limit: int,
) -> str:
    parts: list[str] = []
    if evaluation is not None:
        cited = _citation_text(knowledge, evaluation)
        if cited:
            parts.append(cited)
    if bundle_ids:
        excerpt = knowledge.bundle_chunk_excerpt(bundle_ids, limit_chars=limit)
        if excerpt:
            parts.append(excerpt)
    text = "\n\n".join(parts).strip()
    if len(text) > limit:
        return text[:limit].strip()
    return text


def keywords_for_roundtrip(
    project: ProjectRecord,
    evaluation: EvaluationRecord | None,
) -> list[Keyword]:
    from_eval = _keywords_from_evaluation(evaluation)
    if from_eval:
        return from_eval
    return _keywords_from_project(project)


def build_roundtrip(
    text: str,
    keywords: list[Keyword],
    aliases: AliasDictionary,
    grounding: Grounding | None,
) -> dict[str, object]:
    report = lexical_report_text(keywords, text, aliases=aliases)
    payload: dict[str, object] = report.as_dict()
    payload["extractor"] = "pdftotext"
    payload["text_chars"] = len(text)
    payload["mandatory_missing"] = [term.term for term in report.terms if term.filter_hazard]
    if grounding is None:
        payload["grounding"] = {"mode": "raw_markup"}
    else:
        payload["grounding"] = grounding.as_dict()
    return payload


def template_name(doc_type: str, language: str) -> str:
    if doc_type == "COVER_LETTER":
        return "cover_letter.typ"
    if language == "sk":
        return "resume_sk.typ"
    return "resume_en.typ"


def letter_date(settings: Settings) -> str:
    try:
        zone = ZoneInfo(settings.letter_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown letter timezone {settings.letter_timezone}") from exc
    return datetime.now(zone).date().isoformat()


def store_pdf(pdf: bytes, exports_dir: Path) -> Path:
    if not pdf.startswith(b"%PDF"):
        raise CompileError("compiler did not produce a PDF")
    exports_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(pdf).hexdigest()
    path = (exports_dir / f"{digest}.pdf").resolve()
    if not path.is_relative_to(exports_dir.resolve()):
        raise CompileError("export path escaped the exports directory")
    if not path.exists():
        path.write_bytes(pdf)
    return path


def resolve_export(pdf_path: str, settings: Settings) -> Path:
    path = Path(pdf_path).resolve()
    root = settings.exports_dir.resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".pdf" or not path.is_file():
        raise FileNotFoundError(pdf_path)
    return path


def profile_for_prompt(profile: CandidateProfile) -> dict[str, object]:
    data = profile.model_dump(mode="json")
    skills = data.get("skills")
    if isinstance(skills, list):
        data["skills"] = [
            item for item in skills if isinstance(item, dict) and item.get("confirmed") is True
        ]
    return data


def _resume_prompt(
    project: ProjectRecord,
    profile: CandidateProfile,
    evidence: str,
    language: str,
) -> str:
    return "\n".join(
        [
            f"Requested language: {_LANGUAGE_NAME[language]} ({language}).",
            "Fill the resume schema.",
            "Use only employers, credentials, and dates that appear in the evidence.",
            "Use only the confirmed skills.",
            _target_block(project),
            "Confirmed profile:",
            json.dumps(profile_for_prompt(profile), ensure_ascii=False, indent=2),
            "Evidence from the candidate's own documents:",
            evidence,
        ]
    )


def _letter_prompt(
    project: ProjectRecord,
    profile: CandidateProfile,
    evidence: str,
    language: str,
) -> str:
    return "\n".join(
        [
            f"Requested language: {_LANGUAGE_NAME[language]} ({language}).",
            "Fill the cover letter schema.",
            "Do not claim experience that is absent from the evidence.",
            _target_block(project),
            "Confirmed profile:",
            json.dumps(profile_for_prompt(profile), ensure_ascii=False, indent=2),
            "Evidence from the candidate's own documents:",
            evidence,
        ]
    )


def _target_block(project: ProjectRecord) -> str:
    requirements = project.requirements or {}
    mandatory = _strings(requirements.get("mandatory"))
    preferred = _strings(requirements.get("preferred"))
    offerings = _strings(project.offerings)
    return "\n".join(
        [
            "Target posting (not evidence the candidate did this work):",
            f"Title: {project.title or ''}",
            f"Company: {project.company or ''}",
            f"Contract: {project.contract_type or ''}",
            "Mandatory requirements:",
            _bullets(mandatory),
            "Preferred requirements:",
            _bullets(preferred),
            "Offerings:",
            _bullets(offerings),
        ]
    )


def _citation_text(knowledge: KnowledgeRepository, evaluation: EvaluationRecord) -> str:
    items = evaluation.classification.get("items")
    if not isinstance(items, list):
        return ""
    chunk_ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_ids = item.get("chunk_ids")
        if not isinstance(raw_ids, list):
            continue
        chunk_ids.extend(chunk_id for chunk_id in raw_ids if isinstance(chunk_id, str))
    blocks: list[str] = []
    for chunk in knowledge.chunks_by_ids(chunk_ids):
        header = chunk.section_header or "-"
        blocks.append(f"[{chunk.filename}] {header}\n{chunk.content}".strip())
    return "\n\n".join(blocks)


def _keywords_from_evaluation(evaluation: EvaluationRecord | None) -> list[Keyword]:
    if evaluation is None:
        return []
    raw_terms = evaluation.lexical_report.get("terms")
    if not isinstance(raw_terms, list):
        return []
    keywords: list[Keyword] = []
    for term in raw_terms:
        if not isinstance(term, dict):
            continue
        name = term.get("term")
        group = term.get("group")
        category = term.get("category")
        if not isinstance(name, str) or not name.strip():
            continue
        if isinstance(category, str) and category.strip():
            category_name = category.strip()
        else:
            category_name = "other"
        if isinstance(group, str) and group in _GROUPS:
            group_name = group
        else:
            group_name = "mandatory"
        keywords.append(
            Keyword(term=name.strip(), group=group_name, category=category_name)
        )
    return keywords


def _keywords_from_project(project: ProjectRecord) -> list[Keyword]:
    requirements = project.requirements or {}
    keywords: list[Keyword] = []
    for group, key in (("mandatory", "mandatory"), ("preferred", "preferred")):
        for item in _strings(requirements.get(key)):
            keywords.append(Keyword(term=item, group=group, category="other"))
    for item in _strings(project.offerings):
        keywords.append(Keyword(term=item, group="offering", category="other"))
    return keywords


def _evaluation(
    db: Database,
    project_id: str,
    evaluation_id: str | None,
) -> EvaluationRecord | None:
    if evaluation_id is None or not evaluation_id.strip():
        return None
    record = EvaluationRepository(db).get(evaluation_id)
    if record is None or record.project_id != project_id:
        raise ValueError("evaluation not found for this project")
    return record


def _bundle_name(project: ProjectRecord) -> str:
    company = (project.company or "").strip()
    title = (project.title or "").strip()
    label = " — ".join(part for part in (company, title) if part)
    return (label or "Active application")[:120]


def _export_filename(record: GeneratedDocumentRecord) -> str:
    kind = "cv" if record.doc_type == "CV" else "cover-letter"
    return f"{kind}-{record.language}-v{record.version}.pdf"


def _required_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"generate needs {key}")
    return value.strip()


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("bundle_ids must be a list")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("bundle_ids must be strings")
        items.append(item)
    return items


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _bullets(items: list[str]) -> str:
    if not items:
        return "- none"
    return "\n".join(f"- {item}" for item in items)
