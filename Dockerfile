# syntax=docker/dockerfile:1
# =============================================================================
# KFS Decoder — single-container build for Cloud Run
#
# Stage 1  (node:20-alpine)  : compile React/Vite frontend → /frontend/dist
# Stage 2  (python:3.12-slim): install Python deps, copy app, copy dist
#
# The backend (FastAPI/uvicorn) serves the SPA from /frontend/dist at runtime.
# main.py resolves _FRONTEND_DIST as:
#   Path(__file__).parent.parent.parent.parent / "frontend" / "dist"
# With __file__ = /app/app/main.py this evaluates to /frontend/dist.
# =============================================================================

# ─── Stage 1: build frontend ─────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /build

# Install deps with a clean install (respects package-lock.json)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Copy source and build
COPY frontend/ ./
RUN npm run build
# Artefact: /build/dist/

# ─── Stage 2: Python runtime ──────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Security: run as non-root user
RUN groupadd --gid 1001 appgroup \
 && useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Install Python dependencies (layer-cached before copying app code)
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY backend/app/ ./app/

# Copy compiled frontend to /frontend/dist  (path expected by main.py)
COPY --from=frontend-builder /build/dist/ /frontend/dist/

# Ensure upload temp dir is writable by non-root user
RUN mkdir -p /tmp/kfs_uploads && chown appuser:appgroup /tmp/kfs_uploads

USER appuser

# Cloud Run injects PORT; default 8080
ENV PORT=8080

# Single uvicorn worker — Cloud Run scales via instances
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
