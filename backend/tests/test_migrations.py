from __future__ import annotations

import uuid

from app.clock import utc_now
from app.db.database import Database


def test_migrations_are_idempotent_and_wal(settings) -> None:
    db = Database(settings.db_path)
    try:
        first = db.migrate()
        second = db.migrate()
        mode = db.query_one("PRAGMA journal_mode")
        foreign_keys = db.query_one("PRAGMA foreign_keys")
        names = {row["name"] for row in db.query("SELECT name FROM sqlite_master")}
    finally:
        db.close()

    assert first == [1]
    assert second == []
    assert mode is not None and str(mode[0]).lower() == "wal"
    assert foreign_keys is not None and int(foreign_keys[0]) == 1
    expected = {
        "schema_version",
        "projects",
        "project_stages",
        "knowledge_bundles",
        "bundle_documents",
        "document_chunks",
        "document_chunks_fts",
        "candidate_profile",
        "match_evaluations",
        "generated_documents",
        "project_chats",
        "jobs",
        "tax_params",
        "system_credentials",
    }
    assert expected <= names


def test_fts_folds_diacritics_and_follows_deletes(settings) -> None:
    db = Database(settings.db_path)
    try:
        db.migrate()
        now = utc_now()
        bundle_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        chunk_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO knowledge_bundles (id, name, kind, project_id, created_at, updated_at)
            VALUES (?, 'baseline', 'historical_baseline', NULL, ?, ?)
            """,
            (bundle_id, now, now),
        )
        db.execute(
            """
            INSERT INTO bundle_documents (
                id, bundle_id, filename, doc_type, language, created_at
            ) VALUES (?, ?, 'cv.txt', 'CV', 'sk', ?)
            """,
            (document_id, bundle_id, now),
        )
        db.execute(
            """
            INSERT INTO document_chunks (
                id, document_id, bundle_id, project_id, chunk_index, section_header,
                content, content_hash, token_count, language, doc_type, created_at
            ) VALUES (?, ?, ?, NULL, 0, 'Skúsenosti', ?, 'hash', 3, 'sk', 'CV', ?)
            """,
            (chunk_id, document_id, bundle_id, "Spoločnosť Kubernetesom", now),
        )
        folded = db.query(
            """
            SELECT c.id AS chunk_id
            FROM document_chunks_fts
            JOIN document_chunks AS c ON c.rowid = document_chunks_fts.rowid
            WHERE document_chunks_fts MATCH ?
            """,
            ("spolocnost",),
        )
        exact = db.query(
            """
            SELECT c.id AS chunk_id
            FROM document_chunks_fts
            JOIN document_chunks AS c ON c.rowid = document_chunks_fts.rowid
            WHERE document_chunks_fts MATCH ?
            """,
            ("Kubernetesom",),
        )
        db.execute("DELETE FROM document_chunks WHERE id = ?", (chunk_id,))
        after_delete = db.query(
            "SELECT rowid FROM document_chunks_fts WHERE document_chunks_fts MATCH ?",
            ("Kubernetesom",),
        )
    finally:
        db.close()

    assert [row["chunk_id"] for row in folded] == [chunk_id]
    assert [row["chunk_id"] for row in exact] == [chunk_id]
    assert after_delete == []


def test_project_delete_cascades_to_stages(settings) -> None:
    db = Database(settings.db_path)
    try:
        db.migrate()
        now = utc_now()
        project_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO projects (id, source_kind, created_at, updated_at)
            VALUES (?, 'profesia_url', ?, ?)
            """,
            (project_id, now, now),
        )
        db.execute(
            """
            INSERT INTO project_stages (
                id, project_id, name, status, position, created_at, updated_at
            ) VALUES (?, ?, 'Screening', 'ACTIVE', 0, ?, ?)
            """,
            (str(uuid.uuid4()), project_id, now, now),
        )
        db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        remaining = db.query("SELECT id FROM project_stages")
    finally:
        db.close()
    assert remaining == []
