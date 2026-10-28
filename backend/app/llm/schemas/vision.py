"""Structured transcript of an image-only document (ADR-006, ADR-018)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VisionTranscript(BaseModel):
    text: str = Field(min_length=1)
    language: str | None = None
