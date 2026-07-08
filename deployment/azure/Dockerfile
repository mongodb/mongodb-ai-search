# =============================================================================
# SearchaaS — FastMCP Server
# Exposes the MCP endpoint at http://0.0.0.0:8001/mcp  (streamable-http)
#
# Build:
#   docker build -t searchaas-mcp .
#
# Run:
#   docker run --env-file .env -p 8001:8001 searchaas-mcp
#
# With a custom config file:
#   docker run --env-file .env \
#              -v /path/to/searchaas.yaml:/app/config/searchaas.yaml:ro \
#              -e SEARCHAAS_CONFIG=/app/config/searchaas.yaml \
#              -p 8001:8001 searchaas-mcp
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — builder: install Python dependencies into an isolated prefix
# -----------------------------------------------------------------------------
ARG BASE_REGISTRY=
FROM ${BASE_REGISTRY}python:3.11-slim AS builder

WORKDIR /build

# Build-time system packages (compilers for native extensions in pymongo, numpy, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies into /install so we can copy them cleanly to the runtime stage
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
ARG BASE_REGISTRY=
FROM ${BASE_REGISTRY}python:3.11-slim AS runtime

# curl is needed for ECS container healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Carry installed packages over from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy only the searchaas package (config yaml lives inside searchaas/config/)
COPY searchaas/ ./searchaas/

# ── Runtime environment ──────────────────────────────────────────────────────
# These are safe defaults. Override sensitive values at runtime via --env-file
# or individual -e flags — do NOT bake secrets into the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    # Source of truth for non-secret config. ${VAR} placeholders are
    # resolved from the container env at load_config() time.
    SEARCHAAS_CONFIG=/app/searchaas/config/searchaas.yaml

# FastAPI on 8000, FastMCP on 8001 — same image, ECS task def picks the CMD
EXPOSE 8000 8001

# Non-root user for least-privilege execution
RUN useradd --no-create-home --shell /bin/false mcp
USER mcp

# Start the FastMCP server (reads config from searchaas/config/searchaas.yaml
# which expands ${ATLAS_URI}, ${GOOGLE_API_KEY}, ${VOYAGE_API_KEY}, etc. from env)
CMD ["python", "-m", "searchaas.mcp_server.server"]
