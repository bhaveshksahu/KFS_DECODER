"""Golden-vector tests — Appendix A of the KFS Decoder PRD.

ALL expected values are LITERAL numbers copied from the PRD / RBI circular.
They must NEVER be replaced with expressions that call the code under test.

Vector 1 — RBI Annex B (from RBI circular RBI/2024-25/18):
  Loan ₹20,000 · 15% p.a. fixed · 24 monthly EMIs · first EMI 30 days after sanction
  Charges ₹240 lender + ₹160 third-party = ₹400 total · Net disbursed ₹19,600

Vector 2 — Synthetic "bad KFS" (authored for the project):
  Loan ₹1,00,000 · 12% p.a. fixed · 24 monthly EMIs
  Charges ₹3,000 processing (lender) + ₹500 documentation (lender) + ₹4,000 insurance (third-party)
  Net disbursed ₹92,500 · Stated APR 15.6% (deliberately excludes insurance)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.apr import ChargeItem, emi, compute_apr, effective_annual_pct

TOL = 0.01  # ±0.01 pp tolerance as stated in PRD Appendix A


# ---------------------------------------------------------------------------
# Vector 1 — RBI Annex B
# ---------------------------------------------------------------------------

class TestRBIAnnexBGolden:
    """Pins exact APR figures from RBI/2024-25/18 Annex B."""

    # --- pre-conditions (establish the fixture values) ---

    def test_v1_emi_is_969_73(self):
        """EMI for ₹20,000 at 15% p.a. over 24 months = 969.73 (PRD Appendix A)."""
        result = emi(20_000, 15, 24)
        assert abs(result - 969.73) < TOL, f"EMI = {result:.4f}, expected 969.73"

    # --- APR variants with exact EMI 969.73 ---

    def test_v1_apr_all_charges_exact_emi_is_17_07(self):
        """APR (all charges, exact EMI 969.73, net disbursed 19,600) = 17.07% (PRD Appendix A)."""
        exact_emi = emi(20_000, 15, 24)
        charges = [
            ChargeItem(amount_inr=240.0, payee="lender",      frequency="one_time"),
            ChargeItem(amount_inr=160.0, payee="third_party", frequency="one_time"),
        ]
        result = compute_apr(20_000, 15, 24, charges, instalment_override=exact_emi)
        assert abs(result.apr_all_charges_pct - 17.07) < TOL, (
            f"APR all charges = {result.apr_all_charges_pct:.4f}%, expected 17.07%"
        )

    def test_v1_apr_interest_only_is_15_00(self):
        """APR (interest only, net disbursed 20,000) = 15.00% (PRD Appendix A)."""
        exact_emi = emi(20_000, 15, 24)
        result = compute_apr(20_000, 15, 24, charges=[], instalment_override=exact_emi)
        assert abs(result.apr_interest_only_pct - 15.00) < TOL, (
            f"APR interest only = {result.apr_interest_only_pct:.4f}%, expected 15.00%"
        )

    def test_v1_apr_excl_third_party_is_16_24(self):
        """APR excl. ₹160 third-party (net disbursed 19,760) = 16.24% (PRD Appendix A)."""
        exact_emi = emi(20_000, 15, 24)
        charges = [
            ChargeItem(amount_inr=240.0, payee="lender",      frequency="one_time"),
            ChargeItem(amount_inr=160.0, payee="third_party", frequency="one_time"),
        ]
        result = compute_apr(20_000, 15, 24, charges, instalment_override=exact_emi)
        assert abs(result.apr_excl_third_party_pct - 16.24) < TOL, (
            f"APR excl third-party = {result.apr_excl_third_party_pct:.4f}%, expected 16.24%"
        )

    # --- APR variant with rounded EMI 970 ---

    def test_v1_apr_all_charges_rounded_emi_is_17_10(self):
        """APR (all charges, rounded EMI 970) = 17.10% (PRD Appendix A)."""
        charges = [
            ChargeItem(amount_inr=240.0, payee="lender",      frequency="one_time"),
            ChargeItem(amount_inr=160.0, payee="third_party", frequency="one_time"),
        ]
        result = compute_apr(20_000, 15, 24, charges, instalment_override=970.0)
        assert abs(result.apr_all_charges_pct - 17.10) < TOL, (
            f"APR all charges (EMI 970) = {result.apr_all_charges_pct:.4f}%, expected 17.10%"
        )

    # --- net disbursed sanity ---

    def test_v1_net_disbursed_is_19600(self):
        """Net disbursed with all charges (₹240 + ₹160) = ₹19,600 (PRD Appendix A)."""
        charges = [
            ChargeItem(amount_inr=240.0, payee="lender",      frequency="one_time"),
            ChargeItem(amount_inr=160.0, payee="third_party", frequency="one_time"),
        ]
        result = compute_apr(20_000, 15, 24, charges, instalment_override=970.0)
        assert abs(result.net_disbursed_all - 19_600.0) < 0.01, (
            f"Net disbursed = {result.net_disbursed_all}, expected 19,600"
        )

    def test_v1_convention_nominal_not_effective(self):
        """APR convention is monthly IRR × 12 (nominal), NOT compounded EAR (PRD §9)."""
        exact_emi = emi(20_000, 15, 24)
        result = compute_apr(20_000, 15, 24, [], instalment_override=exact_emi)
        ear = effective_annual_pct(result.apr_interest_only_pct)
        assert ear > result.apr_interest_only_pct, (
            "EAR must be > nominal APR when monthly compounding"
        )


# ---------------------------------------------------------------------------
# Vector 2 — Synthetic "bad KFS"
# ---------------------------------------------------------------------------

class TestSyntheticBadKFSGolden:
    """Pins exact APR and total figures from PRD Appendix A, Vector 2."""

    # PRD Appendix A states EMI = ₹4,707.35 (display value).
    # The total repayable ₹1,12,976.33 is derived from the full-precision EMI
    # (4707.347222...) × 24.  Both the display value and precision are tested.
    _STATED_EMI = 4_707.35      # displayed in the KFS

    def _charges(self) -> list[ChargeItem]:
        return [
            ChargeItem(amount_inr=3_000.0, payee="lender",      frequency="one_time"),
            ChargeItem(amount_inr=500.0,   payee="lender",       frequency="one_time"),
            ChargeItem(amount_inr=4_000.0, payee="third_party",  frequency="one_time"),
        ]

    def test_v2_emi_display_value_is_4707_35(self):
        """Computed EMI rounds to ₹4,707.35 (PRD Appendix A, Vector 2)."""
        result = emi(100_000, 12, 24)
        assert abs(result - 4_707.35) < 0.01, f"EMI = {result:.4f}, expected ~4707.35"

    def test_v2_apr_all_charges_is_19_99(self):
        """APR (all charges, net 92,500) = 19.99% (PRD Appendix A)."""
        result = compute_apr(100_000, 12, 24, self._charges(), instalment_override=self._STATED_EMI)
        assert abs(result.apr_all_charges_pct - 19.99) < TOL, (
            f"APR all charges = {result.apr_all_charges_pct:.4f}%, expected 19.99%"
        )

    def test_v2_apr_excl_third_party_is_15_62(self):
        """APR excl. ₹4,000 insurance (net 96,500) = 15.62% (PRD Appendix A).

        This is the value that matches the lender's stated APR of 15.6%,
        confirming R-02 fires.
        """
        result = compute_apr(100_000, 12, 24, self._charges(), instalment_override=self._STATED_EMI)
        assert abs(result.apr_excl_third_party_pct - 15.62) < TOL, (
            f"APR excl third-party = {result.apr_excl_third_party_pct:.4f}%, expected 15.62%"
        )

    def test_v2_apr_interest_only_is_12_00(self):
        """APR interest only = 12.00% (equals stated rate, PRD Appendix A)."""
        result = compute_apr(100_000, 12, 24, self._charges(), instalment_override=self._STATED_EMI)
        assert abs(result.apr_interest_only_pct - 12.00) < TOL, (
            f"APR interest only = {result.apr_interest_only_pct:.4f}%, expected 12.00%"
        )

    def test_v2_net_disbursed_is_92500(self):
        """Net disbursed (all charges) = ₹92,500 (PRD Appendix A)."""
        result = compute_apr(100_000, 12, 24, self._charges(), instalment_override=self._STATED_EMI)
        assert abs(result.net_disbursed_all - 92_500.0) < 0.01, (
            f"Net disbursed = {result.net_disbursed_all}, expected 92,500"
        )

    def test_v2_net_disbursed_excl_tp_is_96500(self):
        """Net disbursed excl. third-party insurance = ₹96,500 (PRD Appendix A)."""
        result = compute_apr(100_000, 12, 24, self._charges(), instalment_override=self._STATED_EMI)
        assert abs(result.net_disbursed_excl_third_party - 96_500.0) < 0.01, (
            f"Net disbursed excl TP = {result.net_disbursed_excl_third_party}, expected 96,500"
        )

    def test_v2_total_repayable_is_112976_33(self):
        """Total repayable = ₹1,12,976.33 (PRD Appendix A).

        Uses the full-precision EMI (not the rounded display value) to reproduce
        the PRD figure exactly.  The PRD states ₹4,707.35 as a display value but
        the total ₹1,12,976.33 was computed from the unrounded EMI.
        """
        full_precision_emi = emi(100_000, 12, 24)   # 4707.347222...
        result = compute_apr(100_000, 12, 24, self._charges(), instalment_override=full_precision_emi)
        assert abs(result.total_repayable - 112_976.33) < 0.01, (
            f"Total repayable = {result.total_repayable:.2f}, expected 112,976.33"
        )

    def test_v2_r01_would_fail(self):
        """R-01 check: |19.99 − 15.6| = 4.39 pp >> 0.50 pp threshold → FAIL (PRD Appendix A)."""
        result = compute_apr(100_000, 12, 24, self._charges(), instalment_override=self._STATED_EMI)
        stated_apr = 15.6
        diff = abs(result.apr_all_charges_pct - stated_apr)
        assert diff > 0.50, (
            f"R-01 diff = {diff:.4f} pp; expected > 0.50 pp (PRD: |19.99 − 15.6| = 4.39 pp)"
        )

    def test_v2_r02_would_fire(self):
        """R-02 check: excl-TP APR 15.62% ≈ stated APR 15.6% → lender excluded insurance (PRD Appendix A)."""
        result = compute_apr(100_000, 12, 24, self._charges(), instalment_override=self._STATED_EMI)
        stated_apr = 15.6
        diff_excl_tp = abs(result.apr_excl_third_party_pct - stated_apr)
        assert diff_excl_tp < 0.10, (
            f"R-02: excl-TP diff = {diff_excl_tp:.4f} pp from stated 15.6%; expected < 0.10 pp"
        )
