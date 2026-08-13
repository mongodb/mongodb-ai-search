#!/usr/bin/env bash
# =============================================================================
# agents/ — Cloud Run deployment for the Employee Support Copilot (Next.js)
#
# Deploys agents/employee-support-copilot as a single Cloud Run service whose
# BFF (/api/chat) calls the SearchaaS backend. When SEARCHAAS_BASE_URL points
# at a Vertex AI Agent Engine (Reasoning Engine) resource — the Gemini agent
# platform's managed agent runtime — the service authenticates with Google
# ADC via its attached service account (roles/aiplatform.user), which this
# script creates and grants idempotently.
#
# What it does (idempotent):
#   1. Enables required GCP APIs (Cloud Run, Artifact Registry, IAM).
#   2. Ensures an Artifact Registry docker repo.
#   3. Builds + pushes the standalone Next.js image (linux/amd64).
#   4. Creates the runtime service account and grants roles/aiplatform.user
#      (needed to call reasoningEngines:query on the Agent Engine backend).
#   5. Deploys the Cloud Run service with SEARCHAAS_BASE_URL.
#   6. Smoke-tests POST /api/chat against the live URL.
#
# Prerequisites:
#   - gcloud CLI authenticated (gcloud auth login + application-default login)
#   - Docker with buildx
#   - agents/employee-support-copilot/.env.local with SEARCHAAS_BASE_URL
#     (or pass --searchaas-url)
#
# Usage:
#   ./deployment/google/agents/deploy.sh [--project P] [--region R] \
#       [--service NAME] [--searchaas-url URL]
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
APP_DIR="${REPO_ROOT}/agents/employee-support-copilot"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}==> $*${RESET}"; }

# ── Inputs ────────────────────────────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT:-}"
REGION="${GCP_REGION:-us-central1}"
REPO_NAME="${AR_REPO:-searchaas}"
SERVICE="${COPILOT_SERVICE:-employee-support-copilot}"
SA_NAME="${COPILOT_SA_NAME:-employee-support-copilot-sa}"
SEARCHAAS_URL="${SEARCHAAS_BASE_URL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)       PROJECT_ID="$2";    shift 2 ;;
    --region)        REGION="$2";        shift 2 ;;
    --repo)          REPO_NAME="$2";     shift 2 ;;
    --service)       SERVICE="$2";       shift 2 ;;
    --searchaas-url) SEARCHAAS_URL="$2"; shift 2 ;;
    -h|--help)       grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) error "Unknown argument: $1" ;;
  esac
done

# ── Load the app's .env.local for SEARCHAAS_BASE_URL default (env > file) ─────
if [[ -z "${SEARCHAAS_URL}" && -f "${APP_DIR}/.env.local" ]]; then
  while IFS= read -r _line || [[ -n "${_line}" ]]; do
    _line="${_line%$'\r'}"
    [[ -z "${_line// }" ]] && continue
    [[ "${_line#\#}" != "${_line}" ]] && continue
    _key="${_line%%=*}"; _val="${_line#*=}"
    [[ "${_key}" == "${_line}" ]] && continue
    if [[ "${_val:0:1}" == "'" && "${_val: -1}" == "'" ]]; then _val="${_val:1:${#_val}-2}"; fi
    if [[ "${_val:0:1}" == '"' && "${_val: -1}" == '"' ]]; then _val="${_val:1:${#_val}-2}"; fi
    if [[ "${_key}" == "SEARCHAAS_BASE_URL" && -n "${_val}" ]]; then
      SEARCHAAS_URL="${_val}"
    fi
  done < "${APP_DIR}/.env.local"
  [[ -n "${SEARCHAAS_URL}" ]] && info "Loaded SEARCHAAS_BASE_URL from ${APP_DIR}/.env.local"
fi

# ── Prerequisites ─────────────────────────────────────────────────────────────
step "Checking prerequisites"
command -v gcloud >/dev/null 2>&1 || error "gcloud CLI not found."
command -v docker  >/dev/null 2>&1 || error "Docker not found."
[[ -d "${APP_DIR}" ]] || error "App directory not found: ${APP_DIR}"
[[ -f "${APP_DIR}/package.json" ]] || error "No package.json in ${APP_DIR}"
success "Prerequisites OK"

if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
fi
[[ -n "${PROJECT_ID}" ]] || read -rp "GCP Project ID: " PROJECT_ID
[[ -n "${PROJECT_ID}" ]] || error "GCP Project ID is required."

# ── gcloud credential fallback ────────────────────────────────────────────────
# Some environments (workforce identity / short-lived tokens) reject the
# gcloud user token while application-default credentials (ADC) work fine.
# Probe a cheap API call; on UNAUTHENTICATED, drive gcloud with the ADC token.
# (Same pattern as deployment/google/agent_runtime/deploy.sh.)
GCLOUD=(gcloud)
ADC_TOKEN_FILE=""
trap 'rm -f ${ADC_TOKEN_FILE:+"${ADC_TOKEN_FILE}"}' EXIT
if ! gcloud services list --enabled --project="${PROJECT_ID}" --limit=1 >/dev/null 2>&1; then
  ADC_TOKEN_FILE="$(mktemp -t copilot-adc-token)"
  if gcloud auth application-default print-access-token > "${ADC_TOKEN_FILE}" 2>/dev/null \
     && [[ -s "${ADC_TOKEN_FILE}" ]]; then
    GCLOUD=(gcloud --access-token-file="${ADC_TOKEN_FILE}")
    warn "gcloud user token rejected by the API — falling back to application-default credentials."
  else
    rm -f "${ADC_TOKEN_FILE}"
    echo "[ERROR] gcloud credentials are invalid and no ADC available." >&2
    echo "        Run: gcloud auth login && gcloud auth application-default login" >&2
    exit 1
  fi
fi

[[ -n "${SEARCHAAS_URL}" ]] || error \
  "SEARCHAAS_BASE_URL is required — set it in ${APP_DIR}/.env.local, export it, or pass --searchaas-url."
if [[ "${SEARCHAAS_URL}" == *localhost* || "${SEARCHAAS_URL}" == *127.0.0.1* ]]; then
  error "SEARCHAAS_BASE_URL points at localhost (${SEARCHAAS_URL}) — the deployed service cannot reach your machine. Point it at the Agent Engine or Cloud Run backend URL."
fi

AR_HOST="${REGION}-docker.pkg.dev"
AR_REPO="${AR_HOST}/${PROJECT_ID}/${REPO_NAME}"
IMAGE="${AR_REPO}/${SERVICE}:latest"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

info "Project      : ${PROJECT_ID}"
info "Region       : ${REGION}"
info "Service      : ${SERVICE}"
info "Image        : ${IMAGE}"
info "Backend      : ${SEARCHAAS_URL}"
info "Runtime SA   : ${SA_EMAIL}"

# Agent Engine backends require the aiplatform.user grant on the runtime SA.
IS_AGENT_ENGINE=0
[[ "${SEARCHAAS_URL}" == *aiplatform.googleapis.com*reasoningEngines/* ]] && IS_AGENT_ENGINE=1

# ── 1. Enable required GCP APIs ───────────────────────────────────────────────
step "Enabling GCP APIs..."
"${GCLOUD[@]}" services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  --project="${PROJECT_ID}" --quiet
success "APIs enabled"

# ── 2. Artifact Registry repo (idempotent) ────────────────────────────────────
step "Ensuring Artifact Registry repo '${REPO_NAME}'..."
if ! "${GCLOUD[@]}" artifacts repositories describe "${REPO_NAME}" \
     --location="${REGION}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
  "${GCLOUD[@]}" artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker --location="${REGION}" \
    --project="${PROJECT_ID}" --quiet
  success "Repository created"
else
  info "Repository already exists — skipping"
fi
if [[ -n "${ADC_TOKEN_FILE}" ]]; then
  # The docker credential helper would fetch the (rejected) gcloud user token —
  # log docker in directly with the working ADC token instead.
  gcloud auth application-default print-access-token \
    | docker login -u oauth2accesstoken --password-stdin "https://${AR_HOST}" >/dev/null
  success "Docker logged in to ${AR_HOST} with ADC token"
else
  "${GCLOUD[@]}" auth configure-docker "${AR_HOST}" --quiet
fi

# ── 3. Build + push the image ─────────────────────────────────────────────────
step "Building and pushing image (linux/amd64)..."
if [[ -n "${ADC_TOKEN_FILE}" ]] || ! docker info >/dev/null 2>&1; then
  # The gcloud user token is rejected by Artifact Registry's docker credential
  # helper (workforce identity) and/or no local daemon is available — build and
  # push server-side with Cloud Build instead. Context is the repo root so the
  # -f path works; venv/node_modules/.next are excluded via .gitignore.
  [[ -n "${ADC_TOKEN_FILE}" ]] \
    && warn "gcloud user token unusable for docker push — building with Cloud Build instead." \
    || warn "Docker daemon unavailable — building with Cloud Build instead."
  BUILD_CONFIG="$(mktemp -t copilot-cloudbuild).yaml"
  trap 'rm -f ${ADC_TOKEN_FILE:+"${ADC_TOKEN_FILE}"} "${BUILD_CONFIG}"' EXIT
  cat > "${BUILD_CONFIG}" <<YAML
steps:
- name: 'gcr.io/cloud-builders/docker'
  args: ['build', '-t', '${IMAGE}', '-f', 'deployment/google/agents/Dockerfile', 'agents/employee-support-copilot']
images: ['${IMAGE}']
YAML
  "${GCLOUD[@]}" builds submit --config="${BUILD_CONFIG}" \
    --project="${PROJECT_ID}" "${REPO_ROOT}"
else
  docker buildx build --platform linux/amd64 \
    -f "${SCRIPT_DIR}/Dockerfile" \
    -t "${IMAGE}" --push "${APP_DIR}"
fi
success "Image pushed: ${IMAGE}"

# ── 4. Runtime service account + IAM ──────────────────────────────────────────
step "Ensuring runtime service account '${SA_EMAIL}'..."
if ! "${GCLOUD[@]}" iam service-accounts describe "${SA_EMAIL}" \
     --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
  "${GCLOUD[@]}" iam service-accounts create "${SA_NAME}" \
    --display-name="Employee Support Copilot (Cloud Run runtime)" \
    --project="${PROJECT_ID}" --quiet
  success "Service account created"
else
  info "Service account already exists — skipping"
fi

if [[ "${IS_AGENT_ENGINE}" == "1" ]]; then
  info "Granting roles/aiplatform.user (required to call the Agent Engine)..."
  # IAM is eventually consistent — a freshly created SA may not be visible to
  # SetIamPolicy for a few seconds. Retry a few times before giving up.
  for attempt in 1 2 3 4 5; do
    if "${GCLOUD[@]}" projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/aiplatform.user" \
        --condition=None --quiet >/dev/null 2>&1; then
      break
    fi
    if [[ "${attempt}" == "5" ]]; then
      error "Failed to grant roles/aiplatform.user to ${SA_EMAIL} — grant it manually and re-run."
    fi
    info "SA not yet visible to IAM (eventual consistency) — retrying in 10 s..."
    sleep 10
  done
  success "roles/aiplatform.user granted to ${SA_EMAIL}"
fi

# ── 5. Deploy to Cloud Run ────────────────────────────────────────────────────
step "Deploying Cloud Run service '${SERVICE}'..."
"${GCLOUD[@]}" run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --platform=managed \
  --allow-unauthenticated \
  --service-account="${SA_EMAIL}" \
  --port=8080 \
  --cpu=1 \
  --memory=512Mi \
  --min-instances=0 \
  --max-instances=5 \
  --concurrency=80 \
  --timeout=120 \
  --set-env-vars="SEARCHAAS_BASE_URL=${SEARCHAAS_URL}" \
  --quiet

SERVICE_URL=$("${GCLOUD[@]}" run services describe "${SERVICE}" \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format="value(status.url)")
success "Deployed → ${SERVICE_URL}"

# ── 6. Smoke test ─────────────────────────────────────────────────────────────
step "Smoke-testing POST ${SERVICE_URL}/api/chat ..."
info "(first request may hit a Cloud Run + Agent Engine cold start — allowing up to 90 s)"

_chat_test() {
  curl -sS --max-time 90 -o /tmp/copilot-smoke.json -w "%{http_code}" \
    -X POST -H "Content-Type: application/json" \
    -d '{"query": "When is the payroll processed each month?", "topK": 5}' \
    "${SERVICE_URL}/api/chat" 2>/dev/null || echo "000"
}

HTTP_CODE=$(_chat_test)
# One retry — IAM propagation for a freshly-created SA can take ~60 s.
if [[ "${HTTP_CODE}" != "200" ]]; then
  warn "First attempt returned HTTP ${HTTP_CODE} — retrying in 20 s (IAM propagation/cold start)..."
  sleep 20
  HTTP_CODE=$(_chat_test)
fi

if [[ "${HTTP_CODE}" == "200" ]]; then
  python3 - <<'PY' || true
import json
try:
    d = json.load(open("/tmp/copilot-smoke.json"))
    r = d.get("routing", {})
    print(f"[OK]    smoke test: domain={r.get('domain')} strategy={r.get('strategy')} citations={len(d.get('citations') or [])}")
except Exception as e:
    print(f"[WARN]  smoke test returned 200 but body was not parseable: {e}")
PY
  success "Smoke test passed (HTTP 200)"
else
  warn "Smoke test failed (HTTP ${HTTP_CODE}) — response body:"
  head -c 500 /tmp/copilot-smoke.json 2>/dev/null || true
  warn "The service is deployed; check logs: gcloud run services logs read ${SERVICE} --region=${REGION} --project=${PROJECT_ID}"
fi
rm -f /tmp/copilot-smoke.json

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   Employee Support Copilot — Deployment Complete             ║${RESET}"
echo -e "${BOLD}╠══════════════════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}║${RESET}  App URL : ${GREEN}${SERVICE_URL}${RESET}"
echo -e "${BOLD}║${RESET}  Chat UI : ${GREEN}${SERVICE_URL}/chat${RESET}"
echo -e "${BOLD}║${RESET}  Backend : ${SEARCHAAS_URL}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""
cat <<EOF
Notes:
  - The BFF authenticates to the Agent Engine with the attached service
    account via ADC — no API keys or tokens are baked into the image.
  - To point the app at a different backend (e.g. the Cloud Run searchaas-api
    service), redeploy with:
      SEARCHAAS_BASE_URL=<url> $0
EOF
