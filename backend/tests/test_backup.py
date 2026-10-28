from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.config import Settings
from app.vector_store import VECTOR_SIZE, VectorPoint


def _point(document_id: str) -> VectorPoint:
    vector = [0.0] * VECTOR_SIZE
    vector[0] = 1.0
    return VectorPoint(
        id=str(uuid.uuid4()),
        vector=vector,
        bundle_id="bundle-a",
        project_id=None,
        document_id=document_id,
        chunk_index=0,
        doc_type="CV",
        language="en",
        section_header="Experience",
        content_hash="hash-backup",
        token_count=4,
    )


def test_backup_routes_require_a_token(client: TestClient) -> None:
    assert client.post("/maintenance/backup", json={"retention_days": 14}).status_code == 401
    assert client.get("/maintenance/backups").status_code == 401
    assert (
        client.post(
            "/maintenance/restore",
            json={"name": "20261028T031500Z", "replace": True},
        ).status_code
        == 401
    )


def test_backup_snapshot_retention_and_restore(
    client: TestClient,
    settings: Settings,
    auth_header: dict[str, str],
    monkeypatch,
) -> None:
    fixed = datetime(2026, 10, 28, 3, 15, tzinfo=UTC)
    monkeypatch.setattr("app.services.backup.current_time", lambda: fixed)
    old = settings.backups_dir / "20000101T000000Z"
    old.mkdir(parents=True)
    (old / "note.txt").write_text("old", encoding="utf-8")
    (settings.backups_dir / "notes").mkdir()
    (settings.backups_dir / "29990101T000000Z").mkdir()
    marker = "SUPER-SECRET-MARKER-VALUE"
    settings.credentials_dir.mkdir(parents=True, exist_ok=True)
    (settings.credentials_dir / "api_token").write_text(marker, encoding="utf-8")
    (settings.credentials_dir / "master.key").write_text(marker + "\n", encoding="utf-8")

    document_id = str(uuid.uuid4())
    point = _point(document_id)
    vectors = client.app.state.vectors
    vectors.upsert([point])
    client.app.state.db.execute(
        "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
        (9999, "backup-marker", "2026-01-01T00:00:00Z"),
    )

    rejected = client.post(
        "/maintenance/backup",
        headers=auth_header,
        json={"retention_days": 0},
    )
    assert rejected.status_code == 422
    saved = client.post(
        "/maintenance/backup",
        headers=auth_header,
        json={"retention_days": 14},
    )
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert body["master_key_included"] is False
    assert body["points"] == 1
    assert body["name"] == "20261028T031500Z"
    assert "20000101T000000Z" in body["removed"]
    dest = settings.data_dir / body["path"]
    manifest = (dest / "MANIFEST.txt").read_text(encoding="utf-8")
    assert "master_key: NOT INCLUDED" in manifest
    assert "mode: api" in manifest
    for path in dest.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert marker not in text
            assert path.name not in {"master.key", "api_token", "mcp_token"}
    copied = sqlite3.connect(dest / "sparky_rats.db")
    try:
        row = copied.execute(
            "SELECT name FROM schema_version WHERE version = 9999"
        ).fetchone()
    finally:
        copied.close()
    assert row is not None and row[0] == "backup-marker"
    assert not old.exists()
    assert (settings.backups_dir / "notes").is_dir()
    assert (settings.backups_dir / "29990101T000000Z").is_dir()

    again = client.post(
        "/maintenance/backup",
        headers=auth_header,
        json={"retention_days": 14},
    )
    assert again.status_code == 409

    vectors.delete_by_document(document_id)
    assert vectors.search(point.vector, limit=5) == []
    refused = client.post(
        "/maintenance/restore",
        headers=auth_header,
        json={"name": body["name"], "replace": False},
    )
    assert refused.status_code == 422
    restored = client.post(
        "/maintenance/restore",
        headers=auth_header,
        json={"name": body["name"], "replace": True},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["points"] == 1
    hits = vectors.search(point.vector, limit=5)
    assert hits and hits[0].payload["document_id"] == document_id

    missing = client.post(
        "/maintenance/restore",
        headers=auth_header,
        json={"name": "20260101T000000Z", "replace": True},
    )
    assert missing.status_code == 404
    invalid = client.post(
        "/maintenance/restore",
        headers=auth_header,
        json={"name": "../secrets", "replace": True},
    )
    assert invalid.status_code == 422
    listed = client.get("/maintenance/backups", headers=auth_header)
    assert listed.status_code == 200
    names = [item["name"] for item in listed.json()["backups"]]
    assert body["name"] in names
    assert "notes" not in names
