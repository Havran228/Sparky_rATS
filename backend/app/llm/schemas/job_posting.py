"""Structured extract of a job posting (ADR-006, ADR-018)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ContractType = Literal["TPP", "SZCO", "DOHODA", "OTHER", "UNKNOWN"]
SalaryPeriod = Literal["month", "hour", "year", "day", "unknown"]
SalaryBasis = Literal["gross", "net", "unknown"]


class SalaryExtract(BaseModel):
    minimum: str | None = None
    maximum: str | None = None
    currency: str = "EUR"
    period: SalaryPeriod = "unknown"
    basis: SalaryBasis = "unknown"
    raw: str = ""

    @field_validator("minimum", "maximum", mode="before")
    @classmethod
    def stringify_amount(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        return str(value).strip()


class JobPostingExtract(BaseModel):
    title: str | None = None
    company: str | None = None
    contract_type: ContractType = "UNKNOWN"
    language: str | None = None
    location: str | None = None
    salary: SalaryExtract = Field(default_factory=SalaryExtract)
    mandatory_requirements: list[str] = Field(default_factory=list)
    preferred_requirements: list[str] = Field(default_factory=list)
    offerings: list[str] = Field(default_factory=list)
    contacts: list[str] = Field(default_factory=list)
    date_posted: str | None = None
    valid_through: str | None = None
