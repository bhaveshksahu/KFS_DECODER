"""Storage abstraction — local (default) and GCP (TODO).

Interface
---------
store_upload(doc_id, file_bytes, mime_type) -> None
get_upload(doc_id) -> tuple[bytes, str] | None
delete_upload(doc_id) -> None

store_extraction(extraction_id, data: dict) -> None
get_extraction(extraction_id) -> dict | None
patch_extraction(extraction_id, patch: dict) -> dict | None

store_analysis(analysis_id, data: dict) -> None
get_analysis(analysis_id) -> dict | None

Implementations
---------------
local  — in-memory dict for file bytes (test-friendly) + local disk fallback;
         startup cleans files older than 24 h.
gcp    — TODO: Cloud Storage (uploads) + Firestore (records).

PII redaction
-------------
Before any analysis record is stored, _redact_analysis() strips lender-customer
PII fields: lender name, grievance officer name, phone, and email.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------

# PII key names whose values are always blanked regardless of content
_PII_KEYS = {"name", "officer_name", "phone", "email"}

# Pattern for bare account numbers: 8+ consecutive digits (no separators)
_ACCOUNT_RE = re.compile(r"\b\d{8,20}\b")


def _redact_analysis(record: dict) -> dict:
    """Return a redacted copy of the analysis record.

    Strategy:
    - Keys in _PII_KEYS → value replaced with "[REDACTED]"
    - Lender block: keep only `type` (non-identifying)
    - Grievance block: removed entirely
    - proposal_no: removed entirely
    - Bare 8–20 digit sequences (account numbers) in other strings: redacted
    - Citation strings, rule IDs, APR numbers etc. are NOT touched
    """
    import copy
    r = copy.deepcopy(record)

    # Top-level structural removals
    if "lender" in r:
        r["lender"] = {"type": r.get("lender", {}).get("type")}
    if "grievance" in r:
        r["grievance"] = {}
    r.pop("proposal_no", None)

    def _strip(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k in _PII_KEYS:
                    out[k] = "[REDACTED]"
                else:
                    out[k] = _strip(v)
            return out
        if isinstance(obj, list):
            return [_strip(i) for i in obj]
        if isinstance(obj, str):
            # Only scrub bare long digit sequences (account numbers)
            return _ACCOUNT_RE.sub("[REDACTED]", obj)
        return obj

    return _strip(r)


# ---------------------------------------------------------------------------
# In-process local store (default; test-friendly)
# ---------------------------------------------------------------------------

_UPLOADS: dict[str, tuple[bytes, str, float]] = {}   # doc_id → (bytes, mime, timestamp)
_EXTRACTIONS: dict[str, dict] = {}
_ANALYSES: dict[str, dict] = {}

_MAX_AGE_SECONDS = 24 * 3600


def _cleanup_old_uploads() -> None:
    """Remove uploads older than 24 hours from in-memory store."""
    now = time.time()
    stale = [k for k, (_, _, ts) in _UPLOADS.items() if now - ts > _MAX_AGE_SECONDS]
    for k in stale:
        del _UPLOADS[k]
        logger.info("storage: expired upload %s", k)


# ---------------------------------------------------------------------------
# Public interface — local implementation
# ---------------------------------------------------------------------------


def store_upload(doc_id: str, file_bytes: bytes, mime_type: str) -> None:
    _cleanup_old_uploads()
    _UPLOADS[doc_id] = (file_bytes, mime_type, time.time())


def get_upload(doc_id: str) -> tuple[bytes, str] | None:
    entry = _UPLOADS.get(doc_id)
    if entry is None:
        return None
    file_bytes, mime_type, ts = entry
    if time.time() - ts > _MAX_AGE_SECONDS:
        del _UPLOADS[doc_id]
        return None
    return file_bytes, mime_type


def delete_upload(doc_id: str) -> None:
    _UPLOADS.pop(doc_id, None)


def store_extraction(extraction_id: str, data: dict) -> None:
    _EXTRACTIONS[extraction_id] = data


def get_extraction(extraction_id: str) -> dict | None:
    return _EXTRACTIONS.get(extraction_id)


def patch_extraction(extraction_id: str, patch: dict) -> dict | None:
    existing = _EXTRACTIONS.get(extraction_id)
    if existing is None:
        return None
    existing.update(patch)
    return existing


def store_analysis(analysis_id: str, data: dict) -> None:
    redacted = _redact_analysis(data)
    _ANALYSES[analysis_id] = redacted


def get_analysis(analysis_id: str) -> dict | None:
    return _ANALYSES.get(analysis_id)


# ---------------------------------------------------------------------------
# TODO: GCP backend (Cloud Storage + Firestore)
# ---------------------------------------------------------------------------
# To enable: set STORAGE_BACKEND=gcp in environment.
# Requires:
#   pip install google-cloud-storage google-cloud-firestore
#
# def _gcp_store_upload(doc_id, file_bytes, mime_type):
#     from google.cloud import storage as gcs
#     cfg = get_settings()
#     client = gcs.Client(project=cfg.gcp_project)
#     bucket = client.bucket(cfg.bucket)
#     blob = bucket.blob(f"uploads/{doc_id}")
#     blob.upload_from_string(file_bytes, content_type=mime_type)
#     # Lifecycle delete handled by bucket lifecycle rules in infra/lifecycle.json
#
# def _gcs_get_upload(doc_id):
#     ...
#
# def _firestore_store_analysis(analysis_id, data):
#     from google.cloud import firestore
#     cfg = get_settings()
#     db = firestore.Client(project=cfg.gcp_project)
#     db.collection("analyses").document(analysis_id).set(_redact_analysis(data))
#
# Dispatch by STORAGE_BACKEND env var once implemented.
