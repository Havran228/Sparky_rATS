"""Versioned generated documents (ADR-015)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from app.clock import utc_now
from app.db.database import Database


@dataclass(frozen=True)
class GeneratedDocumentRecord:
    id: str
    project_id: str
    evaluation_id: str | None
    doc_type: str
    language: str
    template: str
    version: int
    content: dict[str, object]
    typst_source: str | None
    pdf_path: str | None
    ats_roundtrip_report: dict[str, object] | None
    model_name: str | None
    prompt_version: str | None
    is_submitted: bool
    created_at: str

    def public_dict(self, *, include_source: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "project_id": self.project_id,
            "evaluation_id": self.evaluation_id,
            "doc_type": self.doc_type,
            "language": self.language,
            "template": self.template,
            "version": self.version,
            "content": self.content,
            "pdf_path": self.pdf_path,
            "ats_roundtrip_report": self.ats_roundtrip_report,
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "is_submitted": self.is_submitted,
            "created_at": self.created_at,
        }
        if include_source:
            payload["typst_source"] = self.typst_source
        return payload


class DocumentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(
        self,
        *,
        project_id: str,
        evaluation_id: str | None,
        doc_type: str,
        language: str,
        template: str,
        content: dict[str, object],
        typst_source: str | None,
        pdf_path: str | None,
        ats_roundtrip_report: dict[str, object] | None,
        model_name: str | None,
        prompt_version: str | None,
    ) -> GeneratedDocumentRecord:
        document_id = str(uuid.uuid4())
        now = utc_now()
        with self._db.immediate() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(version), 0) AS version
                FROM generated_documents
                WHERE project_id = ? AND doc_type = ? AND language = ?
                """,
                (project_id, doc_type, language),
            ).fetchone()
            version = 1 if row is None else int(row["version"]) + 1
            conn.execute(
                """
                INSERT INTO generated_documents (
                    id, project_id, evaluation_id, doc_type, language, template, version,
                    content_json, typst_source, pdf_path, ats_roundtrip_report,
                    model_name, prompt_version, is_submitted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    document_id,
                    project_id,
                    evaluation_id,
                    doc_type,
                    language,
                    template,
                    version,
                    json.dumps(content, ensure_ascii=False),
                    typst_source,
                    pdf_path,
                    None
                    if ats_roundtrip_report is None
                    else json.dumps(ats_roundtrip_report, ensure_ascii=False),
                    model_name,
                    prompt_version,
                    now,
                ),
            )
        saved = self.get(document_id)
        if saved is None:
            raise RuntimeError("inserted document disappeared")
        return saved

    def get(self, document_id: str) -> GeneratedDocumentRecord | None:
        row = self._db.query_one(
            "SELECT * FROM generated_documents WHERE id = ?",
            (document_id,),
        )
        if row is None:
            return None
        return _row(row)

    def list_for_project(self, project_id: str) -> list[GeneratedDocumentRecord]:
        rows = self._db.query(
            """
            SELECT * FROM generated_documents
            WHERE project_id = ?
            ORDER BY created_at DESC, version DESC, id DESC
            """,
            (project_id,),
        )
        return [_row(row) for row in rows]

    def set_submitted(self, document_id: str, submitted: bool) -> GeneratedDocumentRecord | None:
        self._db.execute(
            "UPDATE generated_documents SET is_submitted = ? WHERE id = ?",
            (1 if submitted else 0, document_id),
        )
        return self.get(document_id)


def _object(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, str) or not raw:
        return None
    loaded = json.loads(raw)
    if isinstance(loaded, dict):
        return loaded
    return None


def _row(row: object) -> GeneratedDocumentRecord:
    content = _object(row["content_json"])  # type: ignore[index]
    report = _object(row["ats_roundtrip_report"])  # type: ignore[index]
    return GeneratedDocumentRecord(
        id=str(row["id"]),  # type: ignore[index]
        project_id=str(row["project_id"]),  # type: ignore[index]
        evaluation_id=None if row["evaluation_id"] is None else str(row["evaluation_id"]),  # type: ignore[index]
        doc_type=str(row["doc_type"]),  # type: ignore[index]
        language=str(row["language"]),  # type: ignore[index]
        template=str(row["template"]),  # type: ignore[index]
        version=int(row["version"]),  # type: ignore[index]
        content={} if content is None else content,
        typst_source=None if row["typst_source"] is None else str(row["typst_source"]),  # type: ignore[index]
        pdf_path=None if row["pdf_path"] is None else str(row["pdf_path"]),  # type: ignore[index]
        ats_roundtrip_report=report,
        model_name=None if row["model_name"] is None else str(row["model_name"]),  # type: ignore[index]
        prompt_version=(
            None if row["prompt_version"] is None else str(row["prompt_version"])  # type: ignore[index]
        ),
        is_submitted=bool(row["is_submitted"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
    )
