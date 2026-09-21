"""Gemini API client wrapper.

Supports two backends chosen by the GEMINI_BACKEND env var:
  "developer"  → Gemini Developer API (AI Studio key in GEMINI_API_KEY)
  "vertex"     → Vertex AI (project + location from config; uses ADC credentials)

One public function:
    generate_structured(prompt, file_bytes, mime_type, response_schema, temperature)

Design:
- Inline PDF/image bytes → types.Part.from_bytes(data=..., mime_type=...)
- Structured output     → GenerateContentConfig(response_mime_type="application/json",
                                                  response_schema=<Pydantic model>)
- One retry on transient errors (503, 429, ConnectionError)
- Latency logged; document bytes/content never logged
"""

from __future__ import annotations

import logging
import time
from typing import Any, Type

from google.genai import Client, types
from pydantic import BaseModel

from ..config import get_settings

logger = logging.getLogger(__name__)

# Transient HTTP status codes that warrant a single retry
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _make_client() -> Client:
    """Build a google-genai Client for the configured backend."""
    cfg = get_settings()
    backend = cfg.gemini_backend.lower()

    if backend == "vertex":
        return Client(
            vertexai=True,
            project=cfg.gcp_project or None,
            location=cfg.location or None,
        )
    else:
        # Developer API — requires GEMINI_API_KEY
        api_key = cfg.gemini_api_key
        if not api_key:
            raise ValueError(
                "GEMINI_BACKEND=developer requires GEMINI_API_KEY to be set."
            )
        return Client(api_key=api_key)


def generate_structured(
    prompt: str,
    file_bytes: bytes,
    mime_type: str,
    response_schema: Type[BaseModel],
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Send a multimodal request to Gemini and return a parsed dict.

    Parameters
    ----------
    prompt          : text instruction (system + user combined for simplicity)
    file_bytes      : raw bytes of the PDF or image
    mime_type       : e.g. "application/pdf" or "image/jpeg"
    response_schema : Pydantic model class; used as the JSON response schema
    temperature     : low values (0–0.2) for extraction accuracy

    Returns
    -------
    dict parsed from the model's JSON response

    Raises
    ------
    RuntimeError on non-retryable errors or after one failed retry
    """
    cfg = get_settings()
    client = _make_client()

    file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
    text_part = types.Part(text=prompt)
    contents = [types.Content(role="user", parts=[file_part, text_part])]

    gen_config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=response_schema,
    )

    def _call() -> types.GenerateContentResponse:
        return client.models.generate_content(
            model=cfg.gemini_model,
            contents=contents,
            config=gen_config,
        )

    # --- first attempt ---
    t0 = time.monotonic()
    try:
        response = _call()
        latency = time.monotonic() - t0
        logger.info(
            "gemini_generate_structured model=%s latency_s=%.2f attempt=1",
            cfg.gemini_model,
            latency,
        )
    except Exception as exc:
        latency = time.monotonic() - t0
        status = getattr(getattr(exc, "status_code", None), "value", None) or getattr(exc, "code", None)
        if _should_retry(exc, status):
            logger.warning(
                "gemini_generate_structured retrying after transient error: %s latency_s=%.2f",
                type(exc).__name__,
                latency,
            )
            t0 = time.monotonic()
            try:
                response = _call()
                latency = time.monotonic() - t0
                logger.info(
                    "gemini_generate_structured model=%s latency_s=%.2f attempt=2",
                    cfg.gemini_model,
                    latency,
                )
            except Exception as exc2:
                raise RuntimeError(f"Gemini call failed after retry: {exc2}") from exc2
        else:
            raise RuntimeError(f"Gemini call failed: {exc}") from exc

    # Extract text and parse
    text = response.text
    if not text:
        raise RuntimeError("Gemini returned an empty response.")

    import json
    return json.loads(text)


def _should_retry(exc: Exception, status: int | None) -> bool:
    """Return True for transient errors that a single retry might fix."""
    if status in _RETRYABLE_STATUS:
        return True
    name = type(exc).__name__.lower()
    return "connection" in name or "timeout" in name or "transport" in name
