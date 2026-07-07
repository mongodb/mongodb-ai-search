# =============================================================================
# SearchaaS — shared backend image for AWS ECS Express Mode
#
# One image serves BOTH the FastAPI (REST) and FastMCP (streamable-http)
# surfaces. The running process is selected by the container `command`:
#
#   FastAPI : uvicorn searchaas.api.app:app --host 0.0.0.0 --port 8000
#   FastMCP : python -m searchaas.mcp_server.server            (port 8001)
#
# The ECS deploy script (deployment/aws/ecs/deploy.sh) picks the command per
# service via the `command` field in ecs/primary-container-*.json.
#
# Build context is the REPOSITORY ROOT (deploy.sh runs
# `docker buildx build -f deployment/aws/Dockerfile <repo-root>`).
#
# Platform note: build with `--platform linux/amd64` for ECS Fargate (x86_64).
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — builder: install Python dependencies into an isolated prefix
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Compilers for native extensions (pymongo C ext, numpy, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install \
        --prefix=/install \
        --no-cache-dir \
        --no-warn-script-location \
        -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 2 — runtime: minimal image with no build tools
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# curl is used by ECS/ALB container health probes if needed
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Carry installed packages over from the builder stage
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy only the application package (config yaml lives in searchaas/config/)
COPY searchaas/ ./searchaas/

# ── Runtime environment ──────────────────────────────────────────────────────
# Safe non-secret defaults. Secrets (ATLAS_URI, provider keys) are injected at
# runtime by the ECS task definition — never baked into the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    SEARCHAAS_CONFIG=/app/searchaas/config/searchaas.yaml

# FastAPI (:8000) and FastMCP (:8001). Only one runs per container; the ECS
# service `command` selects which. EXPOSE is documentation only.
EXPOSE 8000 8001

# Least-privilege: run as a non-root user
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

# Default command = FastAPI. The FastMCP service overrides this in ECS.
CMD ["uvicorn", "searchaas.api.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info"]
