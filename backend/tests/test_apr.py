"""Test suite for the APR engine — both golden vectors from PRD Appendix A.

Vector 1 — RBI Annex B illustration (from the circular):
  Loan ₹20,000 · 15% p.a. · 24 monthly EMIs · charges ₹400 (₹240 lender + ₹160 third-party)
  Net disbursed ₹19,600

  Expected (PRD §Appendix A):
    APR all charges, exact EMI 969.73   → 17.07% ± 0.01 pp
    APR all charges, rounded EMI 970    → 17.10% ± 0.01 pp
    APR interest only                   → 15.00% ± 0.01 pp
    APR excl. third-party (₹160)        → 16.24% ± 0.01 pp

Vector 2 — Synthetic "bad KFS":
  Loan ₹1,00,000 · 12% p.a. · 24 monthly EMIs
  Charges: ₹3,000 lender processing + ₹500 lender documentation + ₹4,000 third-party insurance
  Net disbursed ₹92,500

  Expected:
    APR all charges                     → 19.99% ± 0.01 pp
    APR excl. third-party (₹4,000)      → 15.62% ± 0.01 pp
    APR interest only                   → 12.00% ± 0.01 pp
"""

import sys
import os

# Allow running from repo root or backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services.apr import ChargeItem, compute_apr, emi, effective_annual_pct


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOLERANCE = 0.01  # ± 0.01 percentage points


def assert_apr(actual: float, expected: float, label: str) -> None:
    assert abs(actual - expected) <= TOLERANCE, (
        f"{label}: got {actual:.4f}%, expected {expected:.2f}% (tol ±{TOLERANCE} pp)"
    )


# ---------------------------------------------------------------------------
# Vector 1 — RBI Annex B
# ---------------------------------------------------------------------------


class TestRBIAnnexB:
    """PRD Appendix A, Vector 1 — lifted directly from the RBI circular."""

    loan = 20_000.0
    rate = 15.0
    n = 24
    charges_lender = [ChargeItem(amount_inr=240.0, payee="lender")]
    charges_tp = [ChargeItem(amount_inr=160.0, payee="third_party")]
    charges_all = charges_lender + charges_tp

    def test_emi_calculation(self):
        """EMI should be ~969.73 (RBI notes the rounding to 970)."""
        e = emi(self.loan, self.rate, self.n)
        assert abs(e - 969.73) < 0.01, f"EMI = {e:.4f}, expected ~969.73"

    def test_apr_all_charges_exact_emi(self):
        """APR with all charges and exact EMI → 17.07% (RBI stated value)."""
        e = emi(self.loan, self.rate, self.n)          # exact EMI
        result = compute_apr(self.loan, self.rate, self.n, self.charges_all, e)
        assert_apr(result.apr_all_charges_pct, 17.07, "APR all charges (exact EMI)")

    def test_apr_all_charges_rounded_emi(self):
        """APR with all charges and rounded EMI 970 → ~17.10%."""
        result = compute_apr(self.loan, self.rate, self.n, self.charges_all, 970.0)
        assert_apr(result.apr_all_charges_pct, 17.10, "APR all charges (rounded EMI 970)")

    def test_apr_interest_only(self):
        """APR with zero charges → equals stated rate 15.00%."""
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, [], e)
        assert_apr(result.apr_interest_only_pct, 15.00, "APR interest only")

    def test_apr_excl_third_party(self):
        """APR excluding ₹160 third-party → 16.24%."""
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, self.charges_all, e)
        assert_apr(result.apr_excl_third_party_pct, 16.24, "APR excl. third-party")

    def test_net_disbursed_all_charges(self):
        """Net disbursed with all charges should be 19,600."""
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, self.charges_all, e)
        assert abs(result.net_disbursed_all - 19_600.0) < 0.01

    def test_net_disbursed_excl_third_party(self):
        """Net disbursed excl. third-party (₹160 removed) should be 19,760."""
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, self.charges_all, e)
        assert abs(result.net_disbursed_excl_third_party - 19_760.0) < 0.01

    def test_convention_nominal_not_effective(self):
        """Verify that we use nominal (monthly IRR × 12), not compounded EAR."""
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, [], e)
        # Nominal 15% → EAR > 15%
        assert result.ear_interest_only_pct > result.apr_interest_only_pct


# ---------------------------------------------------------------------------
# Vector 2 — Synthetic "bad KFS"
# ---------------------------------------------------------------------------


class TestSyntheticBadKFS:
    """PRD Appendix A, Vector 2 — synthetic loan authored for testing."""

    loan = 100_000.0
    rate = 12.0
    n = 24
    charges = [
        ChargeItem(amount_inr=3_000.0, payee="lender",      frequency="one_time"),  # processing
        ChargeItem(amount_inr=500.0,   payee="lender",      frequency="one_time"),  # documentation
        ChargeItem(amount_inr=4_000.0, payee="third_party", frequency="one_time"),  # insurance
    ]

    def test_emi_calculation(self):
        """EMI should be ~4707.35."""
        e = emi(self.loan, self.rate, self.n)
        assert abs(e - 4707.35) < 0.01, f"EMI = {e:.4f}, expected ~4707.35"

    def test_apr_all_charges(self):
        """APR all charges → 19.99%."""
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, self.charges, e)
        assert_apr(result.apr_all_charges_pct, 19.99, "APR all charges (synthetic)")

    def test_apr_excl_third_party(self):
        """APR excl. ₹4,000 insurance (third-party) → 15.62%.

        This matches the stated APR on the document (15.6%), meaning R-02 fires:
        the lender's stated APR was computed excluding the third-party charge.
        """
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, self.charges, e)
        assert_apr(result.apr_excl_third_party_pct, 15.62, "APR excl. third-party (synthetic)")

    def test_apr_interest_only(self):
        """APR interest only → 12.00%."""
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, self.charges, e)
        assert_apr(result.apr_interest_only_pct, 12.00, "APR interest only (synthetic)")

    def test_net_disbursed(self):
        """Net disbursed with all charges = 100,000 − 7,500 = 92,500."""
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, self.charges, e)
        assert abs(result.net_disbursed_all - 92_500.0) < 0.01

    def test_r01_would_fail(self):
        """R-01: |computed APR − stated APR| > 0.15 pp should be flagged.

        Stated APR = 15.6%; computed APR = 19.99% → gap ≈ 4.39 pp → FAIL.
        """
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, self.charges, e)
        stated_apr = 15.6
        gap = abs(result.apr_all_charges_pct - stated_apr)
        assert gap > 0.15, f"R-01 should have fired, gap = {gap:.2f} pp"

    def test_r02_would_fire(self):
        """R-02: excl-third-party APR ≈ stated APR → lender excluded third-party.

        15.62% vs stated 15.6% → within 0.03 pp → R-02 fires.
        """
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, self.charges, e)
        stated_apr = 15.6
        gap_excl_tp = abs(result.apr_excl_third_party_pct - stated_apr)
        assert gap_excl_tp < 0.10, (
            f"R-02 should fire: excl-TP APR {result.apr_excl_third_party_pct:.2f}% "
            f"vs stated {stated_apr}%, gap {gap_excl_tp:.4f} pp"
        )

    def test_total_charges(self):
        """Total charges should be ₹7,500."""
        e = emi(self.loan, self.rate, self.n)
        result = compute_apr(self.loan, self.rate, self.n, self.charges, e)
        assert abs(result.total_charges - 7_500.0) < 0.01


# ---------------------------------------------------------------------------
# Recurring-charge cash flow
# ---------------------------------------------------------------------------


class TestRecurringCharges:
    """Verify that recurring charges are subtracted from the relevant period."""

    def test_yearly_recurring_increases_apr(self):
        """Adding a recurring yearly charge should push APR above the no-charge baseline."""
        loan, rate, n = 50_000.0, 10.0, 36
        e = emi(loan, rate, n)
        result_no_charges = compute_apr(loan, rate, n, [], e)

        recurring = [ChargeItem(amount_inr=500.0, payee="lender", frequency="recurring", recurrence="yearly")]
        result_with = compute_apr(loan, rate, n, recurring, e)

        assert result_with.apr_all_charges_pct > result_no_charges.apr_all_charges_pct

    def test_monthly_recurring_higher_than_yearly(self):
        """Monthly recurring cost > yearly cost of the same per-period amount."""
        loan, rate, n = 50_000.0, 10.0, 24
        e = emi(loan, rate, n)

        monthly = [ChargeItem(amount_inr=100.0, payee="lender", frequency="recurring", recurrence="monthly")]
        yearly = [ChargeItem(amount_inr=100.0, payee="lender", frequency="recurring", recurrence="yearly")]

        r_monthly = compute_apr(loan, rate, n, monthly, e)
        r_yearly = compute_apr(loan, rate, n, yearly, e)

        assert r_monthly.apr_all_charges_pct > r_yearly.apr_all_charges_pct


# ---------------------------------------------------------------------------
# effective_annual_pct helper
# ---------------------------------------------------------------------------


class TestEffectiveAnnualPct:
    def test_15pct_nominal(self):
        """15% nominal monthly → EAR ≈ 16.075%."""
        ear = effective_annual_pct(15.0)
        assert abs(ear - 16.075) < 0.01, f"EAR = {ear:.4f}%, expected ~16.075%"

    def test_zero_rate(self):
        ear = effective_annual_pct(0.0)
        assert ear == pytest.approx(0.0, abs=1e-9)
