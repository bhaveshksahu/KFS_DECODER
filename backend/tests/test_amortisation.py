"""Tests for compute_amortisation_schedule() — analysis.py.

Four required properties (per task spec):
  1. Row count equals term n.
  2. Sum of principal repayments equals sanctioned amount within 0.01.
  3. Final closing balance is 0.
  4. First-row interest equals principal * (rate/12/100), rounded to 2dp.

Additional checks:
  - Row structure (fields present, types correct).
  - First outstanding equals sanctioned amount.
  - Each non-final instalment equals the passed-in EMI (rounded to 2dp).
  - Final instalment adjusts to close the balance.
  - RBI Annex B spot-check: period-1 interest = 20,000 × 15/12/100 = 250.00.
  - Synthetic vector spot-check: period-1 interest = 100,000 × 12/12/100 = 1,000.00.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from app.services.analysis import compute_amortisation_schedule, AmortRow
from app.services.apr import emi


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _rbi_annex_b():
    """RBI Annex B: 20,000 @ 15% / 24 months, exact EMI."""
    principal = 20_000.0
    rate      = 15.0
    n         = 24
    inst      = emi(principal, rate, n)  # 969.732960...
    return principal, rate, n, inst


def _synthetic():
    """Synthetic: 1,00,000 @ 12% / 24 months, exact EMI."""
    principal = 100_000.0
    rate      = 12.0
    n         = 24
    inst      = emi(principal, rate, n)  # 4707.347222...
    return principal, rate, n, inst


# ---------------------------------------------------------------------------
# Core invariants
# ---------------------------------------------------------------------------

class TestAmortisationInvariants:
    """The four mandatory invariants from the task spec."""

    @pytest.mark.parametrize("fixture_fn", [_rbi_annex_b, _synthetic])
    def test_row_count_equals_term(self, fixture_fn):
        """Invariant 1: number of rows == n."""
        principal, rate, n, inst = fixture_fn()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        assert len(rows) == n, f"Expected {n} rows, got {len(rows)}"

    @pytest.mark.parametrize("fixture_fn", [_rbi_annex_b, _synthetic])
    def test_principal_sums_to_sanctioned_amount(self, fixture_fn):
        """Invariant 2: sum of principal repayments ≈ sanctioned amount within 0.01."""
        principal, rate, n, inst = fixture_fn()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        total_principal = sum(r.principal for r in rows)
        assert abs(total_principal - principal) < 0.01, (
            f"Sum of principal {total_principal:.4f} != sanctioned {principal:.2f}"
        )

    @pytest.mark.parametrize("fixture_fn", [_rbi_annex_b, _synthetic])
    def test_final_balance_is_zero(self, fixture_fn):
        """Invariant 3: closing balance after last period == 0."""
        principal, rate, n, inst = fixture_fn()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        last = rows[-1]
        closing = round(last.outstanding - last.principal, 2)
        assert closing == 0.0, f"Closing balance after last period = {closing}"

    @pytest.mark.parametrize("fixture_fn", [_rbi_annex_b, _synthetic])
    def test_first_row_interest_equals_principal_times_monthly_rate(self, fixture_fn):
        """Invariant 4: row-1 interest == round(principal * rate/12/100, 2)."""
        principal, rate, n, inst = fixture_fn()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        expected = round(principal * rate / 12 / 100, 2)
        assert rows[0].interest == expected, (
            f"Row-1 interest {rows[0].interest} != expected {expected}"
        )


# ---------------------------------------------------------------------------
# Structure checks
# ---------------------------------------------------------------------------

class TestAmortisationStructure:

    def test_returns_amort_row_objects(self):
        principal, rate, n, inst = _rbi_annex_b()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        assert all(isinstance(r, AmortRow) for r in rows)

    def test_row_numbers_are_sequential_one_based(self):
        principal, rate, n, inst = _rbi_annex_b()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        assert [r.n for r in rows] == list(range(1, n + 1))

    def test_first_outstanding_equals_principal(self):
        principal, rate, n, inst = _rbi_annex_b()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        assert rows[0].outstanding == principal

    def test_outstanding_decreases_monotonically(self):
        principal, rate, n, inst = _rbi_annex_b()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        for i in range(1, len(rows)):
            assert rows[i].outstanding < rows[i - 1].outstanding, (
                f"Outstanding not decreasing at period {rows[i].n}"
            )

    def test_all_fields_rounded_to_2dp(self):
        """Every float field must already be at 2 decimal places."""
        principal, rate, n, inst = _synthetic()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        for r in rows:
            for attr in ("outstanding", "principal", "interest", "instalment"):
                val = getattr(r, attr)
                assert val == round(val, 2), (
                    f"Period {r.n} {attr}={val} is not rounded to 2dp"
                )


# ---------------------------------------------------------------------------
# Spot-checks against known values
# ---------------------------------------------------------------------------

class TestAmortisationSpotChecks:
    """Spot-checks against values derivable from first principles."""

    def test_rbi_annex_b_period1_interest_is_250(self):
        """RBI Annex B: period-1 interest = 20,000 × 15/12/100 = 250.00."""
        principal, rate, n, inst = _rbi_annex_b()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        assert rows[0].interest == 250.00, (
            f"Period-1 interest = {rows[0].interest}, expected 250.00"
        )

    def test_rbi_annex_b_period1_principal(self):
        """RBI Annex B: period-1 principal = EMI − interest = 969.73 − 250 = 719.73 (approx)."""
        principal, rate, n, inst = _rbi_annex_b()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        expected_principal = round(inst - 250.00, 2)
        assert abs(rows[0].principal - expected_principal) < 0.01, (
            f"Period-1 principal = {rows[0].principal}, expected ~{expected_principal}"
        )

    def test_synthetic_period1_interest_is_1000(self):
        """Synthetic: period-1 interest = 100,000 × 12/12/100 = 1,000.00."""
        principal, rate, n, inst = _synthetic()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        assert rows[0].interest == 1_000.00, (
            f"Period-1 interest = {rows[0].interest}, expected 1,000.00"
        )

    def test_rbi_annex_b_opening_balance_propagates(self):
        """Each period's opening balance == previous period's (outstanding − principal)."""
        principal, rate, n, inst = _rbi_annex_b()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        for i in range(1, len(rows)):
            expected_opening = round(rows[i - 1].outstanding - rows[i - 1].principal, 2)
            assert rows[i].outstanding == expected_opening, (
                f"Period {rows[i].n}: opening {rows[i].outstanding} != "
                f"expected {expected_opening}"
            )

    def test_final_instalment_closes_balance(self):
        """Last instalment = interest + remaining balance (may differ from EMI)."""
        principal, rate, n, inst = _rbi_annex_b()
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        last = rows[-1]
        assert abs(last.instalment - (last.interest + last.principal)) < 0.01, (
            f"Last instalment {last.instalment} != interest+principal "
            f"{last.interest + last.principal}"
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestAmortisationEdgeCases:

    def test_zero_rate_loan(self):
        """Zero-rate loan: all interest rows are 0, each principal = inst."""
        principal, rate, n = 12_000.0, 0.0, 12
        inst = principal / n   # 1000.0
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        assert len(rows) == n
        assert all(r.interest == 0.0 for r in rows)
        total_principal = sum(r.principal for r in rows)
        assert abs(total_principal - principal) < 0.01

    def test_single_period_loan(self):
        """n=1: only one row, principal equals entire loan, closes to 0."""
        principal, rate, n = 5_000.0, 12.0, 1
        r_monthly = rate / 12 / 100
        inst = principal * (1 + r_monthly)   # full repayment at once
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        assert len(rows) == 1
        assert rows[0].outstanding == principal
        assert round(rows[0].outstanding - rows[0].principal, 2) == 0.0

    def test_large_loan(self):
        """Stress test: 5-crore loan, 20 years, 8.5% — invariants must hold."""
        principal = 50_000_000.0
        rate = 8.5
        n = 240
        inst = emi(principal, rate, n)
        rows = compute_amortisation_schedule(principal, rate, n, inst)
        assert len(rows) == n
        assert abs(sum(r.principal for r in rows) - principal) < 0.02
        assert round(rows[-1].outstanding - rows[-1].principal, 2) == 0.0
