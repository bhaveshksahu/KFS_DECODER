"""Pydantic models for the KFS extraction schema (PRD §8).

Every "critical" field is wrapped in a Field[T] envelope that carries the
source evidence (page, verbatim quote, confidence).  Non-found fields use
value=None and status="not_found".

Uses Pydantic v2 syntax (Generic BaseModel via standard typing).
"""

from __future__ import annotations

from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Generic evidence envelope
# ---------------------------------------------------------------------------

T = TypeVar("T")


class EvidenceField(BaseModel, Generic[T]):
    """Wraps a value with provenance metadata.

    Used for every critical field that must be grounded in the document text.
    """

    value: Optional[T] = None
    page: Optional[int] = None
    quote: Optional[str] = Field(default=None, description="≤15 words verbatim from the document")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    status: Literal["found", "not_found", "user_edited"] = "found"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class DocMeta(BaseModel):
    is_kfs: Optional[bool] = None
    loan_category: Optional[Literal["retail", "msme", "credit_card", "other"]] = None
    is_digital_loan: Optional[bool] = None
    sanction_date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD or null")
    language: Optional[Literal["en", "hi", "mixed"]] = None


class Lender(BaseModel):
    name: Optional[str] = None
    type: Optional[Literal["bank", "nbfc", "hfc", "other"]] = None


class FloatingRateDetails(BaseModel):
    benchmark: Optional[str] = None
    benchmark_rate_pct: Optional[float] = None
    spread_pct: Optional[float] = None
    reset_months: Optional[int] = None
    impact_25bps: Optional[str] = None


class Rate(BaseModel):
    interest_rate_pct: EvidenceField[float] = Field(default_factory=lambda: EvidenceField(status="not_found"))
    type: Optional[Literal["fixed", "floating", "hybrid"]] = None
    floating: Optional[FloatingRateDetails] = None


class Instalment(BaseModel):
    type: Optional[Literal["EMI", "EPI", "other"]] = None
    count: EvidenceField[int] = Field(default_factory=lambda: EvidenceField(status="not_found"))
    amount_inr: EvidenceField[float] = Field(default_factory=lambda: EvidenceField(status="not_found"))
    first_due_after_days: EvidenceField[int] = Field(default_factory=lambda: EvidenceField(status="not_found"))


class Loan(BaseModel):
    sanctioned_amount_inr: EvidenceField[float] = Field(default_factory=lambda: EvidenceField(status="not_found"))
    disbursal: Optional[Literal["upfront", "staged"]] = None
    term_months: EvidenceField[int] = Field(default_factory=lambda: EvidenceField(status="not_found"))
    instalments: list[Instalment] = Field(default_factory=list)


class Charge(BaseModel):
    name: str
    category: Optional[
        Literal["processing", "insurance", "valuation", "legal", "stamp_duty", "documentation", "other"]
    ] = None
    payee: Optional[Literal["lender", "third_party"]] = None
    frequency: Optional[Literal["one_time", "recurring"]] = None
    recurrence: Optional[Literal["monthly", "yearly"]] = None
    amount_inr: Optional[float] = None
    percent: Optional[float] = None
    percent_of: Optional[Literal["loan_amount"]] = None
    gst_included: Optional[bool] = None
    page: Optional[int] = None
    quote: Optional[str] = None


class ContingentCharge(BaseModel):
    name: str
    value: str
    page: Optional[int] = None


class ValidityPeriod(BaseModel):
    value: Optional[int] = None
    unit: Optional[Literal["working_days", "calendar_days"]] = None
    page: Optional[int] = None


class Grievance(BaseModel):
    officer_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    page: Optional[int] = None


class FlagsPresent(BaseModel):
    apr_computation_sheet: Optional[bool] = None
    amortisation_schedule: Optional[bool] = None


class AmortisationRow(BaseModel):
    n: int
    outstanding: float
    principal: float
    interest: float
    instalment: float


# ---------------------------------------------------------------------------
# Root extraction schema
# ---------------------------------------------------------------------------


class KFSExtraction(BaseModel):
    """Full extraction output from the Gemini structured-output call."""

    doc_meta: DocMeta = Field(default_factory=DocMeta)
    lender: Lender = Field(default_factory=Lender)
    proposal_no: EvidenceField[str] = Field(default_factory=lambda: EvidenceField(status="not_found"))
    loan: Loan = Field(default_factory=Loan)
    rate: Rate = Field(default_factory=Rate)
    charges: list[Charge] = Field(default_factory=list)
    stated_apr_pct: EvidenceField[float] = Field(default_factory=lambda: EvidenceField(status="not_found"))
    contingent_charges: list[ContingentCharge] = Field(default_factory=list)
    validity_period: Optional[ValidityPeriod] = None
    cooling_off_days: EvidenceField[int] = Field(default_factory=lambda: EvidenceField(status="not_found"))
    grievance: Grievance = Field(default_factory=Grievance)
    recovery_agent_clause_ref: EvidenceField[str] = Field(default_factory=lambda: EvidenceField(status="not_found"))
    flags_present: FlagsPresent = Field(default_factory=FlagsPresent)
    amortisation_schedule: list[AmortisationRow] = Field(default_factory=list)
