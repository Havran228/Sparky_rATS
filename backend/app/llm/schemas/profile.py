"""Candidate profile singleton (ADR-016) and the proposal the fast role returns."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Identity(BaseModel):
    name: str | None = None


class Contact(BaseModel):
    email: str | None = None
    phone: str | None = None
    location: str | None = None


class LanguageItem(BaseModel):
    language: str = Field(min_length=1)
    cefr: str | None = None

    @field_validator("language")
    @classmethod
    def strip_language(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("language is empty")
        return cleaned


class SkillItem(BaseModel):
    skill: str = Field(min_length=1)
    years: float | None = None
    last_used: str | None = None
    evidence_bundle_ids: list[str] = Field(default_factory=list)
    confirmed: bool = False

    @field_validator("skill")
    @classmethod
    def strip_skill(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("skill is empty")
        return cleaned

    @field_validator("years", mode="before")
    @classmethod
    def years_number(cls, value: object) -> float | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError("years must be a number")
        number = float(value)
        if number < 0:
            raise ValueError("years must be non-negative")
        return number


class ProfilePreferences(BaseModel):
    claim_nczd: bool = True
    preferred_contract_types: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    identity: Identity = Field(default_factory=Identity)
    contact: Contact = Field(default_factory=Contact)
    links: list[str] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)
    skills: list[SkillItem] = Field(default_factory=list)
    preferences: ProfilePreferences = Field(default_factory=ProfilePreferences)


class ProposedSkill(BaseModel):
    skill: str = Field(min_length=1)
    years: float | None = None
    last_used: str | None = None

    @field_validator("skill")
    @classmethod
    def strip_skill(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("skill is empty")
        return cleaned

    @field_validator("years", mode="before")
    @classmethod
    def years_number(cls, value: object) -> float | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError("years must be a number")
        return float(value)


class SkillProposal(BaseModel):
    skills: list[ProposedSkill] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)
