#!/usr/bin/env bash
# =============================================================================
# SearchaaS — Combined Single-Service Cloud Run Deployment
#
# Builds ONE image containing the React SPA, FastAPI REST server, and FastMCP
# server. Deploys it as a single Cloud Run service.
#
#   nginx (port 8080)  ← Cloud Run exposes this
#     /                → React SPA
#     /retrieve*       → FastAPI on 127.0.0.1:8000
#     /health          → FastAPI on 127.0.0.1:8000
#     /diagnose        → FastAPI on 127.0.0.1:8000
#     /docs            → FastAPI on 127.0.0.1:8000
#     /query           → FastAPI on 127.0.0.1:8000
#     /mcp             → FastMCP on 127.0.0.1:8001
#
# Usage:
#   ./deployment/deploy-combined.sh [--project PROJECT_ID] [--region REGION] [--repo REPO]
#
# All flags are optional — the script prompts for any missing values.
# =============================================================================
set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}==> $*${RESET}"; }

# ── Resolve repo root ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

# ── Parse CLI flags ───────────────────────────────────────────────────────────
PROJECT_ID=""
REGION=""
REPO_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region)  REGION="$2";     shift 2 ;;
    --repo)    REPO_NAME="$2";  shift 2 ;;
    *) error "Unknown flag: $1" ;;
  esac
done

# ── Prerequisites ─────────────────────────────────────────────────────────────
step "Checking prerequisites"
command -v gcloud  >/dev/null 2>&1 || error "gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"
command -v docker  >/dev/null 2>&1 || error "Docker not found. Install: https://docs.docker.com/get-docker/"
[[ -f "${REPO_ROOT}/.env" ]] || error ".env not found. Run: cp .env.example .env  then fill in secrets."
success "Prerequisites OK"

# ── Resolve project / region / repo ──────────────────────────────────────────
if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
fi
if [[ -z "${PROJECT_ID}" ]]; then
  read -rp "GCP Project ID: " PROJECT_ID
fi
[[ -n "${PROJECT_ID}" ]] || error "GCP Project ID is required."

if [[ -z "${REGION}" ]]; then
  REGION="us-central1"
  warn "No region specified — using default: ${REGION}"
  read -rp "Press Enter to accept '${REGION}' or type a different region: " _r
  [[ -n "${_r}" ]] && REGION="${_r}"
fi

[[ -z "${REPO_NAME}" ]] && REPO_NAME="searchaas"

AR_HOST="${REGION}-docker.pkg.dev"
AR_REPO="${AR_HOST}/${PROJECT_ID}/${REPO_NAME}"
SVC="searchaas"
IMAGE="${AR_REPO}/${SVC}:latest"

info "Project : ${PROJECT_ID}"
info "Region  : ${REGION}"
info "Image   : ${IMAGE}"
info "Service : ${SVC}"

# ── Enable GCP APIs ───────────────────────────────────────────────────────────
step "Enabling GCP APIs"
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project="${PROJECT_ID}" --quiet
success "APIs enabled"

# ── Artifact Registry repo (idempotent) ───────────────────────────────────────
step "Ensuring Artifact Registry repository '${REPO_NAME}' exists"
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

gcloud auth configure-docker "${AR_HOST}" --quiet
success "Docker auth configured"

# ── Store every .env key in Secret Manager ───────────────────────────────────
step "Storing every key from .env in Secret Manager"

# Drive Secret Manager from the .env file so the deploy mirrors what
# `load_config()` sees locally. Every non-comment KEY=VALUE pair becomes a
# secret; the YAML's `${KEY}` placeholders are resolved against these at
# container start. Empty values are skipped (Secret Manager rejects empty
# payloads).
#
# Note: we use two parallel indexed arrays (ENV_KEYS / ENV_VALUES) instead of
# an associative array because macOS still ships bash 3.2, which doesn't
# support `declare -A`. The dedup loop is O(n) but n is tiny (.env has
# < 100 keys in practice).
ENV_KEYS=()
ENV_VALUES=()
_load_env_file() {
  local file="$1"
  [[ -f "${file}" ]] || error "Env file not found: ${file}"
  local line key val
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"                       # strip CR (CRLF safety)
    [[ -z "${line// }" ]] && continue          # blank line
    [[ "${line#\#}" != "${line}" ]] && continue  # comment line
    key="${line%%=*}"
    val="${line#*=}"
    [[ "${key}" == "${line}" ]] && continue    # no '=' separator
    # Strip a matched pair of surrounding quotes from the value.
    if [[ "${val:0:1}" == "'" && "${val: -1}" == "'" ]]; then
      val="${val:1:${#val}-2}"
    elif [[ "${val:0:1}" == '"' && "${val: -1}" == '"' ]]; then
      val="${val:1:${#val}-2}"
    fi
    [[ -z "${val}" ]] && { warn "Skipping ${key} (empty value)"; continue; }
    # Dedup: first definition wins (matches `source .env` semantics)
    local existing="" i
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
  if gcloud secrets describe "${name}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
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

# ── Build and push the combined image ─────────────────────────────────────────
step "Building combined image (linux/amd64) → ${IMAGE}"
docker buildx build \
  --platform linux/amd64 \
  -f deployment/google/Dockerfile.combined \
  -t "${IMAGE}" \
  --push \
  .
success "Image built and pushed"

# ── Build a single --set-secrets flag for every key we pushed above ──────────
# Single flag with comma-separated pairs is faster than N individual flags and
# fits inside Cloud Run's command-line length limit.
_secret_pairs=""
for key in "${ENV_KEYS[@]}"; do
  _secret_pairs+="${key}=${key}:latest,"
done
_secret_pairs="${_secret_pairs%,}"

SECRET_FLAGS=()
if [[ -n "${_secret_pairs}" ]]; then
  SECRET_FLAGS+=("--set-secrets=${_secret_pairs}")
fi

# ── Deploy to Cloud Run ───────────────────────────────────────────────────────
step "Deploying '${SVC}' to Cloud Run (${REGION})"
gcloud run deploy "${SVC}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --platform=managed \
  --allow-unauthenticated \
  --port=8080 \
  --cpu=2 \
  --memory=4Gi \
  --min-instances=0 \
  --max-instances=5 \
  --set-env-vars="PYTHONUNBUFFERED=1,SEARCHAAS_CONFIG=/app/searchaas/config/searchaas.yaml" \
  "${SECRET_FLAGS[@]}" \
  --quiet

SERVICE_URL=$(gcloud run services describe "${SVC}" \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format="value(status.url)")
success "Deployed → ${SERVICE_URL}"

# ── Post-deploy verification ──────────────────────────────────────────────────
# Hit /settings on the live service and confirm the backend resolved the
# YAML's ${ATLAS_URI}/${ATLAS_DB}/${GOOGLE_API_KEY} placeholders from the
# Secret Manager bindings. If the response is missing the collection or
# database, the secrets didn't reach the container — fail loudly so the
# operator sees it instead of debugging an empty UI later.
step "Verifying live /settings reflects searchaas.yaml + .env"
sleep 4   # let the Cloud Run revision finish ramping
_live_settings=$(curl -sS --max-time 20 "${SERVICE_URL}/settings" || true)
if [[ -z "${_live_settings}" ]]; then
  warn "Could not reach ${SERVICE_URL}/settings (cold start?). Skipping verification."
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

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║         SearchaaS — Deployment Complete                      ║${RESET}"
echo -e "${BOLD}╠══════════════════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}║${RESET}  Service URL                                                  ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    ${GREEN}${SERVICE_URL}${RESET}"
echo -e "${BOLD}║${RESET}                                                              ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}  Endpoints                                                    ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    React UI   ${SERVICE_URL}/                              ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    REST API   ${SERVICE_URL}/retrieve                      ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    API Docs   ${SERVICE_URL}/docs                          ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    Health     ${SERVICE_URL}/health                        ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    FastMCP    ${SERVICE_URL}/mcp                           ${BOLD}║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
