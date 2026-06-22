# =============================================================================
# SearchaaS — React Playground UI
# Builds the Vite/React app and serves the static bundle via nginx on port 80.
#
# The UI calls the FastAPI and MCP backends from the browser using URLs that
# are editable at runtime in the Settings panel — so no API base URL needs to
# be baked in at build time. Just point the UI at the deployed backend FQDNs.
#
# Build (locally):
#   docker build -t searchaas-ui searchaas/ui_react
#
# Build (server-side in ACR):
#   az acr build -t searchaas-ui:latest -f searchaas/ui_react/Dockerfile searchaas/ui_react
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — build the static assets
# -----------------------------------------------------------------------------
ARG BASE_REGISTRY=
FROM ${BASE_REGISTRY}node:20-alpine AS build

WORKDIR /app

# Install dependencies first (better layer caching). Use npm ci when a
# lockfile is present for reproducible installs.
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Copy the rest of the source and produce the production build into /app/dist
COPY . .
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 2 — serve with nginx
# -----------------------------------------------------------------------------
ARG BASE_REGISTRY=
FROM ${BASE_REGISTRY}nginx:alpine AS runtime

# SPA-aware nginx config (history fallback + gzip)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static bundle
COPY --from=build /app/dist /usr/share/nginx/html

# Entrypoint generates /config.js from FASTAPI_URL / MCP_URL env vars at start
COPY docker-entrypoint.sh /docker-entrypoint.d/40-searchaas-config.sh
RUN chmod +x /docker-entrypoint.d/40-searchaas-config.sh

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
