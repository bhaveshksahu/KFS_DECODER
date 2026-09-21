"""Deterministic analysis layer — no LLM calls.

Converts a KFSExtraction into:
  - ChargeItem list for apr.py
  - APR variants (all three + EAR)
  - Summary metrics
  - Per-category charge breakdown
  - Computed amortisation schedule

Public API
----------
build_charge_items(extraction, include_stamp_duty) -> list[ChargeItem]
compute_apr_variants(extraction, include_stamp_duty) -> APRVariants
build_summary(extraction, include_stamp_duty)        -> AnalysisSummary
compute_amortisation_schedule(principal, annual_rate_pct, n, instalment) -> list[AmortRow]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models.schemas import KFSExtraction, Charge
from .apr import ChargeItem, compute_apr, emi, APRResult

# Default: stamp duty excluded from APR (toggle per PRD §21 open item 4)
DEFAULT_INCLUDE_STAMP_DUTY = False


# ---------------------------------------------------------------------------
# APR variants result
# ---------------------------------------------------------------------------


@dataclass
class APRVariants:
    interest_only_pct: float
    all_charges_pct: float
    excluding_third_party_pct: float
    effective_annual_all_charges_pct: float
    # raw APRResult from apr.py for full detail access
    apr_result: APRResult
    stamp_duty_included_in_apr: bool
    instalment_used: float
    n_periods: int


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------


@dataclass
class ChargeBreakdown:
    category: str
    payee: str          # "lender" | "third_party"
    total_inr: float
    names: list[str]


# ---------------------------------------------------------------------------
# Amortisation schedule
# ---------------------------------------------------------------------------


@dataclass
class AmortRow:
    """One row of the computed reducing-balance amortisation schedule."""
    n: int             # period number (1-based)
    outstanding: float # opening balance for this period
    principal: float   # principal repaid this period
    interest: float    # interest charged this period
    instalment: float  # total payment this period


def compute_amortisation_schedule(
    principal: float,
    annual_rate_pct: float,
    n: int,
    instalment: float,
) -> list[AmortRow]:
    """Compute a full reducing-balance amortisation schedule.

    Parameters
    ----------
    principal        : sanctioned loan amount (opening balance period 1)
    annual_rate_pct  : nominal annual interest rate, e.g. 15.0
    n                : total number of monthly periods
    instalment       : fixed monthly payment (EMI) — full-precision value

    Rounding:
        Each period's interest and principal are rounded to 2 dp.
        The final period's principal is adjusted so the closing balance
        reaches exactly 0 (absorbs accumulated rounding drift).

    Returns a list of n AmortRow objects.
    """
    r = annual_rate_pct / 12 / 100
    rows: list[AmortRow] = []
    balance = principal

    for i in range(1, n + 1):
        opening = balance
        interest = round(opening * r, 2)
        if i < n:
            principal_repaid = round(instalment - interest, 2)
            inst_this = instalment
        else:
            # Last period: repay exactly the remaining balance
            principal_repaid = round(balance, 2)
            inst_this = round(interest + principal_repaid, 2)

        balance = round(balance - principal_repaid, 2)

        rows.append(AmortRow(
            n=i,
            outstanding=round(opening, 2),
            principal=principal_repaid,
            interest=interest,
            instalment=round(inst_this, 2),
        ))

    return rows


@dataclass
class AnalysisSummary:
    sanctioned_amount_inr: float
    interest_rate_pct: float
    n_periods: int
    instalment_used: float

    apr: APRVariants

    total_repayable: float
    total_interest: float
    total_charges_inr: float
    fee_impact_pp: float            # all_charges_apr − interest_rate
    charge_pct_of_loan: float       # total_charges / sanctioned_amount * 100

    charge_breakdown: list[ChargeBreakdown]
    stamp_duty_assumption: str      # human-readable note for the UI


# ---------------------------------------------------------------------------
# Helpers: convert Charge → ChargeItem
# ---------------------------------------------------------------------------


def build_charge_items(
    extraction: KFSExtraction,
    include_stamp_duty: bool = DEFAULT_INCLUDE_STAMP_DUTY,
) -> list[ChargeItem]:
    """Convert extraction charges to ChargeItem objects for the APR engine.

    Stamp duty is excluded by default per the config toggle.  Records without
    a resolved amount_inr are skipped (percent charges must already be
    normalised by the extractor).
    """
    items: list[ChargeItem] = []
    for c in extraction.charges:
        if c.amount_inr is None:
            continue
        if c.category == "stamp_duty" and not include_stamp_duty:
            continue
        items.append(
            ChargeItem(
                amount_inr=c.amount_inr,
                payee=c.payee or "lender",
                frequency=c.frequency or "one_time",
                recurrence=c.recurrence,
                start_period=1,
            )
        )
    return items


# ---------------------------------------------------------------------------
# APR variants
# ---------------------------------------------------------------------------


def compute_apr_variants(
    extraction: KFSExtraction,
    include_stamp_duty: bool = DEFAULT_INCLUDE_STAMP_DUTY,
) -> Optional[APRVariants]:
    """Compute all three APR variants from the extraction.

    Returns None if essential inputs are missing.
    """
    loan = extraction.loan
    rate = extraction.rate

    principal = loan.sanctioned_amount_inr.value
    rate_pct = rate.interest_rate_pct.value
    n = loan.term_months.value

    if principal is None or rate_pct is None or n is None:
        return None
    if principal <= 0 or n <= 0:
        return None

    # Use the stated instalment if available; fall back to computed
    instalment_override: Optional[float] = None
    if loan.instalments:
        stated = loan.instalments[0].amount_inr.value
        if stated is not None and stated > 0:
            instalment_override = stated

    charges = build_charge_items(extraction, include_stamp_duty)

    result = compute_apr(
        sanctioned_amount=float(principal),
        annual_rate_pct=float(rate_pct),
        n=int(n),
        charges=charges,
        instalment_override=instalment_override,
    )

    stamp_duty_in = include_stamp_duty
    return APRVariants(
        interest_only_pct=result.apr_interest_only_pct,
        all_charges_pct=result.apr_all_charges_pct,
        excluding_third_party_pct=result.apr_excl_third_party_pct,
        effective_annual_all_charges_pct=result.ear_all_charges_pct,
        apr_result=result,
        stamp_duty_included_in_apr=stamp_duty_in,
        instalment_used=result.instalment,
        n_periods=result.n_periods,
    )


# ---------------------------------------------------------------------------
# Charge breakdown
# ---------------------------------------------------------------------------


def _charge_breakdown(extraction: KFSExtraction, include_stamp_duty: bool) -> list[ChargeBreakdown]:
    by_cat: dict[str, ChargeBreakdown] = {}
    for c in extraction.charges:
        if c.amount_inr is None:
            continue
        if c.category == "stamp_duty" and not include_stamp_duty:
            cat_key = "stamp_duty_excluded"
        else:
            cat_key = (c.category or "other") + "__" + (c.payee or "lender")

        if cat_key not in by_cat:
            by_cat[cat_key] = ChargeBreakdown(
                category=c.category or "other",
                payee=c.payee or "lender",
                total_inr=0.0,
                names=[],
            )
        by_cat[cat_key].total_inr += c.amount_inr
        by_cat[cat_key].names.append(c.name)

    return list(by_cat.values())


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_summary(
    extraction: KFSExtraction,
    include_stamp_duty: bool = DEFAULT_INCLUDE_STAMP_DUTY,
) -> Optional[AnalysisSummary]:
    """Build the complete analysis summary.  Returns None if critical inputs missing."""
    apr_variants = compute_apr_variants(extraction, include_stamp_duty)
    if apr_variants is None:
        return None

    principal = extraction.loan.sanctioned_amount_inr.value
    rate_pct = extraction.rate.interest_rate_pct.value
    result = apr_variants.apr_result

    total_charges = sum(
        c.amount_inr
        for c in extraction.charges
        if c.amount_inr is not None
        and not (c.category == "stamp_duty" and not include_stamp_duty)
    )

    fee_impact = apr_variants.all_charges_pct - float(rate_pct)
    charge_pct = (total_charges / float(principal) * 100) if principal else 0.0

    stamp_note = (
        "Stamp duty excluded from APR (default). Toggle 'Include stamp duty' to recalculate."
        if any(c.category == "stamp_duty" for c in extraction.charges)
        else ""
    )

    return AnalysisSummary(
        sanctioned_amount_inr=float(principal),
        interest_rate_pct=float(rate_pct),
        n_periods=apr_variants.n_periods,
        instalment_used=apr_variants.instalment_used,
        apr=apr_variants,
        total_repayable=result.total_repayable,
        total_interest=result.total_interest,
        total_charges_inr=total_charges,
        fee_impact_pp=round(fee_impact, 4),
        charge_pct_of_loan=round(charge_pct, 4),
        charge_breakdown=_charge_breakdown(extraction, include_stamp_duty),
        stamp_duty_assumption=stamp_note,
    )
