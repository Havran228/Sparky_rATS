from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.salary_text import parse_amount, parse_salary_text


@pytest.mark.parametrize(
    ("raw", "minimum", "maximum", "period", "basis"),
    [
        ("od 2 500 EUR", "2500", None, "month", "unknown"),
        ("2 500 - 3 500 € brutto", "2500", "3500", "month", "gross"),
        ("15 €/hod", "15", None, "hour", "unknown"),
        ("od 3 000 netto", "3000", None, "month", "net"),
        ("2.500,50 EUR", "2500.50", None, "month", "unknown"),
        ("from 4,200 gross", "4200", None, "month", "gross"),
    ],
)
def test_salary_phrases(
    raw: str,
    minimum: str,
    maximum: str | None,
    period: str,
    basis: str,
) -> None:
    parsed = parse_salary_text(raw)
    assert parsed is not None
    assert parsed.minimum == minimum
    assert parsed.maximum == maximum
    assert parsed.period == period
    assert parsed.basis == basis


def test_amount_grouping() -> None:
    assert parse_amount("2 500") == Decimal("2500")
    assert parse_amount("2.500,50") == Decimal("2500.50")
    assert parse_amount("1,234.50") == Decimal("1234.50")
    assert parse_salary_text("dohodou") is None
