"""Tests for the extraction pipeline.

Gemini is fully mocked — no network calls.
Tests:
  1. scope_guard returns in_scope=False for a non-KFS response
  2. validation catches a missing charge amount
  3. percent-to-rupee conversion
  4. retry happens exactly once on validation failure
"""

from __future__ import annotations

import sys
import os
import json
from unittest.mock import patch, MagicMock, call
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.services.extractor import (
    scope_guard,
    extract,
    ClassifierResult,
    ExtractionResult,
    _normalise_percent_charges,
    _validate_raw,
    ValidationError,
)
from app.models.schemas import (
    KFSExtraction,
    EvidenceField,
    Loan,
    Rate,
    Instalment,
    Charge,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid extraction dict
# ---------------------------------------------------------------------------


def _minimal_raw(overrides: dict | None = None) -> dict:
    """Build the smallest dict that passes _validate_raw."""
    base = {
        "doc_meta": {
            "is_kfs": True,
            "loan_category": "retail",
            "is_digital_loan": None,
            "sanction_date": None,
            "language": "en",
        },
        "lender": {"name": "Test Bank", "type": "bank"},
        "proposal_no": {"value": "P001", "page": 1, "quote": "Proposal P001", "confidence": 0.9, "status": "found"},
        "loan": {
            "sanctioned_amount_inr": {"value": 20000.0, "page": 1, "quote": "loan of Rs 20000", "confidence": 0.99, "status": "found"},
            "disbursal": "upfront",
            "term_months": {"value": 24, "page": 1, "quote": "24 monthly instalments", "confidence": 0.99, "status": "found"},
            "instalments": [
                {
                    "type": "EMI",
                    "count": {"value": 24, "page": 1, "quote": "24 EMIs", "confidence": 0.99, "status": "found"},
                    "amount_inr": {"value": 970.0, "page": 1, "quote": "EMI Rs 970", "confidence": 0.99, "status": "found"},
                    "first_due_after_days": {"value": 30, "page": 1, "quote": "30 days after disbursement", "confidence": 0.9, "status": "found"},
                }
            ],
        },
        "rate": {
            "interest_rate_pct": {"value": 15.0, "page": 1, "quote": "15% per annum", "confidence": 0.99, "status": "found"},
            "type": "fixed",
            "floating": None,
        },
        "charges": [
            {
                "name": "Processing fee",
                "category": "processing",
                "payee": "lender",
                "frequency": "one_time",
                "recurrence": None,
                "amount_inr": 240.0,
                "percent": None,
                "percent_of": None,
                "gst_included": None,
                "page": 1,
                "quote": "Processing fee Rs. 240",
            }
        ],
        "stated_apr_pct": {"value": 17.07, "page": 1, "quote": "APR 17.07%", "confidence": 0.99, "status": "found"},
        "contingent_charges": [],
        "validity_period": None,
        "cooling_off_days": {"value": 3, "page": 2, "quote": "3 days cooling off", "confidence": 0.9, "status": "found"},
        "grievance": {"officer_name": None, "phone": None, "email": None, "page": None},
        "recovery_agent_clause_ref": {"value": None, "page": None, "quote": None, "confidence": None, "status": "not_found"},
        "flags_present": {"apr_computation_sheet": True, "amortisation_schedule": False},
        "amortisation_schedule": [],
    }
    if overrides:
        base.update(overrides)
    return base


DUMMY_PDF = b"%PDF-1.4 fake pdf bytes for testing"


# ---------------------------------------------------------------------------
# Test 1: scope_guard returns in_scope=False for a non-KFS document
# ---------------------------------------------------------------------------


class TestScopeGuard:
    def test_non_kfs_returns_out_of_scope(self):
        """When Gemini says is_kfs=False, in_scope must be False."""
        non_kfs_response = {
            "is_kfs": False,
            "loan_category": "other",
            "is_digital_loan": None,
            "confidence": 0.95,
            "reason": "This is a salary slip, not a KFS.",
        }
        with patch("app.services.extractor.generate_structured", return_value=non_kfs_response):
            result = scope_guard(DUMMY_PDF, "application/pdf")

        assert isinstance(result, ClassifierResult)
        assert result.is_kfs is False
        assert result.in_scope is False
        assert result.confidence == 0.95
        assert "salary slip" in result.reason.lower()

    def test_kfs_high_confidence_returns_in_scope(self):
        """When Gemini says is_kfs=True with high confidence, in_scope=True."""
        kfs_response = {
            "is_kfs": True,
            "loan_category": "retail",
            "is_digital_loan": False,
            "confidence": 0.98,
            "reason": "Document contains RBI KFS format with all required sections.",
        }
        with patch("app.services.extractor.generate_structured", return_value=kfs_response):
            result = scope_guard(DUMMY_PDF, "application/pdf")

        assert result.is_kfs is True
        assert result.in_scope is True

    def test_kfs_low_confidence_returns_out_of_scope(self):
        """A KFS response with confidence below threshold → in_scope=False."""
        low_conf = {
            "is_kfs": True,
            "loan_category": "retail",
            "is_digital_loan": None,
            "confidence": 0.4,   # below 0.6 threshold
            "reason": "Possibly a KFS but hard to tell.",
        }
        with patch("app.services.extractor.generate_structured", return_value=low_conf):
            result = scope_guard(DUMMY_PDF, "application/pdf")

        assert result.in_scope is False

    def test_gemini_error_returns_out_of_scope(self):
        """If Gemini throws, scope_guard returns in_scope=False (no exception)."""
        with patch(
            "app.services.extractor.generate_structured",
            side_effect=RuntimeError("network timeout"),
        ):
            result = scope_guard(DUMMY_PDF, "application/pdf")

        assert result.in_scope is False
        assert "failed" in result.reason.lower()


# ---------------------------------------------------------------------------
# Test 2: validation catches a missing charge amount
# ---------------------------------------------------------------------------


class TestValidation:
    def test_charge_missing_amount_and_percent(self):
        """_validate_raw should flag a charge with neither amount_inr nor percent."""
        raw = _minimal_raw()
        raw["charges"] = [
            {
                "name": "Mystery fee",
                "category": "other",
                "payee": "lender",
                "frequency": "one_time",
                "recurrence": None,
                "amount_inr": None,   # ← missing
                "percent": None,       # ← missing
                "percent_of": None,
                "gst_included": None,
                "page": 1,
                "quote": "mystery fee",
            }
        ]
        errors = _validate_raw(raw)
        assert any("charges[0]" in e.field_path for e in errors), (
            f"Expected a charge validation error, got: {errors}"
        )

    def test_valid_charge_passes(self):
        """A charge with amount_inr set should not trigger an error."""
        raw = _minimal_raw()
        errors = _validate_raw(raw)
        charge_errors = [e for e in errors if "charges" in e.field_path]
        assert charge_errors == []

    def test_charge_with_percent_passes(self):
        """A charge expressed as percent (no amount_inr) is valid."""
        raw = _minimal_raw()
        raw["charges"] = [
            {
                "name": "Processing fee",
                "category": "processing",
                "payee": "lender",
                "frequency": "one_time",
                "recurrence": None,
                "amount_inr": None,
                "percent": 1.0,
                "percent_of": "loan_amount",
                "gst_included": None,
                "page": 1,
                "quote": "1% processing fee",
            }
        ]
        errors = _validate_raw(raw)
        charge_errors = [e for e in errors if "charges" in e.field_path]
        assert charge_errors == []

    def test_sanctioned_amount_zero_flagged(self):
        """sanctioned_amount_inr = 0 should be a validation error."""
        raw = _minimal_raw()
        raw["loan"]["sanctioned_amount_inr"] = {"value": 0, "page": 1, "quote": "loan amount 0", "confidence": 0.9, "status": "found"}
        errors = _validate_raw(raw)
        assert any("sanctioned_amount" in e.field_path for e in errors)


# ---------------------------------------------------------------------------
# Test 3: percent-to-rupee conversion
# ---------------------------------------------------------------------------


class TestPercentConversion:
    def _make_extraction_with_percent_charge(self, loan_amount: float, percent: float) -> KFSExtraction:
        raw = _minimal_raw()
        raw["loan"]["sanctioned_amount_inr"]["value"] = loan_amount
        raw["charges"] = [
            {
                "name": "Processing fee",
                "category": "processing",
                "payee": "lender",
                "frequency": "one_time",
                "recurrence": None,
                "amount_inr": None,   # will be computed
                "percent": percent,
                "percent_of": "loan_amount",
                "gst_included": None,
                "page": 1,
                "quote": f"{percent}% of loan amount",
            }
        ]
        return KFSExtraction(**raw)

    def test_percent_charge_converted_to_rupees(self):
        """1% of ₹1,00,000 loan → ₹1,000 after normalisation."""
        ext = self._make_extraction_with_percent_charge(100_000.0, 1.0)
        assert ext.charges[0].amount_inr is None  # not yet converted

        _normalise_percent_charges(ext)

        assert ext.charges[0].amount_inr == pytest.approx(1000.0, abs=0.01)

    def test_percent_no_loan_amount_skipped(self):
        """If sanctioned_amount is missing, skip conversion gracefully."""
        raw = _minimal_raw()
        raw["loan"]["sanctioned_amount_inr"] = {"value": None, "page": None, "quote": None, "confidence": None, "status": "not_found"}
        raw["charges"] = [
            {
                "name": "Fee",
                "category": "other",
                "payee": "lender",
                "frequency": "one_time",
                "recurrence": None,
                "amount_inr": None,
                "percent": 2.0,
                "percent_of": "loan_amount",
                "gst_included": None,
                "page": 1,
                "quote": "2%",
            }
        ]
        ext = KFSExtraction(**raw)
        _normalise_percent_charges(ext)  # must not raise
        assert ext.charges[0].amount_inr is None  # unchanged

    def test_multiple_charges_mixed(self):
        """Charges with amount_inr should not be overwritten; percent charges converted."""
        raw = _minimal_raw()
        raw["loan"]["sanctioned_amount_inr"]["value"] = 50_000.0
        raw["charges"] = [
            {
                "name": "Processing fee",
                "category": "processing",
                "payee": "lender",
                "frequency": "one_time",
                "recurrence": None,
                "amount_inr": 500.0,   # already set
                "percent": None,
                "percent_of": None,
                "gst_included": None,
                "page": 1,
                "quote": "Processing fee Rs 500",
            },
            {
                "name": "Documentation fee",
                "category": "documentation",
                "payee": "lender",
                "frequency": "one_time",
                "recurrence": None,
                "amount_inr": None,
                "percent": 0.5,
                "percent_of": "loan_amount",
                "gst_included": None,
                "page": 1,
                "quote": "0.5% documentation",
            },
        ]
        ext = KFSExtraction(**raw)
        _normalise_percent_charges(ext)

        assert ext.charges[0].amount_inr == pytest.approx(500.0)  # unchanged
        assert ext.charges[1].amount_inr == pytest.approx(250.0, abs=0.01)  # 0.5% of 50,000


# ---------------------------------------------------------------------------
# Test 4: retry happens exactly once on validation failure
# ---------------------------------------------------------------------------


class TestRetry:
    def test_retry_called_exactly_once_on_validation_failure(self):
        """extract() must call generate_structured twice: once for initial attempt,
        once for the retry with the error appended."""

        # First call returns a charge with no amount/percent → validation error
        bad_raw = _minimal_raw()
        bad_raw["charges"] = [
            {
                "name": "Broken fee",
                "category": "other",
                "payee": "lender",
                "frequency": "one_time",
                "recurrence": None,
                "amount_inr": None,
                "percent": None,
                "percent_of": None,
                "gst_included": None,
                "page": 1,
                "quote": "broken fee",
            }
        ]

        # Second call (retry) returns a valid raw dict
        good_raw = _minimal_raw()

        call_responses = [bad_raw, good_raw]

        with patch(
            "app.services.extractor.generate_structured",
            side_effect=call_responses,
        ) as mock_gs:
            result = extract(DUMMY_PDF, "application/pdf")

        assert mock_gs.call_count == 2, (
            f"Expected 2 calls (initial + 1 retry), got {mock_gs.call_count}"
        )
        assert result.retry_attempted is True

    def test_no_retry_on_clean_extraction(self):
        """When extraction passes validation, generate_structured called only once."""
        good_raw = _minimal_raw()

        with patch(
            "app.services.extractor.generate_structured",
            return_value=good_raw,
        ) as mock_gs:
            result = extract(DUMMY_PDF, "application/pdf")

        assert mock_gs.call_count == 1
        assert result.retry_attempted is False

    def test_retry_prompt_contains_error_text(self):
        """The retry call's prompt must include the validation error message."""
        bad_raw = _minimal_raw()
        bad_raw["charges"] = [
            {
                "name": "Broken fee",
                "category": "other",
                "payee": "lender",
                "frequency": "one_time",
                "recurrence": None,
                "amount_inr": None,
                "percent": None,
                "percent_of": None,
                "gst_included": None,
                "page": 1,
                "quote": "broken fee",
            }
        ]
        good_raw = _minimal_raw()

        calls_made: list[str] = []

        def _capture(*args, **kwargs):
            calls_made.append(kwargs.get("prompt", args[0] if args else ""))
            if len(calls_made) == 1:
                return bad_raw
            return good_raw

        with patch("app.services.extractor.generate_structured", side_effect=_capture):
            extract(DUMMY_PDF, "application/pdf")

        assert len(calls_made) == 2
        retry_prompt = calls_made[1]
        assert "validation error" in retry_prompt.lower() or "charges[0]" in retry_prompt, (
            f"Retry prompt should contain error info. Got start: {retry_prompt[:200]}"
        )
