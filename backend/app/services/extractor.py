"""Extraction pipeline: scope guard → extract → validate → retry.

Public API
----------
scope_guard(file_bytes, mime_type) -> ClassifierResult
extract(file_bytes, mime_type)     -> ExtractionResult

Both functions return typed dataclasses; neither raises on expected failures.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.schemas import (
    KFSExtraction,
    EvidenceField,
    Charge,
)
from .gemini_client import generate_structured

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt loading (read once from disk)
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ClassifierResult:
    is_kfs: bool
    loan_category: str  # "retail" | "msme" | "credit_card" | "other"
    is_digital_loan: bool | None
    confidence: float
    reason: str
    in_scope: bool  # True when is_kfs=True and confidence >= threshold


@dataclass
class ValidationError:
    field_path: str
    message: str


@dataclass
class ExtractionResult:
    success: bool
    extraction: KFSExtraction | None
    not_found_critical: list[str]
    validation_errors: list[ValidationError]
    retry_attempted: bool
    failure_reason: str | None = None


# ---------------------------------------------------------------------------
# Classifier schema (minimal Pydantic model for structured output)
# ---------------------------------------------------------------------------

from pydantic import BaseModel
from typing import Literal, Optional


class _ClassifierSchema(BaseModel):
    is_kfs: bool
    loan_category: Literal["retail", "msme", "credit_card", "other"]
    is_digital_loan: Optional[bool] = None
    confidence: float
    reason: str


# ---------------------------------------------------------------------------
# Scope guard
# ---------------------------------------------------------------------------

_SCOPE_CONFIDENCE_THRESHOLD = 0.6


def scope_guard(file_bytes: bytes, mime_type: str) -> ClassifierResult:
    """Classify the document.  Never raises; returns in_scope=False on errors."""
    prompt = _load_prompt("classifier.md")
    try:
        raw = generate_structured(
            prompt=prompt,
            file_bytes=file_bytes,
            mime_type=mime_type,
            response_schema=_ClassifierSchema,
            temperature=0.0,
        )
        result = _ClassifierSchema(**raw)
    except Exception as exc:
        logger.warning("scope_guard failed: %s", exc)
        return ClassifierResult(
            is_kfs=False,
            loan_category="other",
            is_digital_loan=None,
            confidence=0.0,
            reason=f"Classification failed: {exc}",
            in_scope=False,
        )

    in_scope = result.is_kfs and result.confidence >= _SCOPE_CONFIDENCE_THRESHOLD
    return ClassifierResult(
        is_kfs=result.is_kfs,
        loan_category=result.loan_category,
        is_digital_loan=result.is_digital_loan,
        confidence=result.confidence,
        reason=result.reason,
        in_scope=in_scope,
    )


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

_CRITICAL_FIELDS = [
    "loan.sanctioned_amount_inr",
    "loan.term_months",
    "rate.interest_rate_pct",
    "loan.instalments[0].amount_inr",
    "stated_apr_pct",
]


def extract(file_bytes: bytes, mime_type: str) -> ExtractionResult:
    """Run extraction with one automatic retry on validation failure."""
    base_prompt = _load_prompt("extractor.md")

    raw, validation_errors, retry = _attempt_extraction(base_prompt, file_bytes, mime_type)

    if raw is None:
        return ExtractionResult(
            success=False,
            extraction=None,
            not_found_critical=[],
            validation_errors=validation_errors,
            retry_attempted=retry,
            failure_reason="Gemini returned an unrecoverable error.",
        )

    if validation_errors and not retry:
        # Append validation errors to prompt and retry once
        error_text = "\n".join(f"- {e.field_path}: {e.message}" for e in validation_errors)
        retry_prompt = (
            base_prompt
            + f"\n\n## Previous attempt had validation errors — fix them:\n{error_text}"
        )
        raw, validation_errors, _ = _attempt_extraction(retry_prompt, file_bytes, mime_type)
        retry = True

    if raw is None:
        return ExtractionResult(
            success=False,
            extraction=None,
            not_found_critical=[],
            validation_errors=validation_errors,
            retry_attempted=retry,
            failure_reason="Extraction failed after retry.",
        )

    # Build Pydantic model (second-pass validation via Pydantic itself)
    try:
        extraction = KFSExtraction(**raw)
    except Exception as exc:
        return ExtractionResult(
            success=False,
            extraction=None,
            not_found_critical=[],
            validation_errors=[ValidationError("schema", str(exc))],
            retry_attempted=retry,
            failure_reason=f"Schema parse error: {exc}",
        )

    # Post-parse validation
    errors = _validate(extraction, raw)
    # Normalise percent charges to rupees in-place
    _normalise_percent_charges(extraction)
    not_found = _list_not_found_critical(extraction)

    success = len([e for e in errors if e.field_path != "term_months_approx"]) == 0

    return ExtractionResult(
        success=success,
        extraction=extraction,
        not_found_critical=not_found,
        validation_errors=errors,
        retry_attempted=retry,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _attempt_extraction(
    prompt: str, file_bytes: bytes, mime_type: str
) -> tuple[dict | None, list[ValidationError], bool]:
    """Call Gemini and return (raw_dict, errors, retry_flag).  Never raises."""
    try:
        raw = generate_structured(
            prompt=prompt,
            file_bytes=file_bytes,
            mime_type=mime_type,
            response_schema=KFSExtraction,
            temperature=0.1,
        )
    except Exception as exc:
        logger.error("extraction call failed: %s", exc)
        return None, [ValidationError("gemini", str(exc))], False

    errors = _validate_raw(raw)
    return raw, errors, False


def _validate_raw(raw: dict) -> list[ValidationError]:
    """Fast structural checks on the raw dict before Pydantic parsing."""
    errors: list[ValidationError] = []

    # sanctioned_amount > 0
    try:
        amt = raw.get("loan", {}).get("sanctioned_amount_inr", {})
        if isinstance(amt, dict):
            val = amt.get("value")
        else:
            val = amt
        if val is not None and (not isinstance(val, (int, float)) or val <= 0):
            errors.append(ValidationError("loan.sanctioned_amount_inr", "must be > 0"))
    except Exception:
        pass

    # every charge has amount_inr or percent
    for i, charge in enumerate(raw.get("charges", [])):
        if not isinstance(charge, dict):
            continue
        has_amount = charge.get("amount_inr") is not None
        has_percent = charge.get("percent") is not None
        if not has_amount and not has_percent:
            errors.append(
                ValidationError(
                    f"charges[{i}]",
                    f"charge '{charge.get('name', '?')}' has neither amount_inr nor percent",
                )
            )

    return errors


def _validate(extraction: KFSExtraction, raw: dict) -> list[ValidationError]:
    """Post-Pydantic structural checks."""
    errors: list[ValidationError] = []

    # term_months ≈ instalment count (within 2)
    term_val = extraction.loan.term_months.value
    instalment_count = sum(
        (inst.count.value or 0) for inst in extraction.loan.instalments
    )
    if term_val and instalment_count:
        if abs(term_val - instalment_count) > 2:
            errors.append(
                ValidationError(
                    "term_months_approx",
                    f"term_months={term_val} but total instalment count={instalment_count}; check document",
                )
            )

    # every charge has amount_inr or percent (post-parse)
    for i, charge in enumerate(extraction.charges):
        if charge.amount_inr is None and charge.percent is None:
            errors.append(
                ValidationError(
                    f"charges[{i}]",
                    f"charge '{charge.name}' has neither amount_inr nor percent",
                )
            )

    return errors


def _normalise_percent_charges(extraction: KFSExtraction) -> None:
    """Convert percent charges to rupees using sanctioned_amount, in-place."""
    amount_inr = extraction.loan.sanctioned_amount_inr.value
    if not amount_inr:
        return
    for charge in extraction.charges:
        if charge.amount_inr is None and charge.percent is not None:
            if charge.percent_of == "loan_amount":
                charge.amount_inr = round(charge.percent / 100 * amount_inr, 2)


def _list_not_found_critical(extraction: KFSExtraction) -> list[str]:
    """Return list of critical field paths whose status is not_found."""
    not_found: list[str] = []

    def _check(field_obj: Any, path: str) -> None:
        if isinstance(field_obj, EvidenceField):
            if field_obj.status == "not_found" or field_obj.value is None:
                not_found.append(path)

    _check(extraction.loan.sanctioned_amount_inr, "loan.sanctioned_amount_inr")
    _check(extraction.loan.term_months, "loan.term_months")
    _check(extraction.rate.interest_rate_pct, "rate.interest_rate_pct")
    _check(extraction.stated_apr_pct, "stated_apr_pct")

    if extraction.loan.instalments:
        _check(extraction.loan.instalments[0].amount_inr, "loan.instalments[0].amount_inr")
    else:
        not_found.append("loan.instalments[0].amount_inr")

    return not_found
