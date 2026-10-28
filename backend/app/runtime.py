"""Shared process objects for routes, tools, and the MCP server."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.db.database import Database
from app.jobs import JobRunner
from app.llm.ollama_client import OllamaClient
from app.secrets_store import SecretsStore
from app.services.google_client import GoogleClient
from app.services.staging import StagingBuffer
from app.vector_store import VectorStore


@dataclass
class Runtime:
    settings: Settings
    db: Database
    ollama: OllamaClient
    vectors: VectorStore
    jobs: JobRunner
    secrets: SecretsStore
    staging: StagingBuffer
    google: GoogleClient
