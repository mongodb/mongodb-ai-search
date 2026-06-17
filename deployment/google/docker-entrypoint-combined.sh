#!/bin/sh
# =============================================================================
# SearchaaS Combined Container — Entrypoint
#
# Start order:
#   1. Write config.js  (React picks up same-origin API/MCP URLs)
#   2. Start uvicorn    (FastAPI on 127.0.0.1:8000) — background with auto-restart
#   3. Start MCP server (FastMCP on 127.0.0.1:8001) — background with auto-restart
#   4. Wait for FastAPI /health to respond            (up to 120 s)
#   5. Start nginx in foreground                     (Cloud Run TCP probe passes here)
#
# nginx only starts after the backends are healthy, so Cloud Run never routes
# traffic to a not-yet-ready upstream.
# =============================================================================
set -e

export PYTHONPATH=/app
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
cd /app

# ── 1. Write runtime config ───────────────────────────────────────────────────
cat > /usr/share/nginx/html/config.js <<'EOF'
// Auto-generated at container start — do not edit manually.
// Empty string = same origin; nginx proxies /retrieve*, /health, /mcp, etc.
window.SEARCHAAS_API_URL = "";
window.SEARCHAAS_MCP_URL = "/mcp";
EOF
echo "[searchaas] config.js written"

# ── Auto-restart wrapper ──────────────────────────────────────────────────────
# Keeps a background process alive if it exits unexpectedly.
_autorestart() {
  local label="$1"; shift
  while true; do
    echo "[searchaas] Starting ${label}..."
    "$@" || true
    echo "[searchaas] ${label} exited — restarting in 3s"
    sleep 3
  done &
}

# ── 2. Start FastAPI ──────────────────────────────────────────────────────────
_autorestart "uvicorn" \
  uvicorn searchaas.api.app:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 1 \
    --log-level info

# ── 3. Start FastMCP ──────────────────────────────────────────────────────────
_autorestart "mcp" \
  python -m searchaas.mcp_server.server

# ── 4. Wait for FastAPI /health ───────────────────────────────────────────────
echo "[searchaas] Waiting for FastAPI to be ready (up to 120 s)..."
TIMEOUT=120
ELAPSED=0
until python -c \
  "import urllib.request, sys; urllib.request.urlopen('http://127.0.0.1:8000/health'); sys.exit(0)" \
  >/dev/null 2>&1; do
  if [ "${ELAPSED}" -ge "${TIMEOUT}" ]; then
    echo "[searchaas] ERROR: FastAPI did not become healthy within ${TIMEOUT}s — aborting"
    exit 1
  fi
  sleep 2
  ELAPSED=$((ELAPSED + 2))
done
echo "[searchaas] FastAPI is ready (${ELAPSED}s)"

# ── 5. Start nginx in foreground ──────────────────────────────────────────────
# Cloud Run's TCP probe on port 8080 will now pass, and all upstreams are ready.
echo "[searchaas] Starting nginx..."
exec nginx -g "daemon off;"
