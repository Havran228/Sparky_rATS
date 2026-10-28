"""Resume and cover-letter models the heavy role fills (ADR-009, ADR-018)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

DocumentLanguage = Literal["en", "sk"]
GeneratedDocType = Literal["CV", "COVER_LETTER"]


def _clean(value: str) -> str:
    return " ".join(value.split())


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected a string")
    cleaned = _clean(value)
    return cleaned or None


def _clean_list(value: list[str]) -> list[str]:
    cleaned = [_clean(item) for item in value if isinstance(item, str)]
    return [item for item in cleaned if item]


class ResumeContact(BaseModel):
    name: str = Field(min_length=1)
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("name is empty")
        return cleaned

    @field_validator("email", "phone", "location", mode="before")
    @classmethod
    def strip_optional(cls, value: object) -> str | None:
        return _clean_optional(value)

    @field_validator("links")
    @classmethod
    def strip_links(cls, value: list[str]) -> list[str]:
        return _clean_list(value)


class ExperienceEntry(BaseModel):
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    start: str | None = None
    end: str | None = None
    location: str | None = None
    bullets: list[str] = Field(default_factory=list)

    @field_validator("company", "title")
    @classmethod
    def strip_required(cls, value: str) -> str:
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("value is empty")
        return cleaned

    @field_validator("start", "end", "location", mode="before")
    @classmethod
    def strip_optional(cls, value: object) -> str | None:
        return _clean_optional(value)

    @field_validator("bullets")
    @classmethod
    def strip_bullets(cls, value: list[str]) -> list[str]:
        return _clean_list(value)


class SkillGroup(BaseModel):
    name: str = Field(min_length=1)
    items: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("name is empty")
        return cleaned

    @field_validator("items")
    @classmethod
    def strip_items(cls, value: list[str]) -> list[str]:
        return _clean_list(value)


class EducationEntry(BaseModel):
    institution: str = Field(min_length=1)
    credential: str | None = None
    start: str | None = None
    end: str | None = None
    detail: str | None = None

    @field_validator("institution")
    @classmethod
    def strip_institution(cls, value: str) -> str:
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("institution is empty")
        return cleaned

    @field_validator("credential", "start", "end", "detail", mode="before")
    @classmethod
    def strip_optional(cls, value: object) -> str | None:
        return _clean_optional(value)


class ResumeLanguage(BaseModel):
    language: str = Field(min_length=1)
    level: str | None = None

    @field_validator("language")
    @classmethod
    def strip_language(cls, value: str) -> str:
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("language is empty")
        return cleaned

    @field_validator("level", mode="before")
    @classmethod
    def strip_level(cls, value: object) -> str | None:
        return _clean_optional(value)


class ResumeModel(BaseModel):
    contact: ResumeContact
    summary: str = ""
    experience: list[ExperienceEntry] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    languages: list[ResumeLanguage] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def strip_summary(cls, value: str) -> str:
        return _clean(value)


class CoverRecipient(BaseModel):
    name: str | None = None
    company: str | None = None
    address: str | None = None

    @field_validator("name", "company", "address", mode="before")
    @classmethod
    def strip_optional(cls, value: object) -> str | None:
        return _clean_optional(value)


class CoverLetterModel(BaseModel):
    contact: ResumeContact
    date: str | None = None
    recipient: CoverRecipient = Field(default_factory=CoverRecipient)
    role: str | None = None
    salutation: str = Field(min_length=1)
    paragraphs: list[str] = Field(min_length=1)
    closing: str = Field(min_length=1)
    signature: str = Field(min_length=1)

    @field_validator("date", "role", mode="before")
    @classmethod
    def strip_optional(cls, value: object) -> str | None:
        return _clean_optional(value)

    @field_validator("salutation", "closing", "signature")
    @classmethod
    def strip_required(cls, value: str) -> str:
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("value is empty")
        return cleaned

    @field_validator("paragraphs")
    @classmethod
    def strip_paragraphs(cls, value: list[str]) -> list[str]:
        cleaned = _clean_list(value)
        if not cleaned:
            raise ValueError("paragraphs are empty")
        return cleaned
