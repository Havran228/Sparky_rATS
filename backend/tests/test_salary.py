from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.db.database import Database
from app.services.salary_calc import (
    UnknownTaxYear,
    calculate,
    list_tax_years,
    load_tax_params,
    tax_on_base,
)


def test_tax_engine_does_not_embed_rates() -> None:
    source_path = Path(__file__).parents[1] / "app/services/salary_calc.py"
    source = source_path.read_text(encoding="utf-8")
    for token in ("497.23", "479.48", "16764", "15730", "1328.63", "0.094", "0.014"):
        assert token not in source


def test_official_2026_tax_on_base_6000_without_nczd(settings) -> None:
    params = _params(settings, 2026)
    lines, tax = tax_on_base(Decimal("6000"), params.brackets)
    assert tax == Decimal("1328.63")
    assert [line.amount for line in lines] == [
        Decimal("696.40"),
        Decimal("340.96"),
        Decimal("291.27"),
    ]


def test_official_2025_progression_example(settings) -> None:
    params = _params(settings, 2025)
    _lines, tax = tax_on_base(Decimal("4500"), params.brackets)
    assert tax == Decimal("882.79")


def test_published_net_wages(settings) -> None:
    params_2026 = _params(settings, 2026)
    low = calculate(
        Decimal("1200"),
        params_2026,
        tax_year=2026,
        claim_nczd=True,
    )
    assert low.net == Decimal("926.51")
    assert low.nczd_applied == Decimal("497.23")

    mid = calculate(
        Decimal("2500"),
        params_2026,
        tax_year=2026,
        claim_nczd=True,
    )
    assert mid.employee_social_total == Decimal("235.00")
    assert mid.employee_health == Decimal("125.00")
    assert mid.tax == Decimal("312.13")
    assert mid.net == Decimal("1827.87")
    assert mid.total_cost == Decimal("3405.00")

    params_2025 = _params(settings, 2025)
    previous = calculate(
        Decimal("1200"),
        params_2025,
        tax_year=2025,
        claim_nczd=True,
    )
    assert previous.net == Decimal("932.85")
    assert previous.employee_health == Decimal("48.00")


def test_phaseout_and_high_salary(settings) -> None:
    params = _params(settings, 2026)
    phased = calculate(Decimal("4000"), params, tax_year=2026, claim_nczd=True)
    assert phased.tax_base == Decimal("3424.00")
    assert phased.nczd_applied == Decimal("80.43")
    assert phased.tax == Decimal("635.28")
    assert phased.net == Decimal("2788.72")

    high = calculate(Decimal("7000"), params, tax_year=2026, claim_nczd=True)
    assert high.nczd_applied == Decimal("0.00")
    assert high.tax == Decimal("1326.23")
    assert high.net == Decimal("4665.77")

    no_allowance = calculate(Decimal("6000"), params, tax_year=2026, claim_nczd=False)
    assert no_allowance.nczd_applied == Decimal("0.00")
    assert no_allowance.tax == Decimal("1069.43")
    assert no_allowance.net == Decimal("4066.57")


def test_other_contracts_are_not_modeled(settings) -> None:
    params = _params(settings, 2026)
    trade = calculate(
        Decimal("3000"),
        params,
        tax_year=2026,
        claim_nczd=True,
        contract_type="SZČO",
    )
    agreement = calculate(
        Decimal("800"),
        params,
        tax_year=2026,
        claim_nczd=True,
        contract_type="dohoda",
    )
    assert trade.modeled is False
    assert trade.gross == Decimal("3000.00")
    assert "SZČO" in (trade.message or "")
    assert agreement.modeled is False
    assert "Dohoda" in (agreement.message or "")


def test_unknown_tax_year(settings) -> None:
    db = _db(settings)
    assert list_tax_years(db) == [2025, 2026]
    with pytest.raises(UnknownTaxYear):
        load_tax_params(db, 2024)
    db.close()


def _params(settings, year: int):  # type: ignore[no-untyped-def]
    db = _db(settings)
    try:
        return load_tax_params(db, year)
    finally:
        db.close()


def _db(settings) -> Database:  # type: ignore[no-untyped-def]
    db = Database(settings.db_path)
    db.migrate()
    return db
