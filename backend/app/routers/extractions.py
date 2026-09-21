"""PATCH /api/v1/extractions/{id}  — save user corrections.
POST  /api/v1/extractions/{id}/analyze — run APR + rules.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..models.schemas import KFSExtraction
from ..services import storage
from ..services.analysis import build_summary, compute_amortisation_schedule
from ..services.rules import run_all

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/extractions", tags=["extractions"])


# ---------------------------------------------------------------------------
# PATCH /api/v1/extractions/{id}
# ---------------------------------------------------------------------------


class ExtractionPatch(BaseModel):
    """Free-form dict of field overrides; any key present overwrites the stored value."""
    fields: dict[str, Any]


@router.patch("/{extraction_id}")
async def patch_extraction(
    extraction_id: str,
    patch: ExtractionPatch,
    request: Request,
) -> JSONResponse:
    """Overwrite specific fields in an extraction record (e.g., after user edits)."""
    updated = storage.patch_extraction(extraction_id, patch.fields)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Extraction {extraction_id} not found.")
    return JSONResponse(status_code=status.HTTP_200_OK, content=updated)


# ---------------------------------------------------------------------------
# POST /api/v1/extractions/{id}/analyze
# ---------------------------------------------------------------------------


@router.post("/{extraction_id}/analyze")
async def analyze_extraction(
    extraction_id: str,
    request: Request,
) -> JSONResponse:
    """Run deterministic APR + rules engine over the stored extraction."""
    req_id = getattr(request.state, "request_id", "?")

    record = storage.get_extraction(extraction_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Extraction {extraction_id} not found.")

    if not record.get("success") or not record.get("extraction"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Extraction was not successful; cannot analyze. Please re-extract or fill fields manually.",
        )

    try:
        extraction = KFSExtraction(**record["extraction"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Extraction record is malformed: {exc}",
        )

    output = run_all(extraction)
    summary = build_summary(extraction)

    # Compute amortisation schedule from extraction inputs
    _amort_schedule: list[dict] = []
    _p = extraction.loan.sanctioned_amount_inr.value
    _r = extraction.rate.interest_rate_pct.value
    _n = extraction.loan.term_months.value
    if _p and _r and _n:
        # Use the same instalment as the APR engine: stated if available, else computed
        _inst: float | None = None
        if extraction.loan.instalments:
            _inst = extraction.loan.instalments[0].amount_inr.value
        if not _inst:
            from ..services.apr import emi as _emi
            _inst = _emi(float(_p), float(_r), int(_n))
        _rows = compute_amortisation_schedule(float(_p), float(_r), int(_n), float(_inst))
        _amort_schedule = [
            {"n": row.n, "outstanding": row.outstanding,
             "principal": row.principal, "interest": row.interest,
             "instalment": row.instalment}
            for row in _rows
        ]

    analysis_id = str(uuid.uuid4())
    result: dict[str, Any] = {
        "analysis_id": analysis_id,
        "extraction_id": extraction_id,
        "in_scope": output.in_scope,
        "apr": None,
        "summary": None,
        "rule_results": [
            {
                "rule_id": r.rule_id,
                "status": r.status,
                "severity": r.severity,
                "evidence": r.evidence,
                "citation": r.citation,
                "plain_text_en": r.plain_text_en,
                "plain_text_hi": r.plain_text_hi,
            }
            for r in output.rule_results
        ],
    }

    if output.apr_variants:
        av = output.apr_variants
        result["apr"] = {
            "interest_only_pct": av.interest_only_pct,
            "all_charges_pct": av.all_charges_pct,
            "excluding_third_party_pct": av.excluding_third_party_pct,
            "effective_annual_all_charges_pct": av.effective_annual_all_charges_pct,
            "stamp_duty_included": av.stamp_duty_included_in_apr,
            "instalment_used": av.instalment_used,
            "n_periods": av.n_periods,
        }

    result["amortisation_schedule"] = _amort_schedule

    if summary:
        result["summary"] = {
            "total_repayable": summary.total_repayable,
            "total_interest": summary.total_interest,
            "total_charges_inr": summary.total_charges_inr,
            "fee_impact_pp": summary.fee_impact_pp,
            "charge_pct_of_loan": summary.charge_pct_of_loan,
            "stamp_duty_assumption": summary.stamp_duty_assumption,
            "charge_breakdown": [
                {"category": cb.category, "payee": cb.payee, "total_inr": cb.total_inr, "names": cb.names}
                for cb in summary.charge_breakdown
            ],
        }

    storage.store_analysis(analysis_id, result)
    logger.info(
        "analysis analysis_id=%s extraction_id=%s in_scope=%s req_id=%s",
        analysis_id, extraction_id, output.in_scope, req_id,
    )
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=result)
