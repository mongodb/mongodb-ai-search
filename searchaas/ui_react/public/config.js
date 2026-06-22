// Runtime configuration placeholder.
// In deployed containers this file is overwritten by docker-entrypoint.sh
// with the actual API URLs from environment variables.
// In local dev (npm run dev) null values here cause the app to fall back
// to its built-in localhost defaults (http://localhost:8000, etc.).
window.__SEARCHAAS_CONFIG__ = {
  FASTAPI_URL: null,
  MCP_URL: null,
  MCP_API_KEY: null
};
