"""GET  /api/v1/samples          — list available demo samples.
POST /api/v1/samples/{key}/load — load a sample instantly (no Gemini call).
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse

from ..services import storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/samples", tags=["samples"])

_SAMPLES_DIR = Path(__file__).parent.parent / "data" / "samples"


def _load_all_samples() -> dict[str, dict]:
    """Load all *.json files from the samples directory. Cached at module level."""
    samples: dict[str, dict] = {}
    for path in sorted(_SAMPLES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            key = data.get("key", path.stem)
            samples[key] = data
        except Exception as exc:
            logger.warning("samples: failed to load %s: %s", path.name, exc)
    return samples


_SAMPLES: dict[str, dict] = _load_all_samples()


# ---------------------------------------------------------------------------
# GET /api/v1/samples
# ---------------------------------------------------------------------------


@router.get("")
async def list_samples() -> JSONResponse:
    """Return the catalogue of available demo samples."""
    catalogue = [
        {
            "key": s["key"],
            "label": s.get("label", s["key"]),
            "description": s.get("description", ""),
            "synthetic": s.get("synthetic", False),
            "has_data": s.get("extraction") is not None,
        }
        for s in _SAMPLES.values()
    ]
    return JSONResponse(status_code=status.HTTP_200_OK, content={"samples": catalogue})


# ---------------------------------------------------------------------------
# POST /api/v1/samples/{key}/load
# ---------------------------------------------------------------------------


@router.post("/{key}/load")
async def load_sample(
    key: str,
    demo: bool = Query(default=False, description="Alias for demo=1 URL param"),
) -> JSONResponse:
    """Load a sample's pre-extracted JSON and analysis instantly, without a Gemini call."""
    sample = _SAMPLES.get(key)
    if sample is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Sample '{key}' not found.")
    if sample.get("extraction") is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Sample '{key}' is a placeholder — extraction data not yet available.",
        )

    # Store as a real extraction + analysis record so subsequent calls work
    extraction_id = str(uuid.uuid4())
    analysis_id = str(uuid.uuid4())

    extraction_record = {
        "extraction_id": extraction_id,
        "doc_id": f"sample:{key}",
        "in_scope": True,
        "success": True,
        "retry_attempted": False,
        "not_found_critical": [],
        "extraction": sample["extraction"],
        "sample_key": key,
    }
    storage.store_extraction(extraction_id, extraction_record)

    analysis_record = {
        "analysis_id": analysis_id,
        "extraction_id": extraction_id,
        "sample_key": key,
        **(sample.get("analysis") or {}),
    }
    storage.store_analysis(analysis_id, analysis_record)

    logger.info("sample loaded key=%s extraction_id=%s analysis_id=%s", key, extraction_id, analysis_id)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "sample_key": key,
            "synthetic": sample.get("synthetic", False),
            "label": sample.get("label", key),
            "extraction_id": extraction_id,
            "analysis_id": analysis_id,
            "extraction": sample["extraction"],
            "analysis": sample.get("analysis"),
        },
    )
