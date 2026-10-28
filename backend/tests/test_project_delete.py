from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.repositories.bundles import ChunkDraft, KnowledgeRepository
from app.db.repositories.documents import DocumentRepository
from app.db.repositories.evaluations import EvaluationRepository
from app.db.repositories.projects import ProjectRepository


def test_delete_project_keeps_shared_pdf_and_the_bundle(
    client: TestClient,
    auth_header: dict[str, str],
) -> None:
    db = client.app.state.db
    settings = client.app.state.settings
    projects = ProjectRepository(db)
    target = _project(projects, "Northwind")
    other = _project(projects, "Kept")
    bundle = KnowledgeRepository(db).insert_bundle(
        name="Active",
        kind="active_application",
        project_id=target.id,
    )
    KnowledgeRepository(db).add_document(
        bundle_id=bundle.id,
        filename="note.md",
        doc_type="OTHER",
        language="en",
        extracted_text="Kubernetes at Northwind",
        content_hash="hash-note",
        file_path=None,
        mime_type="text/markdown",
        project_id=target.id,
        chunks=[
            ChunkDraft(
                chunk_index=0,
                section_header="Work",
                content="Kubernetes at Northwind",
                content_hash="hash-chunk",
                token_count=3,
                language="en",
                doc_type="OTHER",
            )
        ],
    )
    EvaluationRepository(db).insert(
        project_id=target.id,
        lexical_report={"terms": []},
        classification={"items": []},
        match_percentage=10,
        score_weights={"mandatory": 0.6, "preferred": 0.3, "offerings": 0.1},
        model_name="test",
        prompt_version="classify_match_v1",
    )
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    shared = settings.exports_dir / "shared.pdf"
    owned = settings.exports_dir / "owned.pdf"
    outside = settings.data_dir / "outside.pdf"
    shared.write_bytes(b"%PDF-1.4 shared")
    owned.write_bytes(b"%PDF-1.4 owned")
    outside.write_bytes(b"%PDF-1.4 outside")
    documents = DocumentRepository(db)
    _document(documents, target.id, str(shared))
    _document(documents, other.id, str(shared))
    _document(documents, target.id, str(owned), language="sk")
    _document(documents, target.id, str(outside), language="en", doc_type="COVER_LETTER")

    missing = client.delete("/projects/missing", headers=auth_header)
    assert missing.status_code == 404

    removed = client.delete(f"/projects/{target.id}", headers=auth_header)
    assert removed.status_code == 200
    assert removed.json()["deleted"] == target.id
    assert client.get(f"/projects/{target.id}", headers=auth_header).status_code == 404
    listed = client.get("/projects", headers=auth_header).json()["projects"]
    assert [item["id"] for item in listed] == [other.id]

    kept_bundle = KnowledgeRepository(db).get_bundle(bundle.id)
    assert kept_bundle is not None
    assert kept_bundle.project_id is None
    chunk = db.query_one("SELECT project_id FROM document_chunks")
    assert chunk is not None
    assert chunk["project_id"] is None
    evaluation = db.query_one(
        "SELECT id FROM match_evaluations WHERE project_id = ?",
        (target.id,),
    )
    assert evaluation is None
    assert shared.is_file()
    assert not owned.exists()
    assert outside.is_file()
    assert client.get(f"/projects/{other.id}", headers=auth_header).status_code == 200


def _project(projects: ProjectRepository, company: str):
    return projects.insert(
        source_kind="pasted_text",
        source_ref=None,
        company=company,
        title="Platform engineer",
        contract_type="TPP",
        salary=None,
        language="en",
        requirements={"mandatory": ["Kubernetes"], "preferred": []},
        offerings=[],
        contacts=None,
        dates=None,
        raw_content_markdown="Kubernetes.",
        model_name="test",
        prompt_version="extract_job_v1",
    )


def _document(
    documents: DocumentRepository,
    project_id: str,
    pdf_path: str,
    *,
    language: str = "en",
    doc_type: str = "CV",
):
    return documents.insert(
        project_id=project_id,
        evaluation_id=None,
        doc_type=doc_type,
        language=language,
        template="resume_en.typ",
        content={"contact": {"name": "Ada"}},
        typst_source=None,
        pdf_path=pdf_path,
        ats_roundtrip_report=None,
        model_name="test",
        prompt_version="tailor_resume_v1",
    )
