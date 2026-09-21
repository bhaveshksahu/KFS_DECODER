"""GET /api/v1/analyses/{id} — retrieve full result."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from ..services import storage

router = APIRouter(prefix="/api/v1/analyses", tags=["analyses"])


@router.get("/{analysis_id}")
async def get_analysis(analysis_id: str) -> JSONResponse:
    """Retrieve a previously computed analysis by ID."""
    record = storage.get_analysis(analysis_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Analysis {analysis_id} not found.")
    return JSONResponse(status_code=status.HTTP_200_OK, content=record)
