from __future__ import annotations

import uuid

import pytest

from app.vector_store import VectorPoint, VectorStore, pid_alive, release_stale_lock


def _point(document_id: str, bundle_id: str, index: int) -> VectorPoint:
    vector = [0.0] * 1024
    vector[index] = 1.0
    return VectorPoint(
        id=str(uuid.uuid4()),
        vector=vector,
        bundle_id=bundle_id,
        project_id=None,
        document_id=document_id,
        chunk_index=index,
        doc_type="CV",
        language="en",
        section_header="Experience",
        content_hash=f"hash-{index}",
        token_count=12,
    )


def test_upsert_search_and_delete(tmp_path) -> None:
    store = VectorStore.open(tmp_path / "qdrant")
    try:
        bundle = "bundle-a"
        other = "bundle-b"
        document = "doc-1"
        store.upsert(
            [
                _point(document, bundle, 1),
                _point(document, bundle, 2),
                _point("doc-2", other, 3),
            ]
        )
        query = [0.0] * 1024
        query[1] = 1.0
        hits = store.search(query, limit=2, bundle_ids=[bundle])
        assert hits
        assert hits[0].payload["document_id"] == document
        assert all(hit.payload["bundle_id"] == bundle for hit in hits)
        store.delete_by_document(document)
        after = store.search(query, limit=5, bundle_ids=[bundle])
        assert after == []
        assert store.healthy() is True
    finally:
        store.close()


def test_reopen_after_close_and_reject_a_second_client(tmp_path) -> None:
    path = tmp_path / "qdrant"
    first = VectorStore.open(path)
    with pytest.raises(RuntimeError, match="already accessed"):
        VectorStore.open(path)
    first.close()
    second = VectorStore.open(path)
    second.close()


def test_dead_pid_lock_is_removed(tmp_path) -> None:
    storage = tmp_path / "qdrant"
    storage.mkdir()
    lock = storage / ".lock"
    lock.write_text("tmp lock file", encoding="utf-8")
    dead = 2**30
    assert pid_alive(dead) is False

    import app.vector_store as vector_store

    original = vector_store.flock_holder_pid
    vector_store.flock_holder_pid = lambda path: dead  # type: ignore[method-assign]
    try:
        release_stale_lock(storage)
    finally:
        vector_store.flock_holder_pid = original  # type: ignore[method-assign]
    assert not lock.exists()


def test_vector_dimension_is_enforced(tmp_path) -> None:
    store = VectorStore.open(tmp_path / "qdrant")
    try:
        point = _point("doc", "bundle", 1)
        bad = VectorPoint(
            id=point.id,
            vector=[0.0, 1.0],
            bundle_id=point.bundle_id,
            project_id=None,
            document_id=point.document_id,
            chunk_index=1,
            doc_type="CV",
            language="en",
            section_header="",
            content_hash="x",
            token_count=1,
        )
        with pytest.raises(ValueError, match="1024"):
            store.upsert([bad])
    finally:
        store.close()
