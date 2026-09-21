"""Shared request/response models and utilities for the API routers."""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = None


class OKResponse(BaseModel):
    ok: bool = True
