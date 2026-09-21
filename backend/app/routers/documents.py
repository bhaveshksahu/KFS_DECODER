"""POST /api/v1/documents — upload a file.
POST /api/v1/documents/{doc_id}/extract — scope guard + extraction.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

import filetype
from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse

from ..services import storage
from ..services.extractor import extract, scope_guard, ExtractionResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# ---------------------------------------------------------------------------
# Upload validation constants
# ---------------------------------------------------------------------------

_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
_ALLOWED_MIME = {"application/pdf", "image/jpeg", "image/png"}
_ALLOWED_MIME_DISPLAY = "PDF, JPEG, or PNG"


def _sniff_mime(data: bytes) -> str | None:
    """Return detected MIME type using magic bytes, not the filename extension."""
    kind = filetype.guess(data)
    if kind is None:
        # Try PDF header manually — filetype.guess misses some PDFs
        if data[:4] == b"%PDF":
            return "application/pdf"
        return None
    return kind.mime


# ---------------------------------------------------------------------------
# POST /api/v1/documents
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
) -> JSONResponse:
    """Upload a PDF/PNG/JPG for processing.  Returns a doc_id for subsequent calls."""
    req_id = getattr(request.state, "request_id", "?")

    data = await file.read()
    size = len(data)

    if size > _MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size {size:,} bytes exceeds 10 MB limit.",
        )

    mime = _sniff_mime(data)
    if mime not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Detected file type '{mime or 'unknown'}' is not supported. "
                f"Please upload a {_ALLOWED_MIME_DISPLAY} file."
            ),
        )

    doc_id = str(uuid.uuid4())
    storage.store_upload(doc_id, data, mime)
    logger.info("upload doc_id=%s mime=%s size=%d req_id=%s", doc_id, mime, size, req_id)

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"doc_id": doc_id, "mime_type": mime, "size_bytes": size},
    )


# ---------------------------------------------------------------------------
# POST /api/v1/documents/{doc_id}/extract
# ---------------------------------------------------------------------------


@router.post("/{doc_id}/extract")
async def extract_document(
    doc_id: str,
    request: Request,
    demo: bool = Query(default=False, description="Return cached extraction if available"),
) -> JSONResponse:
    """Run scope guard → extraction → validation on an uploaded document."""
    req_id = getattr(request.state, "request_id", "?")

    entry = storage.get_upload(doc_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document {doc_id} not found.")
    file_bytes, mime_type = entry

    # Scope guard
    scope = scope_guard(file_bytes, mime_type)
    if not scope.in_scope:
        extraction_id = str(uuid.uuid4())
        record = {
            "extraction_id": extraction_id,
            "doc_id": doc_id,
            "in_scope": False,
            "scope_reason": scope.reason,
            "loan_category": scope.loan_category,
        }
        storage.store_extraction(extraction_id, record)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "extraction_id": extraction_id,
                "in_scope": False,
                "reason": scope.reason,
                "loan_category": scope.loan_category,
            },
        )

    result: ExtractionResult = extract(file_bytes, mime_type)

    extraction_id = str(uuid.uuid4())
    if result.success and result.extraction:
        record = {
            "extraction_id": extraction_id,
            "doc_id": doc_id,
            "in_scope": True,
            "success": True,
            "retry_attempted": result.retry_attempted,
            "not_found_critical": result.not_found_critical,
            "extraction": result.extraction.model_dump(),
        }
    else:
        record = {
            "extraction_id": extraction_id,
            "doc_id": doc_id,
            "in_scope": True,
            "success": False,
            "failure_reason": result.failure_reason,
            "validation_errors": [
                {"field": e.field_path, "message": e.message}
                for e in result.validation_errors
            ],
            "extraction": None,
        }

    storage.store_extraction(extraction_id, record)
    logger.info(
        "extraction doc_id=%s extraction_id=%s success=%s retry=%s req_id=%s",
        doc_id, extraction_id, result.success, result.retry_attempted, req_id,
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content=record)
