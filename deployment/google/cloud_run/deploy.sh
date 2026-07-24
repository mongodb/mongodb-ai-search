#!/usr/bin/env bash
# =============================================================================
# SearchaaS — Google Cloud Run Deployment (three-service mode)
#
# Deploys three separate Cloud Run services:
#   1. searchaas-api      FastAPI REST server   (port 8000)
#   2. searchaas-mcp      FastMCP server        (port 8001)
#   3. searchaas-frontend React SPA via nginx   (port 8080)
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - Docker installed and running
#   - A populated .env file at the repo root (copy from .env.example)
#   - Billing enabled on the GCP project
#
# Usage:
#   ./deployment/google/cloud_run/deploy.sh [--project PROJECT_ID] \
#       [--region REGION] [--repo REPO_NAME]
#
# All flags are optional — the script will prompt for any missing values.
# Run from the repo root OR from within this directory; the script resolves
# the repo root automatically.
# =============================================================================
set -euo pipefail

# ── Resolve repo root ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${REPO_ROOT}"

# ── Load .env early so all variables are available without manual export ──────
# Variables already set in the environment take precedence (env > .env).
if [[ -f "${REPO_ROOT}/.env" ]]; then
  while IFS= read -r _line || [[ -n "${_line}" ]]; do
    _line="${_line%$'\r'}"
    [[ -z "${_line// }" ]] && continue
    [[ "${_line#\#}" != "${_line}" ]] && continue
    _key="${_line%%=*}"
    _val="${_line#*=}"
    [[ "${_key}" == "${_line}" ]] && continue
    if [[ "${_val:0:1}" == "'" && "${_val: -1}" == "'" ]]; then _val="${_val:1:${#_val}-2}"; fi
    if [[ "${_val:0:1}" == '"' && "${_val: -1}" == '"' ]]; then _val="${_val:1:${#_val}-2}"; fi
    [[ -z "${!_key+x}" ]] && export "${_key}=${_val}"
  done < "${REPO_ROOT}/.env"
fi

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}==> $*${RESET}"; }

# ── Parse CLI flags ───────────────────────────────────────────────────────────
PROJECT_ID=""
REGION=""
REPO_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region)  REGION="$2";     shift 2 ;;
    --repo)    REPO_NAME="$2";  shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) error "Unknown flag: $1" ;;
  esac
done

# ── Prerequisites check ───────────────────────────────────────────────────────
step "Checking prerequisites"

command -v gcloud >/dev/null 2>&1 || error "gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"
command -v docker  >/dev/null 2>&1 || error "Docker not found. Install: https://docs.docker.com/get-docker/"

if [[ -f "${REPO_ROOT}/.env" ]]; then
  success ".env found at ${REPO_ROOT}/.env"
else
  warn ".env not found — relying on exported environment variables. Run: cp .env.example .env  to create one."
fi
success "Prerequisites OK"

# ── Resolve GCP project ───────────────────────────────────────────────────────
if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
fi

if [[ -z "${PROJECT_ID}" ]]; then
  read -rp "GCP Project ID: " PROJECT_ID
fi

[[ -n "${PROJECT_ID}" ]] || error "GCP Project ID is required."
info "Project : ${PROJECT_ID}"

# ── Resolve region ────────────────────────────────────────────────────────────
if [[ -z "${REGION}" ]]; then
  REGION="us-central1"
  warn "No region specified. Using default: ${REGION}"
  read -rp "Press Enter to accept '${REGION}' or type a different region: " INPUT_REGION
  [[ -n "${INPUT_REGION}" ]] && REGION="${INPUT_REGION}"
fi
info "Region  : ${REGION}"

# ── Resolve Artifact Registry repo ───────────────────────────────────────────
if [[ -z "${REPO_NAME}" ]]; then
  REPO_NAME="searchaas"
fi
AR_HOST="${REGION}-docker.pkg.dev"
AR_REPO="${AR_HOST}/${PROJECT_ID}/${REPO_NAME}"
info "Registry: ${AR_REPO}"

# ── Service names ─────────────────────────────────────────────────────────────
SVC_API="searchaas-api"
SVC_MCP="searchaas-mcp"
SVC_FE="searchaas-frontend"

IMG_API="${AR_REPO}/${SVC_API}:latest"
IMG_MCP="${AR_REPO}/${SVC_MCP}:latest"
IMG_FE="${AR_REPO}/${SVC_FE}:latest"

# ── Enable required GCP APIs ──────────────────────────────────────────────────
step "Enabling GCP APIs (this may take a minute on first run)"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project="${PROJECT_ID}" --quiet
success "APIs enabled"

# ── Create Artifact Registry repository (idempotent) ─────────────────────────
step "Creating Artifact Registry repository '${REPO_NAME}'"
if ! gcloud artifacts repositories describe "${REPO_NAME}" \
     --location="${REGION}" --project="${PROJECT_ID}" --quiet 2>/dev/null; then
  gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --project="${PROJECT_ID}" \
    --quiet
  success "Repository created"
else
  info "Repository already exists — skipping"
fi

# ── Configure Docker auth for Artifact Registry ───────────────────────────────
step "Configuring Docker auth for Artifact Registry"
gcloud auth configure-docker "${AR_HOST}" --quiet
success "Docker auth configured"

# ── Store secrets in Secret Manager ──────────────────────────────────────────
step "Storing every key from .env in Secret Manager"

# Parse .env into ENV_KEYS / ENV_VALUES so the deploy is driven by the file
# rather than a hard-coded allowlist. Every non-comment KEY=VALUE pair becomes
# a Secret Manager secret and is later wired to the Cloud Run services via
# --set-secrets. The YAML's `${KEY}` placeholders are resolved against these.
#
# Note: we use two parallel indexed arrays instead of an associative array
# because macOS ships bash 3.2, which doesn't support `declare -A`.
ENV_KEYS=()
ENV_VALUES=()
_load_env_file() {
  local file="$1"
  [[ -f "${file}" ]] || error "Env file not found: ${file}"
  local line key val existing i
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line// }" ]] && continue
    [[ "${line#\#}" != "${line}" ]] && continue
    key="${line%%=*}"
    val="${line#*=}"
    [[ "${key}" == "${line}" ]] && continue
    if [[ "${val:0:1}" == "'" && "${val: -1}" == "'" ]]; then
      val="${val:1:${#val}-2}"
    elif [[ "${val:0:1}" == '"' && "${val: -1}" == '"' ]]; then
      val="${val:1:${#val}-2}"
    fi
    [[ -z "${val}" ]] && { warn "Skipping ${key} (empty value)"; continue; }
    existing=""
    for ((i=0; i<${#ENV_KEYS[@]}; i++)); do
      if [[ "${ENV_KEYS[i]}" == "${key}" ]]; then existing="1"; break; fi
    done
    [[ -n "${existing}" ]] && continue
    ENV_KEYS+=("${key}")
    ENV_VALUES+=("${val}")
  done < "${file}"
}
_load_env_file "${REPO_ROOT}/.env"
info "Loaded ${#ENV_KEYS[@]} keys from .env: ${ENV_KEYS[*]}"

_upsert_secret() {
  local name="$1" value="$2"
  if gcloud secrets describe "${name}" --project="${PROJECT_ID}" --quiet 2>/dev/null; then
    echo -n "${value}" | gcloud secrets versions add "${name}" \
      --data-file=- --project="${PROJECT_ID}" --quiet
    info "Secret updated: ${name}"
  else
    echo -n "${value}" | gcloud secrets create "${name}" \
      --data-file=- --project="${PROJECT_ID}" --quiet
    success "Secret created: ${name}"
  fi
}

for i in "${!ENV_KEYS[@]}"; do
  _upsert_secret "${ENV_KEYS[i]}" "${ENV_VALUES[i]}"
done

# ── Grant Cloud Run SA access to secrets ──────────────────────────────────────
step "Granting Cloud Run service account access to secrets"
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")
CR_SA="service-${PROJECT_NUMBER}@serverless-robot-prod.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${CR_SA}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None \
  --quiet
success "IAM binding set"

# ── Build and push images ─────────────────────────────────────────────────────
step "Building and pushing FastAPI image → ${IMG_API}"
docker buildx build --platform linux/amd64 \
  -f deployment/google/cloud_run/Dockerfile.api \
  -t "${IMG_API}" --push .
success "FastAPI image built and pushed"

step "Building and pushing FastMCP image → ${IMG_MCP}"
docker buildx build --platform linux/amd64 \
  -f deployment/google/cloud_run/Dockerfile \
  -t "${IMG_MCP}" --push .
success "FastMCP image built and pushed"

step "Building and pushing frontend image → ${IMG_FE}"
docker buildx build --platform linux/amd64 \
  -f deployment/google/cloud_run/Dockerfile.frontend \
  -t "${IMG_FE}" --push .
success "Frontend image built and pushed"

# ── Build --set-secrets flag for every key we pushed above ───────────────────
_secret_pairs=""
for key in "${ENV_KEYS[@]}"; do
  _secret_pairs+="${key}=${key}:latest,"
done
_secret_pairs="${_secret_pairs%,}"

SECRET_FLAGS=()
if [[ -n "${_secret_pairs}" ]]; then
  SECRET_FLAGS+=("--set-secrets=${_secret_pairs}")
fi

# ── Deploy FastAPI ─────────────────────────────────────────────────────────────
step "Deploying FastAPI → Cloud Run service '${SVC_API}'"
gcloud run deploy "${SVC_API}" \
  --image="${IMG_API}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8000 \
  --cpu=4 \
  --memory=8Gi \
  --min-instances=3 \
  --max-instances=20 \
  --concurrency=200 \
  --cpu-boost \
  --timeout=300 \
  --set-env-vars="PYTHONUNBUFFERED=1,\
SEARCHAAS_CONFIG=/app/searchaas/config/searchaas.yaml,\
CONCURRENCY_LIMIT=80,\
ATLAS_MAX_POOL=20,\
RETRIEVAL_MAX_TIME_MS=5000,\
LLM_TIMEOUT_S=5.0,\
EMBED_QUEUE_TIMEOUT_S=4,\
REQUEST_TIMEOUT_S=10,\
HNSW_PREWARM_COLLECTIONS=IT_helpdesk:it_helpdesk_vector_index:text;employee_support:employee_support_vector_index:text,\
ENABLE_SUMMARIZATION=false" \
  "${SECRET_FLAGS[@]}" \
  --quiet

API_URL=$(gcloud run services describe "${SVC_API}" \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format="value(status.url)")
success "FastAPI deployed → ${API_URL}"

# ── Deploy FastMCP ─────────────────────────────────────────────────────────────
step "Deploying FastMCP → Cloud Run service '${SVC_MCP}'"
gcloud run deploy "${SVC_MCP}" \
  --image="${IMG_MCP}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8001 \
  --cpu=4 \
  --memory=8Gi \
  --min-instances=2 \
  --max-instances=20 \
  --concurrency=200 \
  --cpu-boost \
  --timeout=300 \
  --set-env-vars="PYTHONUNBUFFERED=1,SEARCHAAS_CONFIG=/app/searchaas/config/searchaas.yaml,CONCURRENCY_LIMIT=80" \
  "${SECRET_FLAGS[@]}" \
  --quiet

MCP_URL=$(gcloud run services describe "${SVC_MCP}" \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format="value(status.url)")
MCP_ENDPOINT="${MCP_URL}/mcp"
success "FastMCP deployed → ${MCP_ENDPOINT}"

# ── Deploy Frontend ───────────────────────────────────────────────────────────
step "Deploying React frontend → Cloud Run service '${SVC_FE}'"
gcloud run deploy "${SVC_FE}" \
  --image="${IMG_FE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080 \
  --cpu=2 \
  --memory=5Gi \
  --min-instances=0 \
  --max-instances=5 \
  --set-env-vars="SEARCHAAS_API_URL=${API_URL},SEARCHAAS_MCP_URL=${MCP_ENDPOINT}" \
  --quiet

FE_URL=$(gcloud run services describe "${SVC_FE}" \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format="value(status.url)")
success "Frontend deployed → ${FE_URL}"

# ── Patch backend CORS to allow the frontend origin ──────────────────────────
step "Patching CORS on API and MCP services to allow frontend origin"

gcloud run services update "${SVC_API}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --update-env-vars="SEARCHAAS_CORS_ORIGINS=${FE_URL}" \
  --quiet
success "CORS updated on ${SVC_API}"

gcloud run services update "${SVC_MCP}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --update-env-vars="SEARCHAAS_CORS_ORIGINS=${FE_URL}" \
  --quiet
success "CORS updated on ${SVC_MCP}"

# ── Post-deploy verification ──────────────────────────────────────────────────
step "Verifying live /settings on ${SVC_API} reflects searchaas.yaml + .env"
sleep 4
_live_settings=$(curl -sS --max-time 20 "${API_URL}/settings" || true)
if [[ -z "${_live_settings}" ]]; then
  warn "Could not reach ${API_URL}/settings (cold start?). Skipping verification."
else
  _coll=$(echo "${_live_settings}" | grep -oE '"collection"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 || true)
  _db=$(echo "${_live_settings}"   | grep -oE '"database"[[:space:]]*:[[:space:]]*"[^"]*"'   | head -1 || true)
  info "Live /settings → ${_db}  ${_coll}"
  if [[ -z "${_coll}" || -z "${_db}" ]]; then
    warn "Live /settings is missing atlas.database / atlas.collection. " \
         "Check that the .env keys referenced by searchaas.yaml were pushed as secrets."
  else
    success "Backend is serving searchaas.yaml + .env values (collection / database visible)."
  fi
fi

step "Verifying frontend revision has SEARCHAAS_API_URL / SEARCHAAS_MCP_URL set"
_fe_env=$(gcloud run services describe "${SVC_FE}" \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format='value(spec.template.spec.containers[0].env)' 2>/dev/null || true)
if echo "${_fe_env}" | grep -q "SEARCHAAS_API_URL" && echo "${_fe_env}" | grep -q "SEARCHAAS_MCP_URL"; then
  success "Frontend env vars set: SEARCHAAS_API_URL, SEARCHAAS_MCP_URL"
else
  warn "Frontend revision is MISSING SEARCHAAS_API_URL or SEARCHAAS_MCP_URL. " \
       "/config.js will default to http://localhost:8000 — UI will not work in browser."
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║           SearchaaS — Deployment Complete                    ║${RESET}"
echo -e "${BOLD}╠══════════════════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}║${RESET}  Frontend (React UI)                                          ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    ${GREEN}${FE_URL}${RESET}"
echo -e "${BOLD}║${RESET}                                                              ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}  FastAPI REST                                                 ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    ${GREEN}${API_URL}${RESET}"
echo -e "${BOLD}║${RESET}    Docs : ${API_URL}/docs                                     ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    Health: ${API_URL}/health                                  ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}                                                              ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}  FastMCP (Streamable HTTP / JSON-RPC)                        ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    ${GREEN}${MCP_ENDPOINT}${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""
info "The React UI is pre-configured to point at the deployed backends."
info "You can override the URLs in the Settings panel inside the app."
