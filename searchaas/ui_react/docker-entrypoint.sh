#!/bin/sh
# Runs via nginx's /docker-entrypoint.d/ mechanism BEFORE nginx starts.
# Generates runtime config.js from environment variables so the same image can
# target any deployment without a rebuild. Set FASTAPI_URL and MCP_URL as
# Container App env vars to point the UI at the deployed backends.
set -e

CONFIG_PATH=/usr/share/nginx/html/config.js

# Write JS-safe values: quoted string for set vars, null for unset vars.
# This prevents empty-string from overriding the app's built-in localhost defaults.
_jsval() { [ -n "${1:-}" ] && printf '"%s"' "$1" || printf 'null'; }

cat > "$CONFIG_PATH" <<EOF
window.__SEARCHAAS_CONFIG__ = {
  FASTAPI_URL: $(_jsval "${FASTAPI_URL:-}"),
  MCP_URL: $(_jsval "${MCP_URL:-}"),
  MCP_API_KEY: $(_jsval "${MCP_API_KEY:-}")
};
EOF

echo "[searchaas] wrote $CONFIG_PATH (FASTAPI_URL=${FASTAPI_URL:-<empty>} MCP_URL=${MCP_URL:-<empty>} MCP_API_KEY=$( [ -n "${MCP_API_KEY:-}" ] && echo '<set>' || echo '<empty>'))"
