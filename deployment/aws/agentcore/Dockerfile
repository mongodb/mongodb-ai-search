# =============================================================================
# SearchaaS — FastMCP image for Amazon Bedrock AgentCore Runtime
#
# AgentCore Runtime requirements for MCP servers:
#   - Platform MUST be linux/arm64
#   - MCP server MUST be served at 0.0.0.0:8000/mcp (streamable-http)
#   - Image MUST live in Amazon ECR
#   (see https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html)
#
# We reuse the existing FastMCP server (searchaas/mcp_server/server.py), which
# already serves the MCP endpoint at /mcp via streamable-http. We force it to
# bind port 8000 through the MCP_PORT env var so it matches the AgentCore
# contract (the server config reads ${MCP_PORT:-8001}).
#
# Build context is the REPOSITORY ROOT. Build with:
#   docker buildx build --platform linux/arm64 \
#       -f deployment/aws/agentcore/Dockerfile -t <ecr-uri>:latest --push <repo-root>
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — builder (arm64): install Python deps into an isolated prefix
# -----------------------------------------------------------------------------
FROM --platform=linux/arm64 python:3.11-slim AS builder

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
# Stage 2 — runtime (arm64)
# -----------------------------------------------------------------------------
FROM --platform=linux/arm64 python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app
COPY searchaas/ ./searchaas/

# ── Runtime environment ──────────────────────────────────────────────────────
# MCP_PORT=8000 + MCP_HOST=0.0.0.0 → server binds 0.0.0.0:8000, and FastMCP's
# streamable-http app exposes the tools at /mcp — exactly what AgentCore expects.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    SEARCHAAS_CONFIG=/app/searchaas/config/searchaas.yaml \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MCP_TRANSPORT=streamable-http

# AgentCore Runtime routes to container port 8000.
EXPOSE 8000

RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

# Serve the FastMCP surface at 0.0.0.0:8000/mcp.
CMD ["python", "-m", "searchaas.mcp_server.server"]
