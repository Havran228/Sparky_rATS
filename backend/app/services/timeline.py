"""Attach a Gmail message to a bundle as an EMAIL_THREAD document (ADR-011).

The owner chooses the message. This module only reads it back from Google and
stores the text. It never sends mail.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel

from app.db.database import Database
from app.db.repositories.bundles import BundleRecord, ChunkDraft, KnowledgeRepository
from app.llm.ollama_client import OllamaClient
from app.services.bundle_ingest import embed_document
from app.services.google_client import GoogleClient, GoogleError
from app.services.parser import ParsedSection, chunk_sections, detect_language
from app.vector_store import VectorStore

_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)


class AttachResult(BaseModel):
    document_id: str
    bundle_id: str
    embedded: bool
    duplicate: bool


def contact_emails(contacts: object) -> list[str]:
    found: list[str] = []
    if isinstance(contacts, list):
        for item in contacts:
            _collect(item, found)
    elif isinstance(contacts, dict):
        for value in contacts.values():
            _collect(value, found)
    elif isinstance(contacts, str):
        _collect(contacts, found)
    unique: list[str] = []
    for email in found:
        lowered = email.lower()
        if lowered not in unique:
            unique.append(lowered)
    return unique


async def attach_message(
    *,
    project_id: str,
    message_id: str,
    bundle_id: str | None,
    db: Database,
    google: GoogleClient,
    ollama: OllamaClient,
    vectors: VectorStore,
) -> AttachResult:
    subject, text, participants = await google.message_text(message_id)
    knowledge = KnowledgeRepository(db)
    bundle = _bundle(knowledge, project_id, bundle_id)
    header = "\n".join(participants)
    document_text = f"# {subject}\n\n{header}\n\n{text}".strip()
    digest = hashlib.sha256(document_text.encode("utf-8")).hexdigest()
    existing = knowledge.find_in_bundle_by_hash(bundle.id, digest)
    if existing is not None:
        return AttachResult(
            document_id=existing.id,
            bundle_id=bundle.id,
            embedded=False,
            duplicate=True,
        )
    sections = [ParsedSection(header=subject, text=document_text)]
    chunks = [
        ChunkDraft(
            chunk_index=chunk.chunk_index,
            section_header=chunk.section_header,
            content=chunk.content,
            content_hash=chunk.content_hash,
            token_count=chunk.token_count,
            language=detect_language(chunk.content),
            doc_type="EMAIL_THREAD",
        )
        for chunk in chunk_sections(sections)
    ]
    if not chunks:
        raise GoogleError("that message has no text to store")
    document = knowledge.add_document(
        bundle_id=bundle.id,
        filename=_filename(subject),
        doc_type="EMAIL_THREAD",
        language=detect_language(document_text),
        extracted_text=document_text,
        content_hash=digest,
        file_path=None,
        mime_type="text/plain",
        project_id=project_id,
        chunks=chunks,
    )
    embedded, _detail = await embed_document(document, project_id, ollama, vectors, knowledge)
    return AttachResult(
        document_id=document.id,
        bundle_id=bundle.id,
        embedded=embedded,
        duplicate=False,
    )


def _bundle(
    knowledge: KnowledgeRepository,
    project_id: str,
    bundle_id: str | None,
) -> BundleRecord:
    if bundle_id:
        bundle = knowledge.get_bundle(bundle_id)
        if bundle is None:
            raise GoogleError("bundle not found")
        return bundle
    existing = knowledge.active_bundle_for_project(project_id)
    if existing is not None:
        return existing
    return knowledge.insert_bundle(
        name="Application correspondence",
        kind="active_application",
        project_id=project_id,
    )


def _filename(subject: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", subject).strip("-") or "email"
    return f"{cleaned[:80]}.txt"


def _collect(value: object, found: list[str]) -> None:
    if isinstance(value, str):
        found.extend(_EMAIL.findall(value))
    elif isinstance(value, dict):
        for nested in value.values():
            _collect(nested, found)
    elif isinstance(value, list):
        for nested in value:
            _collect(nested, found)
