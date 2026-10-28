"""Knowledge bundles, documents, chunks, and the FTS5 mirror (ADR-003, ADR-008)."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from app.clock import utc_now
from app.db.database import Database

DOC_TYPES = frozenset(
    {"CV", "COVER_LETTER", "JOB_POSTING", "CORRESPONDENCE", "SCREENSHOT", "OTHER"}
)
BUNDLE_KINDS = frozenset({"historical_baseline", "active_application"})


@dataclass(frozen=True)
class BundleRecord:
    id: str
    name: str
    kind: str
    project_id: str | None
    created_at: str
    updated_at: str

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class DocumentRecord:
    id: str
    bundle_id: str
    filename: str
    doc_type: str
    language: str | None
    extracted_text: str | None
    content_hash: str | None
    file_path: str | None
    mime_type: str | None
    created_at: str
    chunk_count: int = 0

    def public_dict(self, *, include_text: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "bundle_id": self.bundle_id,
            "filename": self.filename,
            "doc_type": self.doc_type,
            "language": self.language,
            "content_hash": self.content_hash,
            "mime_type": self.mime_type,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
        }
        if include_text:
            payload["extracted_text"] = self.extracted_text
        return payload


@dataclass(frozen=True)
class ChunkDraft:
    chunk_index: int
    section_header: str
    content: str
    content_hash: str
    token_count: int
    language: str | None
    doc_type: str


@dataclass(frozen=True)
class ChunkHit:
    id: str
    document_id: str
    bundle_id: str
    content: str
    section_header: str
    filename: str
    doc_type: str
    language: str | None


class KnowledgeRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert_bundle(self, *, name: str, kind: str, project_id: str | None) -> BundleRecord:
        if kind not in BUNDLE_KINDS:
            raise ValueError(f"unknown bundle kind {kind}")
        now = utc_now()
        bundle_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO knowledge_bundles (id, name, kind, project_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (bundle_id, name.strip(), kind, project_id, now, now),
        )
        saved = self.get_bundle(bundle_id)
        if saved is None:
            raise RuntimeError("inserted bundle disappeared")
        return saved

    def get_bundle(self, bundle_id: str) -> BundleRecord | None:
        row = self._db.query_one("SELECT * FROM knowledge_bundles WHERE id = ?", (bundle_id,))
        if row is None:
            return None
        return _bundle(row)

    def find_bundle_by_name(self, name: str) -> BundleRecord | None:
        row = self._db.query_one(
            "SELECT * FROM knowledge_bundles WHERE name = ? ORDER BY created_at LIMIT 1",
            (name,),
        )
        if row is None:
            return None
        return _bundle(row)

    def list_bundles(self) -> list[BundleRecord]:
        rows = self._db.query(
            "SELECT * FROM knowledge_bundles ORDER BY created_at DESC, id DESC"
        )
        return [_bundle(row) for row in rows]

    def update_bundle(
        self,
        bundle_id: str,
        *,
        name: str | None = None,
        kind: str | None = None,
        project_id: str | None = None,
        set_project_id: bool = False,
    ) -> BundleRecord | None:
        current = self.get_bundle(bundle_id)
        if current is None:
            return None
        next_kind = kind or current.kind
        if next_kind not in BUNDLE_KINDS:
            raise ValueError(f"unknown bundle kind {next_kind}")
        next_project = project_id if set_project_id else current.project_id
        self._db.execute(
            """
            UPDATE knowledge_bundles
            SET name = ?, kind = ?, project_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (name.strip() if name else current.name, next_kind, next_project, utc_now(), bundle_id),
        )
        return self.get_bundle(bundle_id)

    def delete_bundle(self, bundle_id: str) -> list[str]:
        documents = self.list_documents(bundle_id)
        document_ids = [document.id for document in documents]
        self._db.execute("DELETE FROM knowledge_bundles WHERE id = ?", (bundle_id,))
        return document_ids

    def active_bundle_for_project(self, project_id: str) -> BundleRecord | None:
        row = self._db.query_one(
            """
            SELECT * FROM knowledge_bundles
            WHERE project_id = ? AND kind = 'active_application'
            ORDER BY created_at, id
            LIMIT 1
            """,
            (project_id,),
        )
        if row is None:
            return None
        return _bundle(row)

    def find_by_hash(self, content_hash: str) -> DocumentRecord | None:
        row = self._db.query_one(
            "SELECT * FROM bundle_documents WHERE content_hash = ? ORDER BY created_at LIMIT 1",
            (content_hash,),
        )
        if row is None:
            return None
        return self._with_count(_document(row))

    def find_in_bundle_by_hash(self, bundle_id: str, content_hash: str) -> DocumentRecord | None:
        row = self._db.query_one(
            """
            SELECT * FROM bundle_documents
            WHERE bundle_id = ? AND content_hash = ?
            ORDER BY created_at, id
            LIMIT 1
            """,
            (bundle_id, content_hash),
        )
        if row is None:
            return None
        return self._with_count(_document(row))

    def list_documents(self, bundle_id: str) -> list[DocumentRecord]:
        rows = self._db.query(
            """
            SELECT * FROM bundle_documents
            WHERE bundle_id = ?
            ORDER BY created_at, id
            """,
            (bundle_id,),
        )
        return [self._with_count(_document(row)) for row in rows]

    def get_document(self, document_id: str) -> DocumentRecord | None:
        row = self._db.query_one("SELECT * FROM bundle_documents WHERE id = ?", (document_id,))
        if row is None:
            return None
        return self._with_count(_document(row))

    def delete_document(self, document_id: str) -> bool:
        return self._db.execute("DELETE FROM bundle_documents WHERE id = ?", (document_id,)) > 0

    def add_document(
        self,
        *,
        bundle_id: str,
        filename: str,
        doc_type: str,
        language: str | None,
        extracted_text: str | None,
        content_hash: str,
        file_path: str | None,
        mime_type: str | None,
        project_id: str | None,
        chunks: list[ChunkDraft],
    ) -> DocumentRecord:
        if doc_type not in DOC_TYPES:
            raise ValueError(f"unknown doc_type {doc_type}")
        now = utc_now()
        document_id = str(uuid.uuid4())
        with self._db.immediate() as conn:
            conn.execute(
                """
                INSERT INTO bundle_documents (
                    id, bundle_id, filename, doc_type, language, extracted_text,
                    content_hash, file_path, mime_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    bundle_id,
                    filename,
                    doc_type,
                    language,
                    extracted_text,
                    content_hash,
                    file_path,
                    mime_type,
                    now,
                ),
            )
            conn.executemany(
                """
                INSERT INTO document_chunks (
                    id, document_id, bundle_id, project_id, chunk_index, section_header,
                    content, content_hash, token_count, language, doc_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid.uuid4()),
                        document_id,
                        bundle_id,
                        project_id,
                        chunk.chunk_index,
                        chunk.section_header,
                        chunk.content,
                        chunk.content_hash,
                        chunk.token_count,
                        chunk.language,
                        chunk.doc_type,
                        now,
                    )
                    for chunk in chunks
                ],
            )
        saved = self.get_document(document_id)
        if saved is None:
            raise RuntimeError("inserted document disappeared")
        return saved

    def document_chunks(self, document_id: str) -> list[ChunkHit]:
        rows = self._db.query(
            """
            SELECT c.id, c.document_id, c.bundle_id, c.content, c.section_header,
                   c.doc_type, c.language, d.filename
            FROM document_chunks AS c
            JOIN bundle_documents AS d ON d.id = c.document_id
            WHERE c.document_id = ?
            ORDER BY c.chunk_index
            """,
            (document_id,),
        )
        return [_hit(row) for row in rows]

    def chunks_by_ids(self, chunk_ids: list[str]) -> list[ChunkHit]:
        if not chunk_ids:
            return []
        marks = ",".join("?" for _ in chunk_ids)
        rows = self._db.query(
            f"""
            SELECT c.id, c.document_id, c.bundle_id, c.content, c.section_header,
                   c.doc_type, c.language, d.filename
            FROM document_chunks AS c
            JOIN bundle_documents AS d ON d.id = c.document_id
            WHERE c.id IN ({marks})
            """,
            tuple(chunk_ids),
        )
        return [_hit(row) for row in rows]

    def search_fts(self, match: str, bundle_ids: list[str]) -> list[ChunkHit]:
        if not match.strip() or not bundle_ids:
            return []
        marks = ",".join("?" for _ in bundle_ids)
        try:
            rows = self._db.query(
                f"""
                SELECT c.id, c.document_id, c.bundle_id, c.content, c.section_header,
                       c.doc_type, c.language, d.filename
                FROM document_chunks_fts
                JOIN document_chunks AS c ON c.rowid = document_chunks_fts.rowid
                JOIN bundle_documents AS d ON d.id = c.document_id
                WHERE document_chunks_fts MATCH ?
                  AND c.bundle_id IN ({marks})
                """,
                (match, *bundle_ids),
            )
        except sqlite3.Error:
            return []
        return [_hit(row) for row in rows]

    def bundle_chunk_excerpt(self, bundle_ids: list[str], *, limit_chars: int) -> str:
        if not bundle_ids:
            return ""
        marks = ",".join("?" for _ in bundle_ids)
        rows = self._db.query(
            f"""
            SELECT c.section_header, c.content, d.filename
            FROM document_chunks AS c
            JOIN bundle_documents AS d ON d.id = c.document_id
            WHERE c.bundle_id IN ({marks})
            ORDER BY d.created_at, c.chunk_index
            """,
            tuple(bundle_ids),
        )
        parts: list[str] = []
        used = 0
        for row in rows:
            header = "" if row["section_header"] is None else str(row["section_header"])
            filename = str(row["filename"])
            content = str(row["content"])
            block = f"[{filename}] {header}\n{content}".strip()
            if used + len(block) > limit_chars:
                remaining = limit_chars - used
                if remaining > 80:
                    parts.append(block[:remaining])
                break
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)

    def _with_count(self, document: DocumentRecord) -> DocumentRecord:
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM document_chunks WHERE document_id = ?",
            (document.id,),
        )
        count = 0 if row is None else int(row["n"])
        return DocumentRecord(
            id=document.id,
            bundle_id=document.bundle_id,
            filename=document.filename,
            doc_type=document.doc_type,
            language=document.language,
            extracted_text=document.extracted_text,
            content_hash=document.content_hash,
            file_path=document.file_path,
            mime_type=document.mime_type,
            created_at=document.created_at,
            chunk_count=count,
        )


def _bundle(row: object) -> BundleRecord:
    return BundleRecord(
        id=str(row["id"]),  # type: ignore[index]
        name=str(row["name"]),  # type: ignore[index]
        kind=str(row["kind"]),  # type: ignore[index]
        project_id=None if row["project_id"] is None else str(row["project_id"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
        updated_at=str(row["updated_at"]),  # type: ignore[index]
    )


def _document(row: object) -> DocumentRecord:
    return DocumentRecord(
        id=str(row["id"]),  # type: ignore[index]
        bundle_id=str(row["bundle_id"]),  # type: ignore[index]
        filename=str(row["filename"]),  # type: ignore[index]
        doc_type=str(row["doc_type"]),  # type: ignore[index]
        language=None if row["language"] is None else str(row["language"]),  # type: ignore[index]
        extracted_text=(
            None if row["extracted_text"] is None else str(row["extracted_text"])  # type: ignore[index]
        ),
        content_hash=None if row["content_hash"] is None else str(row["content_hash"]),  # type: ignore[index]
        file_path=None if row["file_path"] is None else str(row["file_path"]),  # type: ignore[index]
        mime_type=None if row["mime_type"] is None else str(row["mime_type"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
    )


def _hit(row: object) -> ChunkHit:
    return ChunkHit(
        id=str(row["id"]),  # type: ignore[index]
        document_id=str(row["document_id"]),  # type: ignore[index]
        bundle_id=str(row["bundle_id"]),  # type: ignore[index]
        content=str(row["content"]),  # type: ignore[index]
        section_header="" if row["section_header"] is None else str(row["section_header"]),  # type: ignore[index]
        filename=str(row["filename"]),  # type: ignore[index]
        doc_type=str(row["doc_type"]),  # type: ignore[index]
        language=None if row["language"] is None else str(row["language"]),  # type: ignore[index]
    )

