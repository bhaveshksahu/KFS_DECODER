"""FastAPI application — KFS Decoder backend."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .routers import analyses, documents, extractions, samples

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

cfg = get_settings()

app = FastAPI(
    title="KFS Decoder",
    description="Decode and analyse RBI Key Facts Statements for loans.",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
    request.state.request_id = req_id
    start = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - start
    response.headers["X-Request-ID"] = req_id
    logger.info(
        "http method=%s path=%s status=%d elapsed_ms=%.0f req_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed * 1000,
        req_id,
    )
    return response


# ---------------------------------------------------------------------------
# Structured JSON error handler
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", "?")
    logger.error("unhandled error req_id=%s: %s", req_id, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "request_id": req_id},
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(documents.router)
app.include_router(extractions.router)
app.include_router(analyses.router)
app.include_router(samples.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["ops"], summary="Health check for Cloud Run")
async def healthz() -> JSONResponse:
    return JSONResponse(
        content={
            "status": "ok",
            "gemini_model": cfg.gemini_model,
            "gcp_project": cfg.gcp_project or "(not set)",
            "location": cfg.location,
            "bucket": cfg.bucket or "(not set)",
            "storage_backend": cfg.storage_backend,
        }
    )


# ---------------------------------------------------------------------------
# Frontend static files (SPA) — only if frontend/dist exists
# ---------------------------------------------------------------------------

_FRONTEND_DIST = Path(__file__).parent.parent.parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.is_dir():
    # Serve static assets; SPA fallback handled by catch-all below
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        index = _FRONTEND_DIST / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse(status_code=404, content={"error": "Not found"})
