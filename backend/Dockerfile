# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# KFS Decoder — Cloud Run container
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

WORKDIR /app

# Install dependencies first (layer-cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/

# Cloud Run injects PORT; default to 8080
ENV PORT=8080

# Uvicorn — single worker (Cloud Run scales via instances, not threads)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
