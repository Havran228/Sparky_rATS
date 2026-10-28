"""Store an uploaded or inbox file as chunks, FTS rows, and vectors (ADR-005, ADR-006)."""

from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.db.database import Database
from app.db.repositories.bundles import (
    DOC_TYPES,
    BundleRecord,
    ChunkDraft,
    DocumentRecord,
    KnowledgeRepository,
)
from app.jobs import JobContext, JobRunner
from app.llm.ollama_client import ModelRole, OllamaClient, OllamaError
from app.llm.prompts import load_prompt
from app.llm.schemas.vision import VisionTranscript
from app.services.parser import (
    ParsedSection,
    chunk_sections,
    detect_language,
    extract_sections,
    joined_text,
    sections_from_markdown,
)
from app.vector_store import VECTOR_SIZE, VectorPoint, VectorStore

log = logging.getLogger(__name__)

VISION_PROMPT = "vision_transcript_v1"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".tif", ".tiff", ".gif", ".bmp"}


def register_bundle_handler(
    runner: JobRunner,
    db: Database,
    ollama: OllamaClient,
    vectors: VectorStore,
    settings: Settings,
) -> None:
    async def handle(ctx: JobContext) -> dict[str, object]:
        raw_path = ctx.payload.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("ingest_file needs a path")
        filename = ctx.payload.get("filename")
        doc_type = ctx.payload.get("doc_type")
        bundle_id = ctx.payload.get("bundle_id")
        mime_type = ctx.payload.get("mime_type")
        return await ingest_stored_file(
            Path(raw_path),
            db=db,
            ollama=ollama,
            vectors=vectors,
            settings=settings,
            filename=filename if isinstance(filename, str) else None,
            doc_type=doc_type if isinstance(doc_type, str) else None,
            bundle_id=bundle_id if isinstance(bundle_id, str) else None,
            mime_type=mime_type if isinstance(mime_type, str) else None,
            progress=ctx.progress,
        )

    runner.register("ingest_file", handle, llm=True)


async def ingest_stored_file(
    path: Path,
    *,
    db: Database,
    ollama: OllamaClient,
    vectors: VectorStore,
    settings: Settings,
    filename: str | None,
    doc_type: str | None,
    bundle_id: str | None,
    mime_type: str | None,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, object]:
    def report(value: int, message: str) -> None:
        if progress is not None:
            progress(value, message)

    resolved = _inside_data(path, settings.data_dir)
    report(10, "hashing")
    data = resolved.read_bytes()
    if not data:
        raise RuntimeError("file is empty")
    digest = hashlib.sha256(data).hexdigest()
    repository = KnowledgeRepository(db)
    existing = repository.find_by_hash(digest)
    if existing is not None:
        if existing.file_path != str(resolved):
            resolved.unlink(missing_ok=True)
        report(100, "duplicate")
        return {
            "document_id": existing.id,
            "bundle_id": existing.bundle_id,
            "duplicate": True,
            "chunk_count": existing.chunk_count,
            "embedded": False,
        }
    bundle = _bundle_for(repository, bundle_id, settings.inbox_bundle_name)
    name = _safe_name(filename or resolved.name)
    guessed = mime_type or mimetypes.guess_type(name)[0]
    chosen_type = doc_type if doc_type in DOC_TYPES else _default_doc_type(resolved, guessed)
    report(30, "parsing")
    sections, used_vision = await _sections(
        resolved,
        data,
        guessed,
        ollama,
        min_chars=settings.vision_min_chars,
    )
    language = detect_language(joined_text(sections))
    if used_vision and not language:
        language = "unknown"
    chunks = chunk_sections(sections, target_tokens=settings.chunk_target_tokens)
    drafts = [
        ChunkDraft(
            chunk_index=chunk.chunk_index,
            section_header=chunk.section_header,
            content=chunk.content,
            content_hash=chunk.content_hash,
            token_count=chunk.token_count,
            language=language,
            doc_type=chosen_type,
        )
        for chunk in chunks
    ]
    report(60, "saving")
    document = repository.add_document(
        bundle_id=bundle.id,
        filename=name,
        doc_type=chosen_type,
        language=language,
        extracted_text=joined_text(sections) or None,
        content_hash=digest,
        file_path=str(resolved),
        mime_type=guessed,
        project_id=bundle.project_id,
        chunks=drafts,
    )
    report(80, "embedding")
    embedded, embed_error = await _embed_document(
        document,
        bundle.project_id,
        ollama,
        vectors,
        repository,
    )
    report(100, "stored")
    result: dict[str, object] = {
        "document_id": document.id,
        "bundle_id": document.bundle_id,
        "duplicate": False,
        "chunk_count": document.chunk_count,
        "embedded": embedded,
        "used_vision": used_vision,
    }
    if embed_error:
        result["embed_error"] = embed_error
    return result


async def _sections(
    path: Path,
    data: bytes,
    mime_type: str | None,
    ollama: OllamaClient,
    *,
    min_chars: int,
) -> tuple[list[ParsedSection], bool]:
    if _is_image(path, mime_type):
        transcript = await _transcribe(data, ollama)
        return sections_from_markdown(transcript.text), True
    sections = extract_sections(path, mime_type)
    if len(joined_text(sections)) >= min_chars:
        return sections, False
    if not sections:
        raise RuntimeError(f"no text extracted from {path.name}")
    return sections, False


async def _transcribe(data: bytes, ollama: OllamaClient) -> VisionTranscript:
    system, _version = load_prompt(VISION_PROMPT)
    encoded = base64.b64encode(data).decode("ascii")
    return await ollama.structured(
        ModelRole.vision,
        "Transcribe this document image.",
        VisionTranscript,
        system=system,
        images=[encoded],
    )


async def _embed_document(
    document: DocumentRecord,
    project_id: str | None,
    ollama: OllamaClient,
    vectors: VectorStore,
    repository: KnowledgeRepository,
) -> tuple[bool, str | None]:
    chunks = repository.document_chunks(document.id)
    if not chunks:
        return False, None
    vectors_out: list[list[float]] = []
    texts = [chunk.content for chunk in chunks]
    try:
        for start in range(0, len(texts), 16):
            vectors_out.extend(await ollama.embed(texts[start : start + 16]))
    except OllamaError as exc:
        log.info("embedding skipped for %s: %s", document.id, exc)
        return False, str(exc)
    if len(vectors_out) != len(chunks) or any(len(vector) != VECTOR_SIZE for vector in vectors_out):
        return False, "embedding dimension did not match the collection"
    points = [
        VectorPoint(
            id=chunk.id,
            vector=vector,
            bundle_id=chunk.bundle_id,
            project_id=project_id,
            document_id=chunk.document_id,
            chunk_index=index,
            doc_type=chunk.doc_type,
            language=chunk.language or "",
            section_header=chunk.section_header,
            content_hash=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
            token_count=len(chunk.content.split()),
        )
        for index, (chunk, vector) in enumerate(zip(chunks, vectors_out, strict=True))
    ]
    vectors.upsert(points)
    return True, None


def _bundle_for(
    repository: KnowledgeRepository,
    bundle_id: str | None,
    inbox_name: str,
) -> BundleRecord:
    if bundle_id:
        bundle = repository.get_bundle(bundle_id)
        if bundle is None:
            raise ValueError(f"unknown bundle {bundle_id}")
        return bundle
    existing = repository.find_bundle_by_name(inbox_name)
    if existing is not None:
        return existing
    return repository.insert_bundle(name=inbox_name, kind="historical_baseline", project_id=None)


def _inside_data(path: Path, data_dir: Path) -> Path:
    resolved = path.resolve()
    root = data_dir.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("file is outside the data directory")
    if not resolved.is_file():
        raise ValueError("file is missing")
    return resolved


def _is_image(path: Path, mime_type: str | None) -> bool:
    if mime_type and mime_type.split(";", 1)[0].strip().lower().startswith("image/"):
        return True
    return path.suffix.lower() in _IMAGE_SUFFIXES


def _default_doc_type(path: Path, mime_type: str | None) -> str:
    if _is_image(path, mime_type):
        return "SCREENSHOT"
    return "OTHER"


def _safe_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in Path(name).name)
    cleaned = cleaned.strip("._")
    return cleaned or "upload"
