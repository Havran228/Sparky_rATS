"""Single embedded Qdrant client (ADR-004).

No other module should import qdrant_client. The surface is upsert,
delete-by-document, and filtered search so sqlite-vec can replace this file.
"""

from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

log = logging.getLogger(__name__)

COLLECTION = "sparky_rats_vectors"
VECTOR_SIZE = 1024
_KEYWORD_FIELDS = ("bundle_id", "project_id", "doc_type", "language")


@dataclass(frozen=True)
class VectorPoint:
    id: str
    vector: list[float]
    bundle_id: str
    project_id: str | None
    document_id: str
    chunk_index: int
    doc_type: str
    language: str
    section_header: str
    content_hash: str
    token_count: int


@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    payload: dict[str, object]


class VectorStore:
    def __init__(self, client: QdrantClient) -> None:
        self._client = client

    @classmethod
    def open(cls, path: Path) -> VectorStore:
        path.mkdir(parents=True, exist_ok=True)
        release_stale_lock(path)
        client = QdrantClient(path=str(path))
        store = cls(client)
        store._ensure_collection()
        return store

    def close(self) -> None:
        self._client.close()

    def healthy(self) -> bool:
        try:
            return bool(self._client.collection_exists(COLLECTION))
        except Exception:
            log.exception("qdrant health check failed")
            return False

    def upsert(self, points: list[VectorPoint]) -> None:
        structs: list[PointStruct] = []
        for point in points:
            if len(point.vector) != VECTOR_SIZE:
                raise ValueError(
                    f"vector {point.id} has {len(point.vector)} dims, expected {VECTOR_SIZE}"
                )
            structs.append(
                PointStruct(
                    id=point.id,
                    vector=point.vector,
                    payload={
                        "bundle_id": point.bundle_id,
                        "project_id": point.project_id,
                        "document_id": point.document_id,
                        "chunk_index": point.chunk_index,
                        "doc_type": point.doc_type,
                        "language": point.language,
                        "section_header": point.section_header,
                        "content_hash": point.content_hash,
                        "token_count": point.token_count,
                    },
                )
            )
        if structs:
            self._client.upsert(collection_name=COLLECTION, points=structs)

    def delete_by_document(self, document_id: str) -> None:
        self._client.delete(
            collection_name=COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            ),
        )

    def search(
        self,
        vector: list[float],
        *,
        limit: int = 10,
        bundle_ids: list[str] | None = None,
    ) -> list[VectorHit]:
        if len(vector) != VECTOR_SIZE:
            raise ValueError(f"query vector has {len(vector)} dims, expected {VECTOR_SIZE}")
        query_filter = None
        if bundle_ids is not None:
            query_filter = Filter(
                must=[FieldCondition(key="bundle_id", match=MatchAny(any=bundle_ids))]
            )
        response = self._client.query_points(
            collection_name=COLLECTION,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        hits: list[VectorHit] = []
        for point in response.points:
            payload = dict(point.payload or {})
            hits.append(
                VectorHit(id=str(point.id), score=float(point.score or 0.0), payload=payload)
            )
        return hits

    def _ensure_collection(self) -> None:
        if not self._client.collection_exists(COLLECTION):
            self._client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
        for field in _KEYWORD_FIELDS:
            try:
                # Local Qdrant ignores payload indexes and warns. The call stays
                # so a server-mode swap keeps the ADR-004 keyword indexes.
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message="Payload indexes have no effect in the local Qdrant.*",
                    )
                    self._client.create_payload_index(
                        collection_name=COLLECTION,
                        field_name=field,
                        field_schema=PayloadSchemaType.KEYWORD,
                    )
            except Exception as exc:
                message = str(exc).lower()
                if "already exists" in message or "already" in message:
                    continue
                raise


def release_stale_lock(storage: Path) -> None:
    """Drop a Qdrant flock whose holder pid is dead.

    Qdrant local mode takes an exclusive flock on ``.lock``. The kernel releases
    that lock when the holder exits, so a crash normally leaves an unlocked
    file. If ``/proc/locks`` still names a pid that is not alive, the file is
    removed so the next open can proceed (ADR-004).
    """
    lock_path = storage / ".lock"
    if not lock_path.exists():
        return
    holder = flock_holder_pid(lock_path)
    if holder is None or pid_alive(holder):
        return
    log.warning("removing stale qdrant lock %s held by dead pid %s", lock_path, holder)
    lock_path.unlink(missing_ok=True)


def flock_holder_pid(lock_path: Path) -> int | None:
    try:
        stat = lock_path.stat()
        text = Path("/proc/locks").read_text(encoding="utf-8")
    except OSError:
        return None
    major = os.major(stat.st_dev)
    minor = os.minor(stat.st_dev)
    inode = stat.st_ino
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            pid = int(parts[4])
            device, inode_text = parts[5].split(":")
            dev_major_text, dev_minor_text = device.split(":")
        except ValueError:
            continue
        if int(dev_major_text, 16) != major or int(dev_minor_text, 16) != minor:
            continue
        if int(inode_text) == inode:
            return pid
    return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
