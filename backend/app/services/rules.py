"""Rule engine — deterministic checks over KFSExtraction.

No LLM calls.  Every rule result carries:
  rule_id, status, severity, evidence, citation, plain_text_en, plain_text_hi

Statuses: pass | warn | fail | not_applicable | not_stated
Severity: high | medium | low | info | not_applicable

Wording rule: flags say "inconsistent with the KFS requirements" or
"not stated in the document" — never "illegal", "fraud", or "violation of law".

P0 rules implemented: R-13, R-01, R-02, R-04, R-05, R-06, R-07, R-10, R-12.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from ..models.schemas import KFSExtraction
from .analysis import APRVariants, build_summary, compute_apr_variants
from .apr import emi as compute_emi

# ---------------------------------------------------------------------------
# Load rule metadata (citations, templates)
# ---------------------------------------------------------------------------

_RULES_JSON_PATH = Path(__file__).parent.parent / "data" / "rbi_kfs_rules.json"
_RULE_META: dict[str, dict] = {}

try:
    _raw = json.loads(_RULES_JSON_PATH.read_text(encoding="utf-8"))
    _RULE_META = {r["id"]: r for r in _raw}
except Exception:
    pass  # tests mock the meta if needed; production must have the file


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

_SCOPE_EFFECTIVE_DATE = date(2024, 10, 1)


@dataclass
class RuleResult:
    rule_id: str
    status: str          # pass | warn | fail | not_applicable | not_stated
    severity: str        # high | medium | low | info | not_applicable
    evidence: dict[str, Any]
    citation: str
    plain_text_en: str
    plain_text_hi: str


@dataclass
class AnalysisOutput:
    rule_results: list[RuleResult]
    apr_variants: Optional[APRVariants]
    total_repayable: Optional[float]
    total_interest: Optional[float]
    total_charges_inr: Optional[float]
    fee_impact_pp: Optional[float]
    charge_pct_of_loan: Optional[float]
    in_scope: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta(rule_id: str) -> dict:
    return _RULE_META.get(rule_id, {})


def _citation(rule_id: str) -> str:
    return _meta(rule_id).get("citation", "RBI/2024-25/18")


def _severity(rule_id: str) -> str:
    return _meta(rule_id).get("severity", "medium")


def _tmpl(rule_id: str, key: str) -> str:
    return _meta(rule_id).get("templates", {}).get(key, "")


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


def _r13_scope(extraction: KFSExtraction) -> RuleResult:
    """R-13: scope guard — retail/MSME term loan, sanctioned ≥ 1 Oct 2024."""
    meta = _meta("R-13")
    citation = _citation("R-13")
    evidence: dict[str, Any] = {
        "loan_category": extraction.doc_meta.loan_category,
        "sanction_date": extraction.doc_meta.sanction_date,
    }

    # Credit card → always out of scope
    if extraction.doc_meta.loan_category == "credit_card":
        reason = "credit card products are not covered by RBI/2024-25/18"
        return RuleResult(
            rule_id="R-13",
            status="not_applicable",
            severity="not_applicable",
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-13", "fail_en").format(reason=reason),
            plain_text_hi=_tmpl("R-13", "fail_hi").format(reason=reason),
        )

    # Category unknown
    if extraction.doc_meta.loan_category is None:
        return RuleResult(
            rule_id="R-13",
            status="not_stated",
            severity="not_applicable",
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-13", "not_stated_en"),
            plain_text_hi=_tmpl("R-13", "not_stated_hi"),
        )

    # Sanction date check
    sanction_date_str = extraction.doc_meta.sanction_date
    if sanction_date_str:
        try:
            sd = date.fromisoformat(sanction_date_str)
            if sd < _SCOPE_EFFECTIVE_DATE:
                reason = f"sanction date {sanction_date_str} is before 1 Oct 2024"
                return RuleResult(
                    rule_id="R-13",
                    status="not_applicable",
                    severity="not_applicable",
                    evidence=evidence,
                    citation=citation,
                    plain_text_en=_tmpl("R-13", "fail_en").format(reason=reason),
                    plain_text_hi=_tmpl("R-13", "fail_hi").format(reason=reason),
                )
        except ValueError:
            pass  # unparseable date — don't block

    loan_cat = extraction.doc_meta.loan_category
    return RuleResult(
        rule_id="R-13",
        status="pass",
        severity="not_applicable",
        evidence=evidence,
        citation=citation,
        plain_text_en=_tmpl("R-13", "pass_en").format(loan_category=loan_cat),
        plain_text_hi=_tmpl("R-13", "pass_hi").format(loan_category=loan_cat),
    )


def _r01_apr_matches(
    extraction: KFSExtraction, apr_variants: Optional[APRVariants]
) -> RuleResult:
    """R-01: |computed APR − stated APR| ≤0.15 pass; ≤0.50 warn; >0.50 fail."""
    citation = _citation("R-01")
    stated = extraction.stated_apr_pct.value

    if stated is None or apr_variants is None:
        return RuleResult(
            rule_id="R-01",
            status="not_stated",
            severity=_severity("R-01"),
            evidence={"stated_apr": stated},
            citation=citation,
            plain_text_en=_tmpl("R-01", "not_stated_en"),
            plain_text_hi=_tmpl("R-01", "not_stated_hi"),
        )

    computed = apr_variants.all_charges_pct
    diff = abs(computed - float(stated))
    diff_rounded = round(diff, 4)   # round to 4 dp; comparisons use rounded value
    evidence = {
        "stated_apr": stated,
        "computed_apr": computed,
        "diff_pp": diff_rounded,
    }

    if diff_rounded <= 0.15:
        return RuleResult(
            rule_id="R-01",
            status="pass",
            severity=_severity("R-01"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-01", "pass_en").format(
                computed_apr=computed, stated_apr=stated, diff=diff
            ),
            plain_text_hi=_tmpl("R-01", "pass_hi").format(
                computed_apr=computed, stated_apr=stated, diff=diff
            ),
        )
    elif diff_rounded <= 0.50:
        return RuleResult(
            rule_id="R-01",
            status="warn",
            severity=_severity("R-01"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-01", "warn_en").format(
                computed_apr=computed, stated_apr=stated, diff=diff
            ),
            plain_text_hi=_tmpl("R-01", "warn_hi").format(
                computed_apr=computed, stated_apr=stated, diff=diff
            ),
        )
    else:
        return RuleResult(
            rule_id="R-01",
            status="fail",
            severity=_severity("R-01"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-01", "fail_en").format(
                computed_apr=computed, stated_apr=stated, diff=diff
            ),
            plain_text_hi=_tmpl("R-01", "fail_hi").format(
                computed_apr=computed, stated_apr=stated, diff=diff
            ),
        )


def _r02_third_party_in_apr(
    extraction: KFSExtraction, apr_variants: Optional[APRVariants]
) -> RuleResult:
    """R-02: fail if stated APR matches excl-TP variant but not all-charges variant.

    Distinct early-exit statuses:
      not_applicable — no third-party charges in this KFS (nothing to check)
      not_stated     — third-party charges exist but stated APR or APR variants unavailable
    """
    citation = _citation("R-02")
    stated = extraction.stated_apr_pct.value

    has_tp = any(c.payee == "third_party" and c.amount_inr for c in extraction.charges)

    # No third-party charges at all → rule is not applicable for this document
    if not has_tp:
        return RuleResult(
            rule_id="R-02",
            status="not_applicable",
            severity=_severity("R-02"),
            evidence={"has_third_party_charges": False},
            citation=citation,
            plain_text_en=_tmpl("R-02", "not_applicable_en"),
            plain_text_hi=_tmpl("R-02", "not_applicable_hi"),
        )

    # Third-party charges exist but we lack a stated APR or computed variants
    if stated is None or apr_variants is None:
        return RuleResult(
            rule_id="R-02",
            status="not_stated",
            severity=_severity("R-02"),
            evidence={"stated_apr": stated, "has_third_party_charges": True},
            citation=citation,
            plain_text_en=_tmpl("R-02", "not_stated_en"),
            plain_text_hi=_tmpl("R-02", "not_stated_hi"),
        )

    all_charges = apr_variants.all_charges_pct
    excl_tp = apr_variants.excluding_third_party_pct
    diff_all = abs(float(stated) - all_charges)
    diff_excl = abs(float(stated) - excl_tp)

    evidence = {
        "stated_apr": stated,
        "all_charges_apr": all_charges,
        "excl_tp_apr": excl_tp,
        "diff_all_pp": round(diff_all, 4),
        "diff_excl_pp": round(diff_excl, 4),
    }

    # Fail condition: matches excl-TP within 0.15 pp AND does NOT match all-charges
    if diff_excl <= 0.15 and diff_all > 0.15:
        return RuleResult(
            rule_id="R-02",
            status="fail",
            severity=_severity("R-02"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-02", "fail_en").format(
                stated_apr=stated,
                excl_tp_apr=excl_tp,
                all_charges_apr=all_charges,
            ),
            plain_text_hi=_tmpl("R-02", "fail_hi").format(
                stated_apr=stated,
                excl_tp_apr=excl_tp,
                all_charges_apr=all_charges,
            ),
        )
    else:
        return RuleResult(
            rule_id="R-02",
            status="pass",
            severity=_severity("R-02"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-02", "pass_en").format(stated_apr=stated),
            plain_text_hi=_tmpl("R-02", "pass_hi").format(stated_apr=stated),
        )


def _r04_validity_period(extraction: KFSExtraction) -> RuleResult:
    """R-04: validity period ≥ 3 working days (for term ≥ 7 days)."""
    citation = _citation("R-04")
    vp = extraction.validity_period

    # Skip check if term < 7 days (very short loans)
    term = extraction.loan.term_months.value
    if term is not None and int(term) * 30 < 7:
        return RuleResult(
            rule_id="R-04",
            status="not_applicable",
            severity=_severity("R-04"),
            evidence={"term_months": term},
            citation=citation,
            plain_text_en="Loan term < 7 days — validity period rule not applicable.",
            plain_text_hi="ऋण अवधि < 7 दिन — वैधता अवधि नियम लागू नहीं।",
        )

    if vp is None or vp.value is None:
        return RuleResult(
            rule_id="R-04",
            status="not_stated",
            severity=_severity("R-04"),
            evidence={"validity_period": None},
            citation=citation,
            plain_text_en=_tmpl("R-04", "not_stated_en"),
            plain_text_hi=_tmpl("R-04", "not_stated_hi"),
        )

    # Convert to working days for comparison: calendar_days / 1.4 ≈ working days (rough)
    val = vp.value
    unit = vp.unit or "working_days"
    wd_val = val if unit == "working_days" else math.ceil(val / 1.4)

    evidence = {"value": val, "unit": unit, "working_days_equivalent": wd_val}

    if wd_val >= 3:
        return RuleResult(
            rule_id="R-04",
            status="pass",
            severity=_severity("R-04"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-04", "pass_en").format(value=val, unit=unit),
            plain_text_hi=_tmpl("R-04", "pass_hi").format(value=val, unit=unit),
        )
    else:
        return RuleResult(
            rule_id="R-04",
            status="fail",
            severity=_severity("R-04"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-04", "fail_en").format(value=val, unit=unit),
            plain_text_hi=_tmpl("R-04", "fail_hi").format(value=val, unit=unit),
        )


def _r05_proposal_no(extraction: KFSExtraction) -> RuleResult:
    """R-05: unique proposal number present."""
    citation = _citation("R-05")
    prop_no = extraction.proposal_no.value

    if prop_no:
        return RuleResult(
            rule_id="R-05",
            status="pass",
            severity=_severity("R-05"),
            evidence={"proposal_no": prop_no},
            citation=citation,
            plain_text_en=_tmpl("R-05", "pass_en").format(proposal_no=prop_no),
            plain_text_hi=_tmpl("R-05", "pass_hi").format(proposal_no=prop_no),
        )
    else:
        return RuleResult(
            rule_id="R-05",
            status="warn",
            severity=_severity("R-05"),
            evidence={"proposal_no": None},
            citation=citation,
            plain_text_en=_tmpl("R-05", "warn_en"),
            plain_text_hi=_tmpl("R-05", "warn_hi"),
        )


def _r06_apr_sheet_and_schedule(extraction: KFSExtraction) -> RuleResult:
    """R-06: APR computation sheet and amortisation schedule present."""
    citation = _citation("R-06")
    flags = extraction.flags_present
    apr_sheet = flags.apr_computation_sheet
    amo_sched = flags.amortisation_schedule

    missing = []
    if not apr_sheet:
        missing.append("APR computation sheet")
    if not amo_sched:
        missing.append("amortisation schedule")

    evidence = {
        "apr_computation_sheet": apr_sheet,
        "amortisation_schedule": amo_sched,
    }

    if not missing:
        return RuleResult(
            rule_id="R-06",
            status="pass",
            severity=_severity("R-06"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-06", "pass_en"),
            plain_text_hi=_tmpl("R-06", "pass_hi"),
        )
    else:
        missing_str = " and ".join(missing)
        return RuleResult(
            rule_id="R-06",
            status="warn",
            severity=_severity("R-06"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-06", "warn_en").format(missing_items=missing_str),
            plain_text_hi=_tmpl("R-06", "warn_hi").format(missing_items=missing_str),
        )


_EXPECTED_CONTINGENT = {"penal", "foreclosure", "prepayment", "switching"}


def _r07_contingent_charges(extraction: KFSExtraction) -> RuleResult:
    """R-07: penal / foreclosure / switching charges disclosed."""
    citation = _citation("R-07")
    stated_names = {c.name.lower() for c in extraction.contingent_charges}

    def _present(keyword: str) -> bool:
        return any(keyword in n for n in stated_names)

    missing = []
    if not _present("penal") and not _present("late"):
        missing.append("penal charges")
    if not _present("foreclos") and not _present("prepay") and not _present("pre-pay"):
        missing.append("foreclosure/prepayment charges")
    if not _present("switch"):
        missing.append("switching charges")

    evidence = {
        "contingent_charges_found": [c.name for c in extraction.contingent_charges],
        "missing": missing,
    }

    if not missing:
        return RuleResult(
            rule_id="R-07",
            status="pass",
            severity=_severity("R-07"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-07", "pass_en"),
            plain_text_hi=_tmpl("R-07", "pass_hi"),
        )
    else:
        missing_str = "; ".join(missing)
        return RuleResult(
            rule_id="R-07",
            status="warn",
            severity=_severity("R-07"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-07", "warn_en").format(missing_charges=missing_str),
            plain_text_hi=_tmpl("R-07", "warn_hi").format(missing_charges=missing_str),
        )


def _r10_emi_arithmetic(extraction: KFSExtraction) -> RuleResult:
    """R-10: recomputed EMI vs stated EMI — warn if difference > 1%."""
    citation = _citation("R-10")
    principal = extraction.loan.sanctioned_amount_inr.value
    rate_pct = extraction.rate.interest_rate_pct.value
    n = extraction.loan.term_months.value

    stated_emi: Optional[float] = None
    if extraction.loan.instalments:
        stated_emi = extraction.loan.instalments[0].amount_inr.value

    if any(v is None for v in [principal, rate_pct, n, stated_emi]):
        return RuleResult(
            rule_id="R-10",
            status="not_stated",
            severity=_severity("R-10"),
            evidence={"principal": principal, "rate_pct": rate_pct, "n": n, "stated_emi": stated_emi},
            citation=citation,
            plain_text_en=_tmpl("R-10", "not_stated_en"),
            plain_text_hi=_tmpl("R-10", "not_stated_hi"),
        )

    computed = compute_emi(float(principal), float(rate_pct), int(n))
    diff_pct = abs(computed - float(stated_emi)) / computed * 100 if computed else 0.0
    evidence = {
        "stated_emi": stated_emi,
        "computed_emi": round(computed, 4),
        "diff_pct": round(diff_pct, 4),
    }

    if diff_pct <= 1.0:
        return RuleResult(
            rule_id="R-10",
            status="pass",
            severity=_severity("R-10"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-10", "pass_en").format(
                stated_emi=stated_emi, computed_emi=computed, diff_pct=diff_pct
            ),
            plain_text_hi=_tmpl("R-10", "pass_hi").format(
                stated_emi=stated_emi, computed_emi=computed, diff_pct=diff_pct
            ),
        )
    else:
        return RuleResult(
            rule_id="R-10",
            status="warn",
            severity=_severity("R-10"),
            evidence=evidence,
            citation=citation,
            plain_text_en=_tmpl("R-10", "warn_en").format(
                stated_emi=stated_emi, computed_emi=computed, diff_pct=diff_pct
            ),
            plain_text_hi=_tmpl("R-10", "warn_hi").format(
                stated_emi=stated_emi, computed_emi=computed, diff_pct=diff_pct
            ),
        )


def _r12_fee_impact(
    extraction: KFSExtraction, apr_variants: Optional[APRVariants]
) -> RuleResult:
    """R-12: informational fee-impact insight."""
    citation = _citation("R-12")
    principal = extraction.loan.sanctioned_amount_inr.value
    rate_pct = extraction.rate.interest_rate_pct.value

    if apr_variants is None or principal is None or rate_pct is None:
        return RuleResult(
            rule_id="R-12",
            status="not_stated",
            severity="info",
            evidence={},
            citation=citation,
            plain_text_en="Fee impact cannot be computed — essential fields not stated.",
            plain_text_hi="आवश्यक फ़ील्ड नहीं बताई गई — शुल्क प्रभाव की गणना नहीं की जा सकती।",
        )

    total_charges = sum(
        c.amount_inr for c in extraction.charges if c.amount_inr is not None
    )
    fee_impact_pp = apr_variants.all_charges_pct - float(rate_pct)
    charge_pct = (total_charges / float(principal) * 100) if principal else 0.0

    evidence = {
        "fee_impact_pp": round(fee_impact_pp, 4),
        "total_charges_inr": total_charges,
        "charge_pct_of_loan": round(charge_pct, 4),
        "all_charges_apr": apr_variants.all_charges_pct,
        "interest_only_apr": apr_variants.interest_only_pct,
    }

    return RuleResult(
        rule_id="R-12",
        status="pass",
        severity="info",
        evidence=evidence,
        citation=citation,
        plain_text_en=_tmpl("R-12", "info_en").format(
            fee_impact_pp=fee_impact_pp,
            total_charges=total_charges,
            charge_pct_of_loan=charge_pct,
        ),
        plain_text_hi=_tmpl("R-12", "info_hi").format(
            fee_impact_pp=fee_impact_pp,
            total_charges=total_charges,
            charge_pct_of_loan=charge_pct,
        ),
    )


# ---------------------------------------------------------------------------
# run_all
# ---------------------------------------------------------------------------


def run_all(extraction: KFSExtraction) -> AnalysisOutput:
    """Run all P0 rules and return results plus summary metrics."""
    apr_variants = compute_apr_variants(extraction)
    summary = build_summary(extraction)

    r13 = _r13_scope(extraction)
    in_scope = r13.status == "pass"

    results = [
        r13,
        _r01_apr_matches(extraction, apr_variants),
        _r02_third_party_in_apr(extraction, apr_variants),
        _r04_validity_period(extraction),
        _r05_proposal_no(extraction),
        _r06_apr_sheet_and_schedule(extraction),
        _r07_contingent_charges(extraction),
        _r10_emi_arithmetic(extraction),
        _r12_fee_impact(extraction, apr_variants),
    ]

    return AnalysisOutput(
        rule_results=results,
        apr_variants=apr_variants,
        total_repayable=summary.total_repayable if summary else None,
        total_interest=summary.total_interest if summary else None,
        total_charges_inr=summary.total_charges_inr if summary else None,
        fee_impact_pp=summary.fee_impact_pp if summary else None,
        charge_pct_of_loan=summary.charge_pct_of_loan if summary else None,
        in_scope=in_scope,
    )
