"""Tests for the analysis and rule engine layers.

All tests are deterministic — no LLM, no network.

Coverage:
  - Golden vector 1 (RBI Annex B): R-01 pass, R-02 pass, R-13 in scope
  - Golden vector 1 with rounded EMI 970: R-01 still passes
  - Golden vector 2 (synthetic bad KFS): R-01 fail, R-02 fail, total repayable, fee impact
  - Truth-table tests for each rule (pass / warn/fail / not_stated cases)
  - Scope guard cases: credit card, sanction date before 2024-10-01
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional
import copy

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.models.schemas import (
    KFSExtraction,
    DocMeta,
    Lender,
    Loan,
    Rate,
    Instalment,
    Charge,
    ContingentCharge,
    ValidityPeriod,
    Grievance,
    FlagsPresent,
    EvidenceField,
)
from app.services.analysis import build_summary, compute_apr_variants
from app.services.rules import run_all, RuleResult, _r13_scope, _r01_apr_matches, _r02_third_party_in_apr, _r04_validity_period, _r05_proposal_no, _r06_apr_sheet_and_schedule, _r07_contingent_charges, _r10_emi_arithmetic, _r12_fee_impact


# ---------------------------------------------------------------------------
# Helpers — build KFSExtraction from dict
# ---------------------------------------------------------------------------

def _ef(value, status="found", page=1, quote="quote here", confidence=0.99):
    return EvidenceField(value=value, status=status, page=page, quote=quote, confidence=confidence)


def _ef_missing():
    return EvidenceField(status="not_found")


def _make_extraction(
    *,
    loan_category="retail",
    sanction_date="2024-10-15",
    is_digital_loan=False,
    principal: float,
    rate_pct: float,
    n: int,
    instalment: float,
    charges: list[dict] | None = None,
    stated_apr: float | None = None,
    proposal_no: str | None = "KFS-001",
    contingent_charges: list[dict] | None = None,
    validity_value: int | None = 3,
    validity_unit: str = "working_days",
    apr_sheet: bool = True,
    amo_sched: bool = True,
) -> KFSExtraction:
    """Build a minimal but complete KFSExtraction for testing."""

    charge_objs = []
    if charges:
        for c in charges:
            charge_objs.append(Charge(
                name=c.get("name", "Fee"),
                category=c.get("category", "processing"),
                payee=c.get("payee", "lender"),
                frequency=c.get("frequency", "one_time"),
                recurrence=c.get("recurrence"),
                amount_inr=c.get("amount_inr"),
                percent=c.get("percent"),
                percent_of=c.get("percent_of"),
            ))

    contingent_objs = []
    if contingent_charges:
        for cc in contingent_charges:
            contingent_objs.append(ContingentCharge(
                name=cc["name"], value=cc.get("value", "as applicable"), page=cc.get("page", 2)
            ))

    vp = None
    if validity_value is not None:
        vp = ValidityPeriod(value=validity_value, unit=validity_unit, page=1)

    return KFSExtraction(
        doc_meta=DocMeta(
            is_kfs=True,
            loan_category=loan_category,
            is_digital_loan=is_digital_loan,
            sanction_date=sanction_date,
            language="en",
        ),
        lender=Lender(name="Test Bank", type="bank"),
        proposal_no=_ef(proposal_no) if proposal_no else _ef_missing(),
        loan=Loan(
            sanctioned_amount_inr=_ef(principal),
            disbursal="upfront",
            term_months=_ef(n),
            instalments=[Instalment(
                type="EMI",
                count=_ef(n),
                amount_inr=_ef(instalment),
                first_due_after_days=_ef(30),
            )],
        ),
        rate=Rate(
            interest_rate_pct=_ef(rate_pct),
            type="fixed",
        ),
        charges=charge_objs,
        stated_apr_pct=_ef(stated_apr) if stated_apr is not None else _ef_missing(),
        contingent_charges=contingent_objs,
        validity_period=vp,
        cooling_off_days=_ef(3),
        grievance=Grievance(officer_name="Test Officer", phone="1800-xxx", email="test@bank.com"),
        flags_present=FlagsPresent(
            apr_computation_sheet=apr_sheet,
            amortisation_schedule=amo_sched,
        ),
    )


# ---------------------------------------------------------------------------
# Golden vector fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def rbi_annex_b_exact():
    """Vector 1 — RBI Annex B with exact EMI 969.73."""
    from app.services.apr import emi
    e = emi(20000, 15, 24)  # 969.732961
    return _make_extraction(
        principal=20000.0,
        rate_pct=15.0,
        n=24,
        instalment=round(e, 2),
        charges=[
            {"name": "Processing fee", "category": "processing", "payee": "lender", "amount_inr": 240.0},
            {"name": "Third-party fee", "category": "legal", "payee": "third_party", "amount_inr": 160.0},
        ],
        stated_apr=17.07,
        contingent_charges=[
            {"name": "Penal charges", "value": "2% p.m."},
            {"name": "Foreclosure charges", "value": "Nil"},
            {"name": "Switching charges", "value": "Nil"},
        ],
    )


@pytest.fixture
def rbi_annex_b_rounded():
    """Vector 1 — RBI Annex B with rounded EMI 970."""
    return _make_extraction(
        principal=20000.0,
        rate_pct=15.0,
        n=24,
        instalment=970.0,
        charges=[
            {"name": "Processing fee", "category": "processing", "payee": "lender", "amount_inr": 240.0},
            {"name": "Third-party fee", "category": "legal", "payee": "third_party", "amount_inr": 160.0},
        ],
        stated_apr=17.10,   # stated APR for the rounded EMI version
        contingent_charges=[
            {"name": "Penal charges", "value": "2% p.m."},
            {"name": "Foreclosure charges", "value": "Nil"},
            {"name": "Switching charges", "value": "Nil"},
        ],
    )


@pytest.fixture
def synthetic_bad_kfs():
    """Vector 2 — synthetic bad KFS (PRD Appendix A).

    The PRD states EMI ₹4,707.35 (2 dp display) but total repayable ₹1,12,976.33
    implies the unrounded value 4707.347222... was used in the sum.
    We pass the full-precision EMI so total_repayable matches ₹1,12,976.33 exactly.
    """
    from app.services.apr import emi
    e = emi(100000, 12, 24)  # 4707.347222... (full precision)
    return _make_extraction(
        principal=100000.0,
        rate_pct=12.0,
        n=24,
        instalment=e,   # full precision — matches PRD total repayable 112,976.33
        charges=[
            {"name": "Processing fee", "category": "processing", "payee": "lender", "amount_inr": 3000.0},
            {"name": "Documentation fee", "category": "documentation", "payee": "lender", "amount_inr": 500.0},
            {"name": "Insurance premium", "category": "insurance", "payee": "third_party", "amount_inr": 4000.0},
        ],
        stated_apr=15.6,   # deliberately excludes insurance
        contingent_charges=[
            {"name": "Penal charges", "value": "2% p.m."},
            {"name": "Foreclosure charges", "value": "2% on outstanding"},
            {"name": "Switching charges", "value": "Nil"},
        ],
    )


# ---------------------------------------------------------------------------
# Golden Vector 1 — RBI Annex B
# ---------------------------------------------------------------------------

class TestGoldenVector1:
    def test_r01_pass_exact_emi(self, rbi_annex_b_exact):
        """R-01 must pass: |17.07 − 17.07| ≈ 0."""
        output = run_all(rbi_annex_b_exact)
        r01 = next(r for r in output.rule_results if r.rule_id == "R-01")
        assert r01.status == "pass", f"R-01 should pass, got {r01.status}: {r01.plain_text_en}"

    def test_r01_pass_rounded_emi(self, rbi_annex_b_rounded):
        """R-01 must still pass with rounded EMI 970 (|17.10 − 17.10| ≈ 0)."""
        output = run_all(rbi_annex_b_rounded)
        r01 = next(r for r in output.rule_results if r.rule_id == "R-01")
        assert r01.status == "pass", f"R-01 should pass with rounded EMI, got {r01.status}"

    def test_r02_pass(self, rbi_annex_b_exact):
        """R-02 must pass: stated APR (17.07) is close to all-charges APR, not excl-TP only."""
        output = run_all(rbi_annex_b_exact)
        r02 = next(r for r in output.rule_results if r.rule_id == "R-02")
        assert r02.status == "pass", f"R-02 should pass, got {r02.status}: {r02.plain_text_en}"

    def test_r13_in_scope(self, rbi_annex_b_exact):
        """R-13 must be in scope (retail, sanction date 2024-10-15)."""
        output = run_all(rbi_annex_b_exact)
        r13 = next(r for r in output.rule_results if r.rule_id == "R-13")
        assert r13.status == "pass"
        assert output.in_scope is True

    def test_apr_variants_exact_emi(self, rbi_annex_b_exact):
        """APR variants should match PRD Appendix A within tolerance."""
        apr = compute_apr_variants(rbi_annex_b_exact)
        assert apr is not None
        assert abs(apr.all_charges_pct - 17.07) < 0.01
        assert abs(apr.interest_only_pct - 15.00) < 0.01
        assert abs(apr.excluding_third_party_pct - 16.24) < 0.02  # 16.235 expected


# ---------------------------------------------------------------------------
# Golden Vector 2 — Synthetic bad KFS
# ---------------------------------------------------------------------------

class TestGoldenVector2:
    def test_r01_fail(self, synthetic_bad_kfs):
        """R-01 must fail: |19.99 − 15.6| ≈ 4.39 pp >> 0.50 pp threshold."""
        output = run_all(synthetic_bad_kfs)
        r01 = next(r for r in output.rule_results if r.rule_id == "R-01")
        assert r01.status == "fail", f"R-01 should fail, got {r01.status}"

    def test_r02_fail(self, synthetic_bad_kfs):
        """R-02 must fail: stated 15.6% ≈ excl-TP APR 15.62%, not all-charges 19.99%."""
        output = run_all(synthetic_bad_kfs)
        r02 = next(r for r in output.rule_results if r.rule_id == "R-02")
        assert r02.status == "fail", f"R-02 should fail, got {r02.status}: {r02.plain_text_en}"

    def test_total_repayable(self, synthetic_bad_kfs):
        """Total repayable = ₹1,12,976.33 (per PRD Appendix A)."""
        summary = build_summary(synthetic_bad_kfs)
        assert summary is not None
        assert abs(summary.total_repayable - 112976.33) < 0.01

    def test_fee_impact_pp(self, synthetic_bad_kfs):
        """Fee impact ≈ 7.99 pp (19.99% − 12.00%)."""
        summary = build_summary(synthetic_bad_kfs)
        assert summary is not None
        assert abs(summary.fee_impact_pp - 7.99) < 0.01

    def test_total_charges_inr(self, synthetic_bad_kfs):
        """Total charges = ₹7,500."""
        summary = build_summary(synthetic_bad_kfs)
        assert summary is not None
        assert abs(summary.total_charges_inr - 7500.0) < 0.01

    def test_apr_all_charges(self, synthetic_bad_kfs):
        """All-charges APR ≈ 19.99%."""
        apr = compute_apr_variants(synthetic_bad_kfs)
        assert apr is not None
        assert abs(apr.all_charges_pct - 19.99) < 0.01

    def test_apr_excl_third_party(self, synthetic_bad_kfs):
        """Excl-TP APR ≈ 15.62%."""
        apr = compute_apr_variants(synthetic_bad_kfs)
        assert apr is not None
        assert abs(apr.excluding_third_party_pct - 15.62) < 0.01

    def test_apr_interest_only(self, synthetic_bad_kfs):
        """Interest-only APR = 12.00%."""
        apr = compute_apr_variants(synthetic_bad_kfs)
        assert apr is not None
        assert abs(apr.interest_only_pct - 12.00) < 0.01


# ---------------------------------------------------------------------------
# R-13 truth table
# ---------------------------------------------------------------------------

class TestR13ScopeGuard:
    def test_pass_retail(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               loan_category="retail", sanction_date="2024-10-15")
        r = _r13_scope(ext)
        assert r.status == "pass"

    def test_pass_msme(self):
        ext = _make_extraction(principal=50000, rate_pct=14, n=12, instalment=4500,
                               loan_category="msme", sanction_date="2025-01-01")
        r = _r13_scope(ext)
        assert r.status == "pass"

    def test_not_applicable_credit_card(self):
        ext = _make_extraction(principal=50000, rate_pct=36, n=12, instalment=5000,
                               loan_category="credit_card", sanction_date="2025-01-01")
        r = _r13_scope(ext)
        assert r.status == "not_applicable"
        assert "credit card" in r.plain_text_en.lower()

    def test_not_applicable_before_oct_2024(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               loan_category="retail", sanction_date="2024-09-30")
        r = _r13_scope(ext)
        assert r.status == "not_applicable"
        assert "2024-09-30" in r.plain_text_en

    def test_not_applicable_exactly_before_oct_2024(self):
        """1 Sep 2024 → out of scope."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               loan_category="retail", sanction_date="2024-01-01")
        r = _r13_scope(ext)
        assert r.status == "not_applicable"

    def test_pass_exactly_oct_2024(self):
        """1 Oct 2024 → in scope."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               loan_category="retail", sanction_date="2024-10-01")
        r = _r13_scope(ext)
        assert r.status == "pass"

    def test_not_stated_no_category(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               loan_category=None, sanction_date="2025-01-01")
        # Override loan_category to None
        ext.doc_meta.loan_category = None
        r = _r13_scope(ext)
        assert r.status == "not_stated"


# ---------------------------------------------------------------------------
# R-01 truth table
# ---------------------------------------------------------------------------

class TestR01APRMatches:
    def _run(self, principal, rate, n, inst, charges, stated_apr):
        ext = _make_extraction(principal=principal, rate_pct=rate, n=n,
                               instalment=inst, charges=charges, stated_apr=stated_apr)
        apr = compute_apr_variants(ext)
        return _r01_apr_matches(ext, apr)

    def test_pass_exact(self):
        """Difference ≤ 0.15 pp → pass."""
        from app.services.apr import emi
        e = emi(20000, 15, 24)
        # stated APR = computed APR (within 0.01 pp)
        r = self._run(20000, 15, 24, e,
                      [{"name": "Processing", "payee": "lender", "amount_inr": 240},
                       {"name": "TP fee", "payee": "third_party", "amount_inr": 160}],
                      17.07)
        assert r.status == "pass"

    def test_warn_small_diff(self):
        """Difference 0.15–0.50 pp → warn."""
        from app.services.apr import emi
        e = emi(20000, 15, 24)
        # stated APR = 16.80% (diff ≈ 0.27 pp from computed 17.07)
        r = self._run(20000, 15, 24, e,
                      [{"name": "Fee", "payee": "lender", "amount_inr": 240},
                       {"name": "TP", "payee": "third_party", "amount_inr": 160}],
                      16.80)
        assert r.status == "warn"

    def test_fail_large_diff(self):
        """Difference > 0.50 pp → fail."""
        from app.services.apr import emi
        e = emi(100000, 12, 24)
        r = self._run(100000, 12, 24, e,
                      [{"name": "Fee", "payee": "lender", "amount_inr": 3000},
                       {"name": "TP", "payee": "third_party", "amount_inr": 4000}],
                      15.6)  # stated vs computed ≈ 19.99 → diff 4.39 pp
        assert r.status == "fail"

    def test_not_stated_no_stated_apr(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               charges=[], stated_apr=None)
        ext.stated_apr_pct = _ef_missing()
        apr = compute_apr_variants(ext)
        r = _r01_apr_matches(ext, apr)
        assert r.status == "not_stated"

    def test_not_stated_no_apr_variants(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970, stated_apr=17.07)
        r = _r01_apr_matches(ext, None)
        assert r.status == "not_stated"


# ---------------------------------------------------------------------------
# R-02 truth table
# ---------------------------------------------------------------------------

class TestR02ThirdParty:
    def test_fail_excl_tp_matches_stated(self, synthetic_bad_kfs):
        """Synthetic bad KFS: stated 15.6% ≈ excl-TP 15.62% → fail."""
        apr = compute_apr_variants(synthetic_bad_kfs)
        r = _r02_third_party_in_apr(synthetic_bad_kfs, apr)
        assert r.status == "fail"

    def test_pass_all_charges_matches_stated(self, rbi_annex_b_exact):
        """RBI Annex B: stated 17.07% ≈ all-charges 17.07% → pass."""
        apr = compute_apr_variants(rbi_annex_b_exact)
        r = _r02_third_party_in_apr(rbi_annex_b_exact, apr)
        assert r.status == "pass"

    def test_not_applicable_no_third_party_charges(self):
        """No third-party charges → not_applicable (rule does not apply, not an error)."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               charges=[{"name": "Fee", "payee": "lender", "amount_inr": 240}],
                               stated_apr=15.5)
        apr = compute_apr_variants(ext)
        r = _r02_third_party_in_apr(ext, apr)
        assert r.status == "not_applicable"

    def test_not_stated_no_stated_apr(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               charges=[{"name": "TP", "payee": "third_party", "amount_inr": 160}],
                               stated_apr=None)
        ext.stated_apr_pct = _ef_missing()
        apr = compute_apr_variants(ext)
        r = _r02_third_party_in_apr(ext, apr)
        assert r.status == "not_stated"


# ---------------------------------------------------------------------------
# R-04 truth table
# ---------------------------------------------------------------------------

class TestR04ValidityPeriod:
    def test_pass_3_working_days(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               validity_value=3, validity_unit="working_days")
        r = _r04_validity_period(ext)
        assert r.status == "pass"

    def test_pass_more_than_3(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               validity_value=7, validity_unit="working_days")
        r = _r04_validity_period(ext)
        assert r.status == "pass"

    def test_fail_only_2_days(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               validity_value=2, validity_unit="working_days")
        r = _r04_validity_period(ext)
        assert r.status == "fail"

    def test_not_stated_missing(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               validity_value=None)
        r = _r04_validity_period(ext)
        assert r.status == "not_stated"

    def test_wording_never_illegal(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               validity_value=1, validity_unit="working_days")
        r = _r04_validity_period(ext)
        assert "illegal" not in r.plain_text_en.lower()
        assert "fraud" not in r.plain_text_en.lower()
        assert "inconsistent" in r.plain_text_en.lower()


# ---------------------------------------------------------------------------
# R-05 truth table
# ---------------------------------------------------------------------------

class TestR05ProposalNo:
    def test_pass_present(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               proposal_no="KFS-2024-001")
        r = _r05_proposal_no(ext)
        assert r.status == "pass"

    def test_warn_missing(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               proposal_no=None)
        r = _r05_proposal_no(ext)
        assert r.status == "warn"


# ---------------------------------------------------------------------------
# R-06 truth table
# ---------------------------------------------------------------------------

class TestR06AprSheetAndSchedule:
    def test_pass_both_present(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               apr_sheet=True, amo_sched=True)
        r = _r06_apr_sheet_and_schedule(ext)
        assert r.status == "pass"

    def test_warn_both_missing(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               apr_sheet=False, amo_sched=False)
        r = _r06_apr_sheet_and_schedule(ext)
        assert r.status == "warn"
        assert "APR computation sheet" in r.plain_text_en
        assert "amortisation schedule" in r.plain_text_en

    def test_warn_only_apr_sheet_missing(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               apr_sheet=False, amo_sched=True)
        r = _r06_apr_sheet_and_schedule(ext)
        assert r.status == "warn"


# ---------------------------------------------------------------------------
# R-07 truth table
# ---------------------------------------------------------------------------

class TestR07ContingentCharges:
    def test_pass_all_present(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               contingent_charges=[
                                   {"name": "Penal charges", "value": "2% p.m."},
                                   {"name": "Foreclosure charges", "value": "Nil"},
                                   {"name": "Switching charges", "value": "Nil"},
                               ])
        r = _r07_contingent_charges(ext)
        assert r.status == "pass"

    def test_warn_missing_penal(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               contingent_charges=[
                                   {"name": "Foreclosure charges", "value": "Nil"},
                                   {"name": "Switching charges", "value": "Nil"},
                               ])
        r = _r07_contingent_charges(ext)
        assert r.status == "warn"
        assert "penal" in r.plain_text_en.lower()

    def test_warn_nothing_stated(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               contingent_charges=[])
        r = _r07_contingent_charges(ext)
        assert r.status == "warn"


# ---------------------------------------------------------------------------
# R-10 truth table
# ---------------------------------------------------------------------------

class TestR10EMIArithmetic:
    def test_pass_exact_emi(self):
        """Computed EMI == stated EMI → pass."""
        from app.services.apr import emi
        e = round(emi(20000, 15, 24), 2)
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=e)
        r = _r10_emi_arithmetic(ext)
        assert r.status == "pass"

    def test_pass_rounded_emi(self):
        """Rounded EMI 970 vs computed 969.73 → diff < 1% → pass."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970.0)
        r = _r10_emi_arithmetic(ext)
        assert r.status == "pass", f"Expected pass, got {r.status}: {r.evidence}"

    def test_warn_large_discrepancy(self):
        """Stated EMI deliberately wrong by >1% → warn."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=800.0)
        r = _r10_emi_arithmetic(ext)
        assert r.status == "warn"

    def test_not_stated_missing_rate(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970)
        ext.rate.interest_rate_pct = _ef_missing()
        r = _r10_emi_arithmetic(ext)
        assert r.status == "not_stated"


# ---------------------------------------------------------------------------
# R-12 truth table
# ---------------------------------------------------------------------------

class TestR12FeeImpact:
    def test_info_present(self, synthetic_bad_kfs):
        apr = compute_apr_variants(synthetic_bad_kfs)
        r = _r12_fee_impact(synthetic_bad_kfs, apr)
        assert r.status == "pass"
        assert r.severity == "info"
        ev = r.evidence
        assert abs(ev["fee_impact_pp"] - 7.99) < 0.01
        assert abs(ev["total_charges_inr"] - 7500.0) < 0.01

    def test_not_stated_no_apr_variants(self):
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970)
        r = _r12_fee_impact(ext, None)
        assert r.status == "not_stated"


# ---------------------------------------------------------------------------
# Wording guard — no "illegal" or "fraud" in any rule output
# ---------------------------------------------------------------------------

class TestWordingGuard:
    _BANNED = ["illegal", "fraud", "violation of law", "criminal"]

    def _check_no_banned(self, result: RuleResult) -> None:
        for word in self._BANNED:
            assert word not in result.plain_text_en.lower(), (
                f"Rule {result.rule_id} contains banned word '{word}' in English text"
            )
            assert word not in result.plain_text_hi.lower(), (
                f"Rule {result.rule_id} contains banned word '{word}' in Hindi text"
            )

    def test_no_banned_words_in_any_rule(self, synthetic_bad_kfs):
        output = run_all(synthetic_bad_kfs)
        for result in output.rule_results:
            self._check_no_banned(result)


# ---------------------------------------------------------------------------
# run_all integration
# ---------------------------------------------------------------------------

class TestRunAll:
    def test_returns_all_p0_rules(self, rbi_annex_b_exact):
        output = run_all(rbi_annex_b_exact)
        ids = {r.rule_id for r in output.rule_results}
        expected = {"R-13", "R-01", "R-02", "R-04", "R-05", "R-06", "R-07", "R-10", "R-12"}
        assert ids == expected

    def test_in_scope_flag_set(self, rbi_annex_b_exact):
        output = run_all(rbi_annex_b_exact)
        assert output.in_scope is True

    def test_in_scope_false_for_credit_card(self):
        ext = _make_extraction(principal=50000, rate_pct=36, n=12, instalment=5000,
                               loan_category="credit_card", sanction_date="2025-01-01")
        output = run_all(ext)
        assert output.in_scope is False

    def test_summary_values_present(self, synthetic_bad_kfs):
        output = run_all(synthetic_bad_kfs)
        assert output.total_repayable is not None
        assert abs(output.total_repayable - 112976.33) < 0.01
        assert output.fee_impact_pp is not None
        assert abs(output.fee_impact_pp - 7.99) < 0.01


# ---------------------------------------------------------------------------
# ICICI-style real KFS fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def icici_kfs():
    """Real-world ICICI-style KFS fixture.

    Inputs (as observed on the document):
      Sanctioned amount : ₹12,00,000
      Tenure            : 60 months
      Interest rate     : 11.7% p.a. fixed
      Stated EMI        : ₹26,683
      Stated APR        : 12.28%
      Processing fee    : ₹7,999 (lender, one-time)
      Third-party charges: none

    Pre-computed actuals (verified before writing the test):
      Computed EMI      : ₹26,511.77  (diff 0.65% < 1% → R-10 pass)
      APR all charges   : 12.278%     (diff 0.002 pp from stated → R-01 pass)
      APR excl TP       : same as all charges (no third-party charges)
      R-02              : not_stated  (no third-party charges to check)
    """
    return _make_extraction(
        principal=1200000.0,
        rate_pct=11.7,
        n=60,
        instalment=26683.0,
        charges=[
            {"name": "Processing fee", "category": "processing",
             "payee": "lender", "frequency": "one_time", "amount_inr": 7999.0},
        ],
        stated_apr=12.28,
        contingent_charges=[
            {"name": "Penal charges", "value": "2% p.m."},
            {"name": "Foreclosure charges", "value": "4% on outstanding"},
            {"name": "Switching charges", "value": "Nil"},
        ],
        sanction_date="2024-11-01",
        loan_category="retail",
    )


class TestICICIKFS:
    """Tests against a real-world ICICI-style KFS fixture.

    Numbers were computed first (see fixture docstring) and the test
    assertions were written to match those computations — not the other
    way around.
    """

    def test_r01_pass(self, icici_kfs):
        """R-01: computed APR 12.278% vs stated 12.28% → diff 0.002 pp → pass."""
        output = run_all(icici_kfs)
        r01 = next(r for r in output.rule_results if r.rule_id == "R-01")
        assert r01.status == "pass", (
            f"R-01 should pass. Evidence: {r01.evidence}. Text: {r01.plain_text_en}"
        )

    def test_r02_not_triggered(self, icici_kfs):
        """R-02: no third-party charges → not_applicable (rule does not apply)."""
        output = run_all(icici_kfs)
        r02 = next(r for r in output.rule_results if r.rule_id == "R-02")
        assert r02.status == "not_applicable", (
            f"R-02 should be not_applicable with no third-party charges. "
            f"Got status={r02.status}. Evidence: {r02.evidence}"
        )

    def test_r10_pass(self, icici_kfs):
        """R-10: stated EMI 26683 vs computed 26511.77 → diff 0.65% ≤ 1% → pass."""
        output = run_all(icici_kfs)
        r10 = next(r for r in output.rule_results if r.rule_id == "R-10")
        assert r10.status == "pass", (
            f"R-10 should pass. Evidence: {r10.evidence}. Text: {r10.plain_text_en}"
        )

    def test_r13_in_scope(self, icici_kfs):
        """R-13: retail loan, sanction date 2024-11-01 → in scope."""
        output = run_all(icici_kfs)
        r13 = next(r for r in output.rule_results if r.rule_id == "R-13")
        assert r13.status == "pass"
        assert output.in_scope is True

    def test_apr_all_charges_value(self, icici_kfs):
        """Computed APR (all charges) should be ~12.28% (within 0.01 pp)."""
        apr = compute_apr_variants(icici_kfs)
        assert apr is not None
        assert abs(apr.all_charges_pct - 12.28) < 0.01, (
            f"APR all charges = {apr.all_charges_pct:.4f}%, expected ~12.28%"
        )


# ---------------------------------------------------------------------------
# Item 1 — R-01 boundary values
# ---------------------------------------------------------------------------

class TestR01Boundaries:
    """Exact boundary checks for the R-01 threshold ladder.

    ≤ 0.15 pp  → pass
    0.16 pp    → warn   (first value strictly above pass boundary)
    ≤ 0.50 pp  → warn
    0.51 pp    → fail   (first value strictly above warn boundary)
    """

    def _r01_for_diff(self, diff_pp: float) -> RuleResult:
        """Build a minimal extraction whose computed-vs-stated APR difference equals diff_pp,
        then run _r01_apr_matches and return the result.

        Uses a no-charges loan so computed APR == interest_rate exactly.
        `stated_apr` is rounded to 6 dp before storing to avoid floating-point
        representation errors (e.g. 12.0 + 0.15 = 12.150000000000036 in IEEE 754).
        """
        from app.services.apr import emi as _emi
        principal, rate, n = 50000.0, 12.0, 24
        inst = _emi(principal, rate, n)
        stated = round(rate + diff_pp, 6)   # round to avoid IEEE 754 overshoot
        ext = _make_extraction(principal=principal, rate_pct=rate, n=n,
                               instalment=inst, charges=[], stated_apr=stated)
        apr = compute_apr_variants(ext)
        return _r01_apr_matches(ext, apr)

    def test_exactly_015_is_pass(self):
        """diff = 0.15 pp → pass (boundary inclusive)."""
        r = self._r01_for_diff(0.15)
        assert r.status == "pass", f"Expected pass at 0.15 pp, got {r.status} (evidence={r.evidence})"

    def test_016_is_warn(self):
        """diff = 0.16 pp → warn (just above pass boundary)."""
        r = self._r01_for_diff(0.16)
        assert r.status == "warn", f"Expected warn at 0.16 pp, got {r.status} (evidence={r.evidence})"

    def test_exactly_050_is_warn(self):
        """diff = 0.50 pp → warn (boundary inclusive)."""
        r = self._r01_for_diff(0.50)
        assert r.status == "warn", f"Expected warn at 0.50 pp, got {r.status} (evidence={r.evidence})"

    def test_051_is_fail(self):
        """diff = 0.51 pp → fail (just above warn boundary)."""
        r = self._r01_for_diff(0.51)
        assert r.status == "fail", f"Expected fail at 0.51 pp, got {r.status} (evidence={r.evidence})"


# ---------------------------------------------------------------------------
# Item 2 — build_cashflows / build_charge_items behaviour
# ---------------------------------------------------------------------------

class TestBuildCashflows:
    """Tests for build_charge_items (t0) and per-period recurring charges."""

    def test_t0_lender_and_tp_charges_deducted(self):
        """Net disbursed = sanctioned - upfront lender charges - upfront TP charges."""
        ext = _make_extraction(
            principal=100000.0, rate_pct=12.0, n=24, instalment=4707.35,
            charges=[
                {"name": "Processing", "payee": "lender",      "frequency": "one_time", "amount_inr": 3000.0},
                {"name": "Insurance",  "payee": "third_party",  "frequency": "one_time", "amount_inr": 4000.0},
            ],
        )
        from app.services.analysis import build_charge_items, compute_apr_variants
        items = build_charge_items(ext)
        assert len(items) == 2
        upfront = sum(i.amount_inr for i in items if i.frequency == "one_time")
        assert abs(upfront - 7000.0) < 0.01  # 3000 + 4000

        apr = compute_apr_variants(ext)
        assert apr is not None
        assert abs(apr.apr_result.net_disbursed_all - 93000.0) < 0.01  # 100000 - 7000

    def test_t0_only_lender_charges_for_excl_tp(self):
        """Excl-TP net disbursed = sanctioned - lender charges only."""
        ext = _make_extraction(
            principal=100000.0, rate_pct=12.0, n=24, instalment=4707.35,
            charges=[
                {"name": "Processing", "payee": "lender",      "frequency": "one_time", "amount_inr": 3000.0},
                {"name": "Insurance",  "payee": "third_party",  "frequency": "one_time", "amount_inr": 4000.0},
            ],
        )
        apr = compute_apr_variants(ext)
        assert apr is not None
        assert abs(apr.apr_result.net_disbursed_excl_third_party - 97000.0) < 0.01  # 100000 - 3000

    def test_recurring_monthly_charge_in_every_period(self):
        """A monthly recurring charge must increase all-charges APR above interest-only APR."""
        ext = _make_extraction(
            principal=50000.0, rate_pct=10.0, n=12, instalment=4395.0,
            charges=[
                {"name": "Monthly protection",
                 "payee": "lender", "frequency": "recurring",
                 "recurrence": "monthly", "amount_inr": 200.0},
            ],
        )
        from app.services.analysis import build_charge_items
        items = build_charge_items(ext)
        assert len(items) == 1
        assert items[0].frequency == "recurring"
        assert items[0].recurrence == "monthly"

        apr = compute_apr_variants(ext)
        assert apr is not None
        assert apr.all_charges_pct > apr.interest_only_pct, (
            "Monthly recurring charge should push all-charges APR above interest-only APR"
        )

    def test_recurring_yearly_charge_lands_in_correct_period(self):
        """A yearly recurring charge (start period 1) affects period 1 and period 13 for n=24."""
        from app.services.apr import ChargeItem, _build_cash_flows
        inst = 5000.0
        n = 24
        charges = [ChargeItem(amount_inr=1200.0, payee="lender",
                              frequency="recurring", recurrence="yearly", start_period=1)]
        _, cfs = _build_cash_flows(inst, n, charges, {"lender"})
        # Period 1 (index 0) and period 13 (index 12) should have the extra charge
        assert abs(cfs[0]  - (inst + 1200.0)) < 0.01, f"Period 1 CF = {cfs[0]}, expected {inst+1200}"
        assert abs(cfs[12] - (inst + 1200.0)) < 0.01, f"Period 13 CF = {cfs[12]}, expected {inst+1200}"
        # All other periods are just the instalment
        for i, cf in enumerate(cfs):
            if i not in (0, 12):
                assert abs(cf - inst) < 0.01, f"Period {i+1} CF = {cf}, expected {inst}"

    def test_no_charges_gives_interest_only(self):
        """With no charges, net_disbursed_all = sanctioned_amount."""
        ext = _make_extraction(principal=20000.0, rate_pct=15.0, n=24, instalment=970.0,
                               charges=[])
        apr = compute_apr_variants(ext)
        assert apr is not None
        assert abs(apr.apr_result.net_disbursed_all - 20000.0) < 0.01


# ---------------------------------------------------------------------------
# Item 3 — stamp duty toggle
# ---------------------------------------------------------------------------

class TestStampDutyToggle:
    def _ext_with_stamp_duty(self):
        return _make_extraction(
            principal=100000.0, rate_pct=12.0, n=24, instalment=4707.35,
            charges=[
                {"name": "Processing fee", "category": "processing",
                 "payee": "lender", "frequency": "one_time", "amount_inr": 2000.0},
                {"name": "Stamp duty",     "category": "stamp_duty",
                 "payee": "lender", "frequency": "one_time", "amount_inr": 500.0},
            ],
        )

    def test_default_excludes_stamp_duty_from_apr(self):
        """Default (include_stamp_duty=False): stamp duty not deducted from net disbursed."""
        from app.services.analysis import build_charge_items, compute_apr_variants
        ext = self._ext_with_stamp_duty()
        items = build_charge_items(ext, include_stamp_duty=False)
        # Only processing fee; stamp duty filtered out
        names = [i.amount_inr for i in items]
        assert len(items) == 1, f"Expected 1 item (no stamp duty), got {len(items)}"
        assert abs(items[0].amount_inr - 2000.0) < 0.01

        # APR should use net disbursed = 100000 - 2000 = 98000
        apr = compute_apr_variants(ext, include_stamp_duty=False)
        assert apr is not None
        assert abs(apr.apr_result.net_disbursed_all - 98000.0) < 0.01

    def test_include_stamp_duty_includes_it_in_apr(self):
        """include_stamp_duty=True: stamp duty deducted from net disbursed."""
        from app.services.analysis import build_charge_items, compute_apr_variants
        ext = self._ext_with_stamp_duty()
        items = build_charge_items(ext, include_stamp_duty=True)
        assert len(items) == 2

        apr = compute_apr_variants(ext, include_stamp_duty=True)
        assert apr is not None
        # Net disbursed = 100000 - 2000 - 500 = 97500
        assert abs(apr.apr_result.net_disbursed_all - 97500.0) < 0.01

    def test_including_stamp_duty_raises_apr(self):
        """Including stamp duty deducts more from net disbursed → higher APR."""
        from app.services.analysis import compute_apr_variants
        ext = self._ext_with_stamp_duty()
        apr_excl = compute_apr_variants(ext, include_stamp_duty=False)
        apr_incl = compute_apr_variants(ext, include_stamp_duty=True)
        assert apr_incl.all_charges_pct > apr_excl.all_charges_pct

    def test_stamp_duty_assumption_recorded_when_present(self):
        """build_summary records stamp_duty_assumption note when stamp duty is in the charges."""
        from app.services.analysis import build_summary
        ext = self._ext_with_stamp_duty()
        summary = build_summary(ext, include_stamp_duty=False)
        assert summary is not None
        assert summary.stamp_duty_assumption != "", (
            "stamp_duty_assumption should be non-empty when stamp duty charge is present"
        )
        assert "stamp duty" in summary.stamp_duty_assumption.lower()

    def test_no_stamp_duty_assumption_when_absent(self):
        """stamp_duty_assumption is empty when no stamp duty charge exists."""
        from app.services.analysis import build_summary
        ext = _make_extraction(principal=50000.0, rate_pct=10.0, n=12, instalment=4395.0,
                               charges=[{"name": "Processing", "payee": "lender",
                                         "amount_inr": 500.0, "category": "processing"}])
        summary = build_summary(ext)
        assert summary is not None
        assert summary.stamp_duty_assumption == ""


# ---------------------------------------------------------------------------
# Item 4 — R-02 not_applicable vs not_stated distinction
# ---------------------------------------------------------------------------

class TestR02NotApplicableVsNotStated:
    def test_not_applicable_when_no_tp_charges(self):
        """No third-party charges → not_applicable (nothing to evaluate)."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               charges=[{"name": "Fee", "payee": "lender", "amount_inr": 240}],
                               stated_apr=15.5)
        apr = compute_apr_variants(ext)
        r = _r02_third_party_in_apr(ext, apr)
        assert r.status == "not_applicable"

    def test_not_applicable_even_when_stated_apr_missing(self):
        """No TP charges → not_applicable regardless of whether stated APR is present."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               charges=[{"name": "Fee", "payee": "lender", "amount_inr": 240}],
                               stated_apr=None)
        ext.stated_apr_pct = _ef_missing()
        apr = compute_apr_variants(ext)
        r = _r02_third_party_in_apr(ext, apr)
        assert r.status == "not_applicable"

    def test_not_stated_when_tp_charges_present_but_stated_apr_missing(self):
        """TP charges present but stated APR missing → not_stated (can't evaluate)."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               charges=[{"name": "TP", "payee": "third_party", "amount_inr": 160}],
                               stated_apr=None)
        ext.stated_apr_pct = _ef_missing()
        apr = compute_apr_variants(ext)
        r = _r02_third_party_in_apr(ext, apr)
        assert r.status == "not_stated"

    def test_not_applicable_text_mentions_third_party(self):
        """not_applicable plain text should explain that no third-party charges exist."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970,
                               charges=[], stated_apr=15.0)
        apr = compute_apr_variants(ext)
        r = _r02_third_party_in_apr(ext, apr)
        assert r.status == "not_applicable"
        assert "third" in r.plain_text_en.lower() or "third-party" in r.plain_text_en.lower()


# ---------------------------------------------------------------------------
# Item 5 — R-10 plain-text contains first-instalment note
# ---------------------------------------------------------------------------

class TestR10FirstInstalmentNote:
    _NOTE_FRAGMENT = "first instalment"

    def test_pass_text_contains_first_instalment_note(self):
        """R-10 pass text should mention the first instalment timing caveat."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=970.0)
        r = _r10_emi_arithmetic(ext)
        assert r.status == "pass"
        assert self._NOTE_FRAGMENT in r.plain_text_en.lower(), (
            f"Expected '{self._NOTE_FRAGMENT}' in pass text. Got: {r.plain_text_en}"
        )

    def test_warn_text_contains_first_instalment_note(self):
        """R-10 warn text should also mention the first instalment timing caveat."""
        ext = _make_extraction(principal=20000, rate_pct=15, n=24, instalment=800.0)
        r = _r10_emi_arithmetic(ext)
        assert r.status == "warn"
        assert self._NOTE_FRAGMENT in r.plain_text_en.lower(), (
            f"Expected '{self._NOTE_FRAGMENT}' in warn text. Got: {r.plain_text_en}"
        )
