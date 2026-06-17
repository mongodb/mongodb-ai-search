#!/bin/sh
# =============================================================================
# SearchaaS Frontend — Runtime Config Injection
#
# Runs as part of the nginx docker-entrypoint.d/ chain before nginx starts.
# Writes /usr/share/nginx/html/config.js so the React app can read backend
# URLs from window.SEARCHAAS_API_URL and window.SEARCHAAS_MCP_URL at load
# time — without needing a rebuild.
#
# Environment variables:
#   SEARCHAAS_API_URL   FastAPI REST base URL  (default: http://localhost:8000)
#   SEARCHAAS_MCP_URL   FastMCP endpoint URL   (default: http://localhost:8001/mcp)
# =============================================================================

API_URL="${SEARCHAAS_API_URL:-http://localhost:8000}"
MCP_URL="${SEARCHAAS_MCP_URL:-http://localhost:8001/mcp}"

cat > /usr/share/nginx/html/config.js <<EOF
// Auto-generated at container start — do not edit manually.
window.SEARCHAAS_API_URL = "${API_URL}";
window.SEARCHAAS_MCP_URL = "${MCP_URL}";
EOF

echo "[searchaas-frontend] config.js written:"
echo "  SEARCHAAS_API_URL = ${API_URL}"
echo "  SEARCHAAS_MCP_URL = ${MCP_URL}"
