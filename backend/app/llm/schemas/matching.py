"""Keyword extraction and traffic-light classification (ADR-008, ADR-018)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

KeywordCategory = Literal[
    "technology",
    "framework",
    "certification",
    "degree",
    "language",
    "soft",
    "other",
]
KeywordGroup = Literal["mandatory", "preferred", "offering"]
TrafficLight = Literal["GREEN", "YELLOW", "RED"]


class RequirementKeyword(BaseModel):
    term: str = Field(min_length=1)
    category: KeywordCategory = "other"
    group: KeywordGroup
    mandatory: bool = False

    @field_validator("term")
    @classmethod
    def strip_term(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("term is empty")
        return cleaned


class RequirementKeywords(BaseModel):
    keywords: list[RequirementKeyword] = Field(default_factory=list)

    @field_validator("keywords")
    @classmethod
    def align_mandatory(cls, value: list[RequirementKeyword]) -> list[RequirementKeyword]:
        aligned: list[RequirementKeyword] = []
        for item in value:
            aligned.append(item.model_copy(update={"mandatory": item.group == "mandatory"}))
        return aligned


class JudgementItem(BaseModel):
    term: str = Field(min_length=1)
    light: TrafficLight
    rationale: str = ""
    chunk_ids: list[str] = Field(default_factory=list)

    @field_validator("light", mode="before")
    @classmethod
    def upper_light(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("term")
    @classmethod
    def strip_term(cls, value: str) -> str:
        return " ".join(value.split())


class JudgementBatch(BaseModel):
    items: list[JudgementItem] = Field(default_factory=list)
