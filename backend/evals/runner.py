"""Run posting/bundle cases and report colour agreement (ADR-020).

The default classifier is a lexical proxy: GREEN when the alias-aware word-boundary
match hits, otherwise RED, citing those chunks. A live heavy model can replace it
later; the report always names the model and the classification prompt version.
Citation checks and the deterministic score are the same functions the API uses.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Protocol

from app.db.database import Database
from app.db.repositories.bundles import ChunkDraft, KnowledgeRepository
from app.services.ats_engine import (
    Keyword,
    lexical_report,
    load_aliases,
    match_percentage,
    verify_citation,
)
from app.services.evaluator import CLASSIFY_PROMPT
from app.services.parser import chunk_sections, detect_language, sections_from_markdown

CASES_DIR = Path(__file__).resolve().parent / "cases"


class ColourClassifier(Protocol):
    def __call__(self, term: str, *, hit_count: int, chunk_ids: list[str]) -> tuple[str, list[str]]:
        """Return a light and the chunk ids it wants to cite."""


def run_cases(
    *,
    model_name: str = "offline-lexical",
    classifier: ColourClassifier | None = None,
    cases_dir: Path | None = None,
) -> dict[str, object]:
    chosen = classifier or lexical_proxy
    root = cases_dir or CASES_DIR
    case_reports: list[dict[str, object]] = []
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            case_reports.append(_run_case(payload, model_name=model_name, classifier=chosen))
    agreement = 1.0
    if case_reports:
        agreement = round(
            sum(float(item["agreement"]) for item in case_reports) / len(case_reports),
            4,
        )
    return {
        "model_name": model_name,
        "prompt_version": CLASSIFY_PROMPT,
        "agreement": agreement,
        "cases": case_reports,
    }


def lexical_proxy(term: str, *, hit_count: int, chunk_ids: list[str]) -> tuple[str, list[str]]:
    del term
    if hit_count > 0:
        return "GREEN", list(chunk_ids)
    return "RED", []


def _run_case(
    payload: dict[str, object],
    *,
    model_name: str,
    classifier: ColourClassifier,
) -> dict[str, object]:
    case_id = str(payload.get("id") or "case")
    keywords = _keywords(payload)
    expected = payload.get("expected")
    expected_map = expected if isinstance(expected, dict) else {}
    hazards_raw = payload.get("expected_hazards")
    expected_hazards: list[str] = []
    if isinstance(hazards_raw, list):
        expected_hazards = [item for item in hazards_raw if isinstance(item, str)]
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "harness.db")
        try:
            db.migrate()
            repository = KnowledgeRepository(db)
            bundle = repository.insert_bundle(
                name=f"case-{case_id}",
                kind="historical_baseline",
                project_id=None,
            )
            documents = payload.get("documents")
            if isinstance(documents, list):
                for index, document in enumerate(documents):
                    if isinstance(document, dict):
                        _store_document(repository, bundle.id, document, index)
            lexical = lexical_report(
                keywords,
                bundle_ids=[bundle.id],
                repository=repository,
                aliases=load_aliases(),
            )
        finally:
            db.close()
    actual: dict[str, str] = {}
    items = []
    for term in lexical.terms:
        light, cited = classifier(term.term, hit_count=term.hit_count, chunk_ids=term.chunk_ids)
        verified = verify_citation(
            term=term.term,
            group=term.group,
            category=term.category,
            light=light,
            rationale="harness",
            chunk_ids=cited,
            allowed_ids=set(term.chunk_ids),
        )
        actual[term.term] = verified.light
        items.append(verified)
    matched = 0
    compared = 0
    for term, light in expected_map.items():
        if not isinstance(term, str) or not isinstance(light, str):
            continue
        compared += 1
        if actual.get(term) == light:
            matched += 1
    hazards = [term.term for term in lexical.terms if term.filter_hazard]
    return {
        "id": case_id,
        "model_name": model_name,
        "prompt_version": CLASSIFY_PROMPT,
        "agreement": 1.0 if compared == 0 else round(matched / compared, 4),
        "expected": {str(key): value for key, value in expected_map.items()},
        "actual": actual,
        "filter_hazards": hazards,
        "expected_hazards": expected_hazards,
        "match_percentage": match_percentage(
            items,
            {"mandatory": 0.6, "preferred": 0.3, "offerings": 0.1},
        ),
    }


def _keywords(payload: dict[str, object]) -> list[Keyword]:
    keywords: list[Keyword] = []
    for group, category in (
        ("mandatory", "technology"),
        ("preferred", "technology"),
        ("offerings", "other"),
    ):
        values = payload.get(group)
        if not isinstance(values, list):
            continue
        keyword_group = "offering" if group == "offerings" else group
        for value in values:
            if isinstance(value, str) and value.strip():
                keywords.append(Keyword(term=value.strip(), group=keyword_group, category=category))
    return keywords


def _store_document(
    repository: KnowledgeRepository,
    bundle_id: str,
    document: dict[str, object],
    index: int,
) -> None:
    text = document.get("text")
    if not isinstance(text, str):
        return
    filename = document.get("filename")
    doc_type = document.get("doc_type")
    sections = sections_from_markdown(text)
    chunks = chunk_sections(sections, target_tokens=400)
    language = detect_language(text)
    repository.add_document(
        bundle_id=bundle_id,
        filename=filename if isinstance(filename, str) else f"doc-{index}.md",
        doc_type=doc_type if isinstance(doc_type, str) else "CV",
        language=language,
        extracted_text=text,
        content_hash=f"case-{bundle_id}-{index}",
        file_path=None,
        mime_type="text/markdown",
        project_id=None,
        chunks=[
            ChunkDraft(
                chunk_index=chunk.chunk_index,
                section_header=chunk.section_header,
                content=chunk.content,
                content_hash=chunk.content_hash,
                token_count=chunk.token_count,
                language=language,
                doc_type=doc_type if isinstance(doc_type, str) else "CV",
            )
            for chunk in chunks
        ],
    )
