#!/usr/bin/env bash
# =============================================================================
# Build and push all three SearchaaS images to Azure Container Registry.
#
# Uses `az acr build` so images are built server-side in ACR — no local Docker
# daemon or matching CPU architecture required (works fine from an M-series Mac
# targeting linux/amd64).
#
# Usage:
#   ./scripts/build-and-push.sh <acr-name> [image-tag]
#
# Example:
#   ./scripts/build-and-push.sh searchaasacrabc123 latest
#
# Find the ACR name after deploying infra:
#   az deployment sub show -n searchaas --query properties.outputs.acrName.value -o tsv
# =============================================================================
set -euo pipefail

ACR_NAME="${1:-}"
IMAGE_TAG="${2:-latest}"

if [[ -z "${ACR_NAME}" ]]; then
  echo "ERROR: ACR name is required." >&2
  echo "Usage: $0 <acr-name> [image-tag]" >&2
  exit 1
fi

# Resolve repo root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AZURE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"   # deployment/azure/
REPO_ROOT="$(cd "${AZURE_DIR}/../.." && pwd)"  # repo root

# Base images are imported into the same ACR (see DEPLOYMENT.md) to avoid
# Docker Hub anonymous pull rate limits. Builds pull bases from ACR via the
# BASE_REGISTRY build-arg. Set BASE_REGISTRY="" to pull from Docker Hub instead.
ACR_LOGIN_SERVER="$(az acr show -n "${ACR_NAME}" --query loginServer -o tsv)"
BASE_REGISTRY="${BASE_REGISTRY-${ACR_LOGIN_SERVER}/}"

echo "==> Building images in ACR '${ACR_NAME}' with tag '${IMAGE_TAG}'"
echo "    Repo root:     ${REPO_ROOT}"
echo "    Base registry: ${BASE_REGISTRY:-<docker hub>}"

# --- Import base images into ACR (idempotent — skipped if already present) --
# This avoids Docker Hub anonymous pull rate limits during server-side ACR
# builds. Safe to re-run: `az acr import` is a no-op when the tag already
# exists (exits 0).
echo "==> Importing base images into ACR (skipped if already present)"
# Optional: set DOCKER_HUB_USERNAME + DOCKER_HUB_TOKEN to avoid Docker Hub
# anonymous pull rate limits (429 errors). A free Docker Hub account is enough.
DOCKER_HUB_USERNAME="${DOCKER_HUB_USERNAME:-}"
DOCKER_HUB_TOKEN="${DOCKER_HUB_TOKEN:-}"

_import_base() {
  local src="$1" tag="$2"
  if az acr repository show-tags --name "${ACR_NAME}" --repository "${tag%%:*}" \
       --query "[?@=='${tag##*:}']" -o tsv 2>/dev/null | grep -q .; then
    echo "    [skip] ${tag} already in ACR"
  else
    echo "    [import] docker.io/library/${src} -> ${tag}"
    local auth_flags=()
    if [[ -n "${DOCKER_HUB_USERNAME}" && -n "${DOCKER_HUB_TOKEN}" ]]; then
      auth_flags+=(--username "${DOCKER_HUB_USERNAME}" --password "${DOCKER_HUB_TOKEN}")
    fi
    az acr import \
      --name "${ACR_NAME}" \
      --source "docker.io/library/${src}" \
      --image "${tag}" \
      --force \
      ${auth_flags[@]+"${auth_flags[@]}"}
  fi
}
_import_base "python:3.11-slim"  "python:3.11-slim"
_import_base "node:20-alpine"    "node:20-alpine"
_import_base "nginx:alpine"      "nginx:alpine"
echo "    Base images ready."

# --- MCP server (Dockerfile) ------------------------------------------------
echo "==> [1/3] searchaas-mcp"
az acr build \
  --registry "${ACR_NAME}" \
  --image "searchaas-mcp:${IMAGE_TAG}" \
  --build-arg BASE_REGISTRY="${BASE_REGISTRY}" \
  --file "${AZURE_DIR}/Dockerfile" \
  "${REPO_ROOT}"

# --- REST API (Dockerfile.api) ----------------------------------------------
echo "==> [2/3] searchaas-api"
az acr build \
  --registry "${ACR_NAME}" \
  --image "searchaas-api:${IMAGE_TAG}" \
  --build-arg BASE_REGISTRY="${BASE_REGISTRY}" \
  --file "${AZURE_DIR}/Dockerfile.api" \
  "${REPO_ROOT}"

# --- React UI (searchaas/ui_react/Dockerfile) -------------------------------
echo "==> [3/3] searchaas-ui"
az acr build \
  --registry "${ACR_NAME}" \
  --image "searchaas-ui:${IMAGE_TAG}" \
  --build-arg BASE_REGISTRY="${BASE_REGISTRY}" \
  --file "${REPO_ROOT}/searchaas/ui_react/Dockerfile" \
  "${REPO_ROOT}/searchaas/ui_react"

echo "==> Done. Pushed:"
echo "      searchaas-mcp:${IMAGE_TAG}"
echo "      searchaas-api:${IMAGE_TAG}"
echo "      searchaas-ui:${IMAGE_TAG}"
