"""APR engine — RBI Annex B convention.

APR = monthly IRR × 12  (nominal, NOT effective annual rate).
Solve via Brent's method (scipy) with bisection fallback so scipy is optional.

Three variants always computed:
  - interest_only : net_disbursed = sanctioned_amount  (no charges)
  - all_charges   : net_disbursed = sanctioned_amount − all_upfront_charges
  - excl_third_party : net_disbursed = sanctioned_amount − lender_charges_only
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# Primitive financial functions
# ---------------------------------------------------------------------------


def emi(principal: float, annual_rate_pct: float, n: int) -> float:
    """Reducing-balance equal monthly instalment."""
    r = annual_rate_pct / 12 / 100
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


def _npv(rate: float, cash_flows: list[float]) -> float:
    """Net present value: cash_flows[0] is t=1, cash_flows[n-1] is t=n."""
    return sum(cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cash_flows))


def _bisection(f: Callable[[float], float], lo: float, hi: float, tol: float = 1e-10) -> float:
    """Bisection root-finder — no external dependencies."""
    for _ in range(200):
        mid = (lo + hi) / 2
        if abs(hi - lo) < tol:
            break
        if f(mid) * f(lo) < 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def _irr_monthly(net_disbursed: float, cash_flows: list[float]) -> float:
    """Monthly IRR such that NPV(disbursed, cash_flows) == 0.

    cash_flows is a list of n values (positive = payment from borrower's POV
    in absolute terms; we discount outflows).
    """
    # f(r) = 0  ↔  disbursed = Σ cf_t / (1+r)^t
    f = lambda r: -net_disbursed + _npv(r, cash_flows)

    try:
        from scipy.optimize import brentq  # type: ignore[import]

        return brentq(f, 1e-9, 0.5)
    except ImportError:
        return _bisection(f, 1e-9, 0.5)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ChargeItem:
    """A single charge on the loan.

    amount_inr  : the charge in rupees
    payee       : "lender" or "third_party"
    frequency   : "one_time" or "recurring"
    recurrence  : "monthly" or "yearly" (only for recurring)
    start_period: 1-based period number where the first recurring charge falls
                  (default 1, ignored for one_time)
    """

    amount_inr: float
    payee: str = "lender"            # "lender" | "third_party"
    frequency: str = "one_time"      # "one_time" | "recurring"
    recurrence: str | None = None    # "monthly" | "yearly"
    start_period: int = 1


@dataclass
class APRResult:
    """Full APR calculation result."""

    # Three nominal APR variants (monthly IRR × 12 × 100)
    apr_all_charges_pct: float
    apr_interest_only_pct: float
    apr_excl_third_party_pct: float

    # Effective annual rates ((1+r)^12 − 1) — labelled differently from KFS APR
    ear_all_charges_pct: float
    ear_interest_only_pct: float
    ear_excl_third_party_pct: float

    # Convenience totals
    instalment: float
    n_periods: int
    total_repayable: float
    total_interest: float
    total_charges: float
    net_disbursed_all: float
    net_disbursed_excl_third_party: float


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------


def _build_cash_flows(
    instalment: float,
    n: int,
    charges: list[ChargeItem],
    include_payees: set[str],
) -> tuple[float, list[float]]:
    """Build (net_disbursed, per_period_outflows) for a given set of payees.

    net_disbursed = sanctioned_amount − one_time_charges (for included payees)
    per_period_outflows[t-1] = instalment + recurring_charges_at_t

    NOTE: sanctioned_amount is NOT passed in here; the caller sets it.  We
    return `total_upfront` so the caller can subtract from sanctioned_amount.
    """
    total_upfront = sum(
        c.amount_inr
        for c in charges
        if c.frequency == "one_time" and c.payee in include_payees
    )

    # Build per-period cash-flow array
    period_cfs: list[float] = [instalment] * n
    for c in charges:
        if c.frequency != "recurring" or c.payee not in include_payees:
            continue
        if c.recurrence == "monthly":
            for t in range(c.start_period - 1, n):
                period_cfs[t] += c.amount_inr
        elif c.recurrence == "yearly":
            for yr in range(0, n // 12 + 1):
                t = c.start_period - 1 + yr * 12
                if t < n:
                    period_cfs[t] += c.amount_inr

    return total_upfront, period_cfs


def compute_apr(
    sanctioned_amount: float,
    annual_rate_pct: float,
    n: int,
    charges: list[ChargeItem] | None = None,
    instalment_override: float | None = None,
) -> APRResult:
    """Compute all three APR variants.

    Parameters
    ----------
    sanctioned_amount   : loan principal as stated in the KFS
    annual_rate_pct     : stated interest rate (used to compute EMI if no override)
    n                   : number of monthly instalments
    charges             : list of ChargeItem; defaults to empty list
    instalment_override : use exact EMI from KFS instead of computing it
    """
    if charges is None:
        charges = []

    inst = instalment_override if instalment_override is not None else emi(sanctioned_amount, annual_rate_pct, n)

    def _apr(upfront: float, period_cfs: list[float]) -> float:
        nd = sanctioned_amount - upfront
        monthly_irr = _irr_monthly(nd, period_cfs)
        return monthly_irr * 12 * 100

    def _ear(apr_nominal: float) -> float:
        r = apr_nominal / 12 / 100
        return ((1 + r) ** 12 - 1) * 100

    # Variant 1 — interest only (no charges)
    _, cfs_io = _build_cash_flows(inst, n, [], set())
    apr_io = _apr(0.0, cfs_io)

    # Variant 2 — all charges
    upfront_all, cfs_all = _build_cash_flows(inst, n, charges, {"lender", "third_party"})
    apr_all = _apr(upfront_all, cfs_all)

    # Variant 3 — exclude third-party charges
    upfront_lender, cfs_lender = _build_cash_flows(inst, n, charges, {"lender"})
    apr_excl_tp = _apr(upfront_lender, cfs_lender)

    total_repayable = sum(cfs_all)  # sum of all outflows (interest + principal)
    total_interest = total_repayable - sanctioned_amount
    total_charges = sum(c.amount_inr for c in charges)

    return APRResult(
        apr_all_charges_pct=round(apr_all, 6),
        apr_interest_only_pct=round(apr_io, 6),
        apr_excl_third_party_pct=round(apr_excl_tp, 6),
        ear_all_charges_pct=round(_ear(apr_all), 6),
        ear_interest_only_pct=round(_ear(apr_io), 6),
        ear_excl_third_party_pct=round(_ear(apr_excl_tp), 6),
        instalment=round(inst, 6),
        n_periods=n,
        total_repayable=round(total_repayable, 2),
        total_interest=round(total_interest, 2),
        total_charges=total_charges,
        net_disbursed_all=round(sanctioned_amount - upfront_all, 2),
        net_disbursed_excl_third_party=round(sanctioned_amount - upfront_lender, 2),
    )


def effective_annual_pct(apr_nominal_pct: float) -> float:
    """Convenience: (1+r)^12 − 1, r = nominal monthly rate."""
    r = apr_nominal_pct / 12 / 100
    return ((1 + r) ** 12 - 1) * 100
