from __future__ import annotations

import uuid

from app.clock import utc_now
from app.db.database import Database
from app.db.repositories.bundles import KnowledgeRepository
from app.llm.schemas.profile import CandidateProfile, SkillItem, SkillProposal
from app.services.ats_engine import (
    ClassifiedItem,
    Keyword,
    count_forms,
    lexical_report,
    load_aliases,
    match_percentage,
    reciprocal_rank_fusion,
    verify_citation,
)
from app.services.profile import merge_proposal


def test_alias_dictionary_expands_without_crossing_java_and_javascript() -> None:
    aliases = load_aliases()
    assert aliases.version == "aliases_v1"
    assert "Kubernetesom" in aliases.forms("K8s")
    assert "Angličtina" in aliases.forms("English")
    java = {form.casefold() for form in aliases.forms("Java")}
    javascript = {form.casefold() for form in aliases.forms("JavaScript")}
    assert "javascript" not in java
    assert "java" not in javascript


def test_word_boundary_counts_do_not_substring_match() -> None:
    text = "I write JavaScript, Java, and Google docs. C# too."
    assert count_forms(text, ["Java"]) == 1
    assert count_forms(text, ["JavaScript", "JS"]) == 1
    assert count_forms(text, ["Go", "Golang"]) == 0
    assert count_forms(text, ["C#", "CSharp"]) == 1
    assert count_forms("Angličtina na úrovni C1", ["English", "Anglictina"]) == 1


def test_lexical_report_uses_fts_aliases_and_filter_hazards(settings) -> None:
    db = Database(settings.db_path)
    try:
        db.migrate()
        repository = KnowledgeRepository(db)
        bundle = repository.insert_bundle(
            name="baseline",
            kind="historical_baseline",
            project_id=None,
        )
        _chunk(db, bundle.id, "Experience with Kubernetesom and Python services.")
        _chunk(db, bundle.id, "Daily work is JavaScript, not systems programming.")
        report = lexical_report(
            [
                Keyword(term="Kubernetes", group="mandatory", category="technology"),
                Keyword(term="Java", group="mandatory", category="technology"),
                Keyword(term="Go", group="mandatory", category="technology"),
            ],
            bundle_ids=[bundle.id],
            repository=repository,
        )
    finally:
        db.close()

    by_term = {term.term: term for term in report.terms}
    assert by_term["Kubernetes"].hit_count == 1
    assert by_term["Kubernetes"].filter_hazard is False
    assert by_term["Java"].hit_count == 0
    assert by_term["Java"].filter_hazard is True
    assert by_term["Go"].filter_hazard is True
    assert report.alias_version == "aliases_v1"


def test_citation_downgrade_and_score() -> None:
    allowed = {"chunk-1"}
    downgraded = verify_citation(
        term="Kubernetes",
        group="mandatory",
        category="technology",
        light="GREEN",
        rationale="Looks familiar.",
        chunk_ids=["missing"],
        allowed_ids=allowed,
    )
    assert downgraded.light == "YELLOW"
    assert downgraded.citation_downgraded is True
    assert downgraded.chunk_ids == []

    kept = verify_citation(
        term="Python",
        group="mandatory",
        category="technology",
        light="GREEN",
        rationale="Cited.",
        chunk_ids=["chunk-1", "other"],
        allowed_ids=allowed,
    )
    assert kept.light == "GREEN"
    assert kept.chunk_ids == ["chunk-1"]
    assert kept.dropped_chunk_ids == ["other"]

    score = match_percentage(
        [
            ClassifiedItem(
                "Kubernetes", "mandatory", "technology", "GREEN", "", ["chunk-1"], False, []
            ),
            ClassifiedItem("SQL", "preferred", "technology", "RED", "", [], False, []),
            ClassifiedItem("Home office", "offering", "other", "YELLOW", "", [], False, []),
        ],
        {"mandatory": 0.6, "preferred": 0.3, "offerings": 0.1},
    )
    assert score == 65.0
    only_mandatory = match_percentage(
        [
            ClassifiedItem("Kubernetes", "mandatory", "technology", "GREEN", "", [], False, []),
            ClassifiedItem("Go", "mandatory", "technology", "RED", "", [], False, []),
        ],
        {"mandatory": 0.6, "preferred": 0.3, "offerings": 0.1},
    )
    assert only_mandatory == 50.0


def test_reciprocal_rank_fusion_prefers_items_in_both_lists() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=1, limit=3)
    assert fused[0] == "b"
    assert set(fused) == {"a", "b", "c"}


def test_merge_proposal_does_not_overwrite_confirmed_skills() -> None:
    profile = CandidateProfile(
        skills=[SkillItem(skill="Python", years=6, confirmed=True, evidence_bundle_ids=["b1"])]
    )
    merged = merge_proposal(
        profile,
        SkillProposal(
            skills=[
                {"skill": "Python", "years": 1},
                {"skill": "Kubernetes", "years": 3},
            ],
            languages=[{"language": "English", "cefr": "C1"}],
        ),
        ["b2"],
    )
    by_name = {item.skill: item for item in merged.skills}
    assert by_name["Python"].confirmed is True
    assert by_name["Python"].years == 6
    assert by_name["Python"].evidence_bundle_ids == ["b1", "b2"]
    assert by_name["Kubernetes"].confirmed is False
    assert merged.languages[0].cefr == "C1"


def _chunk(db: Database, bundle_id: str, content: str) -> None:
    now = utc_now()
    document_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO bundle_documents (
            id, bundle_id, filename, doc_type, language, extracted_text, content_hash, created_at
        ) VALUES (?, ?, 'cv.md', 'CV', 'en', ?, ?, ?)
        """,
        (document_id, bundle_id, content, document_id, now),
    )
    db.execute(
        """
        INSERT INTO document_chunks (
            id, document_id, bundle_id, project_id, chunk_index, section_header,
            content, content_hash, token_count, language, doc_type, created_at
        ) VALUES (?, ?, ?, NULL, 0, 'Experience', ?, ?, ?, 'en', 'CV', ?)
        """,
        (
            str(uuid.uuid4()),
            document_id,
            bundle_id,
            content,
            document_id,
            len(content.split()),
            now,
        ),
    )
