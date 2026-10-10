"""Slovak payroll for an employment contract, parameterized by tax year (ADR-007).

Every rate, cap, allowance, and bracket is loaded from ``tax_params``. This module
only supplies decimal rounding and the arithmetic.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from app.db.database import Database

CENT = Decimal("0.01")
MONTHS = Decimal(12)


class UnknownTaxYear(LookupError):
    def __init__(self, year: int) -> None:
        self.year = year
        super().__init__(f"no tax parameters for {year}")


class RateLine(BaseModel):
    code: str
    rate: Decimal


class TaxBracket(BaseModel):
    up_to: Decimal | None = None
    rate: Decimal


class TaxParams(BaseModel):
    employee_social: list[RateLine]
    employee_social_cap: Decimal
    employee_health_rate: Decimal
    employer_social: list[RateLine]
    employer_social_cap: Decimal
    employer_health_rate: Decimal
    nczd_monthly: Decimal
    nczd_full_until_annual: Decimal
    nczd_phaseout_constant: Decimal
    nczd_phaseout_divisor: Decimal
    brackets: list[TaxBracket]


class LevyLine(BaseModel):
    code: str
    amount: Decimal


class BracketLine(BaseModel):
    rate: Decimal
    portion: Decimal
    amount: Decimal


class SalaryBreakdown(BaseModel):
    modeled: bool
    contract_type: str
    tax_year: int
    message: str | None = None
    gross: Decimal | None = None
    employee_social: list[LevyLine] = Field(default_factory=list)
    employee_social_total: Decimal | None = None
    employee_health: Decimal | None = None
    employee_total: Decimal | None = None
    tax_base: Decimal | None = None
    nczd_applied: Decimal | None = None
    taxable: Decimal | None = None
    tax_brackets: list[BracketLine] = Field(default_factory=list)
    tax: Decimal | None = None
    net: Decimal | None = None
    employer_social: list[LevyLine] = Field(default_factory=list)
    employer_social_total: Decimal | None = None
    employer_health: Decimal | None = None
    employer_total: Decimal | None = None
    total_cost: Decimal | None = None


_NOT_MODELED = {
    "SZCO": (
        "SZČO is not modeled in v1. Levies depend on prior-year income, "
        "minimum bases, and the expense regime."
    ),
    "DOHODA": "Dohoda is not modeled in v1. The published figure is left unconverted.",
}


def load_tax_params(db: Database, year: int) -> TaxParams:
    row = db.query_one("SELECT params_json FROM tax_params WHERE tax_year = ?", (year,))
    if row is None:
        raise UnknownTaxYear(year)
    raw = row["params_json"]
    if not isinstance(raw, str):
        raise UnknownTaxYear(year)
    return TaxParams.model_validate_json(raw)


def list_tax_years(db: Database) -> list[int]:
    rows = db.query("SELECT tax_year FROM tax_params ORDER BY tax_year")
    return [int(row["tax_year"]) for row in rows]


def normalize_contract(value: str) -> str:
    compact = value.strip().upper().replace("Č", "C").replace(" ", "").replace("_", "")
    if compact in {"TPP", "EMPLOYMENT", "FULLTIME", "HPP"}:
        return "TPP"
    if compact in {"SZCO", "ZIVNOST", "CONTRACTOR", "TRADE"}:
        return "SZCO"
    if compact in {"DOHODA", "AGREEMENT"}:
        return "DOHODA"
    return compact or "TPP"


def calculate(
    gross: Decimal,
    params: TaxParams,
    *,
    tax_year: int,
    claim_nczd: bool,
    contract_type: str = "TPP",
) -> SalaryBreakdown:
    contract = normalize_contract(contract_type)
    if contract in _NOT_MODELED:
        return SalaryBreakdown(
            modeled=False,
            contract_type=contract,
            tax_year=tax_year,
            message=_NOT_MODELED[contract],
            gross=_money(gross),
        )
    if contract != "TPP":
        return SalaryBreakdown(
            modeled=False,
            contract_type=contract,
            tax_year=tax_year,
            message="Only an employment contract (TPP) is modeled in v1.",
            gross=_money(gross),
        )
    if gross < 0:
        raise ValueError("gross must be zero or positive")

    employee_social = _levies(gross, params.employee_social, params.employee_social_cap)
    social_total = _sum(employee_social)
    health = _money(gross * params.employee_health_rate)
    employee_total = _money(social_total + health)
    tax_base = _money(gross - employee_total)
    nczd = monthly_nczd(tax_base, params, claim=claim_nczd)
    taxable = tax_base - nczd
    if taxable < 0:
        taxable = Decimal("0.00")
    else:
        taxable = _money(taxable)
    bracket_lines, tax = tax_on_base(taxable, params.brackets)
    net = _money(gross - employee_total - tax)

    employer_social = _levies(gross, params.employer_social, params.employer_social_cap)
    employer_social_total = _sum(employer_social)
    employer_health = _money(gross * params.employer_health_rate)
    employer_total = _money(employer_social_total + employer_health)
    return SalaryBreakdown(
        modeled=True,
        contract_type="TPP",
        tax_year=tax_year,
        gross=_money(gross),
        employee_social=employee_social,
        employee_social_total=social_total,
        employee_health=health,
        employee_total=employee_total,
        tax_base=tax_base,
        nczd_applied=nczd,
        taxable=taxable,
        tax_brackets=bracket_lines,
        tax=tax,
        net=net,
        employer_social=employer_social,
        employer_social_total=employer_social_total,
        employer_health=employer_health,
        employer_total=employer_total,
        total_cost=_money(gross + employer_total),
    )


def monthly_nczd(tax_base: Decimal, params: TaxParams, *, claim: bool) -> Decimal:
    if not claim or tax_base <= 0:
        return Decimal("0.00")
    annual = tax_base * MONTHS
    if annual <= params.nczd_full_until_annual:
        return _money(params.nczd_monthly)
    raw = params.nczd_phaseout_constant - (annual / params.nczd_phaseout_divisor)
    if raw <= 0:
        return Decimal("0.00")
    return _money(raw / MONTHS)


def tax_on_base(taxable: Decimal, brackets: list[TaxBracket]) -> tuple[list[BracketLine], Decimal]:
    """Tax on a monthly taxable base. Each bracket is rounded to cents, then summed."""
    if taxable < 0:
        raise ValueError("taxable base must be zero or positive")
    covered = Decimal("0.00")
    lines: list[BracketLine] = []
    total = Decimal("0.00")
    for bracket in brackets:
        if taxable <= covered:
            break
        cap = taxable if bracket.up_to is None else min(taxable, bracket.up_to)
        portion = _money(cap - covered)
        if portion <= 0:
            continue
        amount = _money(portion * bracket.rate)
        lines.append(BracketLine(rate=bracket.rate, portion=portion, amount=amount))
        total = _money(total + amount)
        covered = cap
        if bracket.up_to is None or taxable <= bracket.up_to:
            break
    return lines, total


def _levies(gross: Decimal, lines: list[RateLine], cap: Decimal) -> list[LevyLine]:
    base = gross if gross <= cap else cap
    if base < 0:
        base = Decimal("0.00")
    return [LevyLine(code=line.code, amount=_money(base * line.rate)) for line in lines]


def _sum(lines: list[LevyLine]) -> Decimal:
    total = Decimal("0.00")
    for line in lines:
        total = _money(total + line.amount)
    return total


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
