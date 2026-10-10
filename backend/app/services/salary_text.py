"""Deterministic salary phrases from a posting (ADR-006)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.llm.schemas.job_posting import SalaryBasis, SalaryExtract, SalaryPeriod

_RANGE = re.compile(
    r"(\d[\d\s\u00a0.,]*)\s*(?:€|eur)?\s*[-–—]\s*(\d[\d\s\u00a0.,]*)",
    re.IGNORECASE,
)
_FROM = re.compile(
    r"(?:od|from|min\.?)\s*(\d[\d\s\u00a0.,]*)",
    re.IGNORECASE,
)
_AMOUNT = re.compile(r"(\d[\d\s\u00a0.,]*)")
_HOURLY = re.compile(r"/\s*(?:hod\.?|hodinu|h\b|hour)", re.IGNORECASE)
_YEARLY = re.compile(r"/\s*(?:rok|year|ročne)|ročne|p\.?\s*a\.?", re.IGNORECASE)
_DAILY = re.compile(r"/\s*(?:deň|den|day)", re.IGNORECASE)


def parse_salary_text(raw: str) -> SalaryExtract | None:
    text = raw.strip()
    if not text:
        return None
    basis = _basis(text)
    period = _period(text)
    currency = "EUR"
    match = _RANGE.search(text)
    if match:
        minimum = _amount(match.group(1))
        maximum = _amount(match.group(2))
        if minimum is None and maximum is None:
            return None
        return SalaryExtract(
            minimum=_text(minimum),
            maximum=_text(maximum),
            currency=currency,
            period=period,
            basis=basis,
            raw=text,
        )
    from_match = _FROM.search(text)
    if from_match:
        minimum = _amount(from_match.group(1))
        if minimum is None:
            return None
        return SalaryExtract(
            minimum=_text(minimum),
            maximum=None,
            currency=currency,
            period=period,
            basis=basis,
            raw=text,
        )
    amount_match = _AMOUNT.search(text)
    if amount_match is None:
        return None
    only = _amount(amount_match.group(1))
    if only is None:
        return None
    return SalaryExtract(
        minimum=_text(only),
        maximum=None,
        currency=currency,
        period=period,
        basis=basis,
        raw=text,
    )


def parse_amount(raw: str) -> Decimal | None:
    return _amount(raw)


def _basis(text: str) -> SalaryBasis:
    lowered = text.lower()
    if re.search(r"netto|nettó|net\b|čist", lowered):
        return "net"
    if re.search(r"brutto|hrub|gross", lowered):
        return "gross"
    return "unknown"


def _period(text: str) -> SalaryPeriod:
    if _HOURLY.search(text):
        return "hour"
    if _YEARLY.search(text):
        return "year"
    if _DAILY.search(text):
        return "day"
    return "month"


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _amount(raw: str) -> Decimal | None:
    text = raw.strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = _split_mark(text, ",")
    elif "." in text:
        text = _split_mark(text, ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _split_mark(text: str, mark: str) -> str:
    parts = text.split(mark)
    if len(parts) > 1 and len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
        return "".join(parts)
    if mark == ",":
        return text.replace(",", ".")
    return text
