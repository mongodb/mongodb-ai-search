# =============================================================================
# SearchaaS — FastMCP image for Google Cloud Agent Runtime (Vertex AI)
#
# Google Agent Runtime requirements for MCP servers:
#   - Platform: linux/amd64  (Cloud Run default)
#   - MCP server served at   0.0.0.0:8000/mcp  (streamable-http)
#   - Image stored in        Artifact Registry
#   - Auth handled by        Cloud Run + IAP or API Gateway (see deploy.sh)
#
# The existing FastMCP server (searchaas/mcp_server/server.py) already serves
# the MCP endpoint at /mcp via streamable-http. We force it to bind port 8000
# via MCP_PORT so it matches the agent runtime contract (config default is 8001).
#
# Build context is the REPOSITORY ROOT. Build with:
#   docker buildx build --platform linux/amd64 \
#       -f deployment/google/agent_runtime/Dockerfile \
#       -t <ar-uri>:latest --push <repo-root>
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — builder: install Python deps into an isolated prefix
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

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
# Stage 2 — runtime: minimal image
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app
COPY searchaas/ ./searchaas/

# ── Runtime environment ──────────────────────────────────────────────────────
# MCP_PORT=8000 + MCP_HOST=0.0.0.0 → server binds 0.0.0.0:8000 and exposes
# tools at /mcp — the endpoint the Vertex AI agent runtime invokes.
#
# Override ATLAS_URI, ATLAS_DB, embedding/LLM provider at deploy time via
# --set-env-vars or Secret Manager bindings (see deploy.sh).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    SEARCHAAS_CONFIG=/app/searchaas/config/searchaas.yaml \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MCP_TRANSPORT=streamable-http

# Cloud Run / Agent Runtime routes to container port 8000.
EXPOSE 8000

RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

# Serve the FastMCP surface at 0.0.0.0:8000/mcp.
CMD ["python", "-m", "searchaas.mcp_server.server"]
