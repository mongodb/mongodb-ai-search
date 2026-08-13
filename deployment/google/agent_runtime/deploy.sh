#!/usr/bin/env bash
# =============================================================================
# SearchaaS — Vertex AI Agent Engine (Reasoning Engine) Deployment
#
# Deploys SearchaaS to the Google Cloud Gemini agent platform's managed agent
# runtime — Vertex AI Agent Engine (a.k.a. Reasoning Engine). This is NOT a
# Cloud Run deployment: no image is built locally and no Cloud Run service is
# created. The Agent Engine service builds and hosts the runtime container
# server-side from the pickled agent + the searchaas package.
#
# What it does (idempotent):
#   1. Enables required GCP APIs (Vertex AI, Storage, Secret Manager, Cloud
#      Build, Artifact Registry, IAM).
#   2. Ensures a regional GCS staging bucket for Agent Engine artifacts.
#   3. Pushes .env secrets to Secret Manager.
#   4. Grants the Agent Engine service agent access to the staging bucket and
#      the secrets.
#   5. Ensures deploy-time Python deps (Vertex AI SDK + cloudpickle) exist in
#      the repo venv.
#   6. Runs deploy_agent_engine.py, which pickles the SearchaaSAgent wrapper,
#      uploads it with the searchaas package, and creates (or updates, when
#      --engine-id is given) the managed Agent Engine.
#   7. Prints the Agent Engine resource name and a sample query invocation.
#
# Docs:
#   https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview
#   https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/develop/custom
#
# Prerequisites:
#   - gcloud CLI authenticated (gcloud auth login + application-default login)
#   - Python venv at repo root (./venv) or set PYTHON=<python3.10-3.13>
#   - ATLAS_URI + ATLAS_DB exported (or set in .env)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

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
    # Strip surrounding quotes
    if [[ "${_val:0:1}" == "'" && "${_val: -1}" == "'" ]]; then _val="${_val:1:${#_val}-2}"; fi
    if [[ "${_val:0:1}" == '"' && "${_val: -1}" == '"' ]]; then _val="${_val:1:${#_val}-2}"; fi
    # Only set if not already set in the environment
    [[ -z "${!_key+x}" ]] && export "${_key}=${_val}"
  done < "${REPO_ROOT}/.env"
  echo "[INFO]  Loaded .env from ${REPO_ROOT}/.env"
else
  echo "[WARN]  No .env file found at ${REPO_ROOT}/.env — relying on exported environment variables."
fi

# ── Inputs ────────────────────────────────────────────────────────────────────
: "${ATLAS_URI:?ATLAS_URI is required — set it in .env or export before running}"
: "${ATLAS_DB:?ATLAS_DB is required — set it in .env or export before running}"

CONFIRM="${YES:-}"
PROJECT_ID="${GCP_PROJECT:-}"
REGION="${GCP_REGION:-us-central1}"
DISPLAY_NAME="${AGENT_ENGINE_DISPLAY_NAME:-searchaas-agent}"
ENGINE_ID="${AGENT_ENGINE_ID:-}"
STAGING_BUCKET="${AGENT_ENGINE_STAGING_BUCKET:-}"
PYTHON_BIN="${PYTHON:-${REPO_ROOT}/venv/bin/python}"

# Effective embedding provider — Voyage key required for voyageai / voyage_multimodal.
EMBEDDINGS_PROVIDER="${EMBEDDINGS_PROVIDER:-voyageai}"
case "$EMBEDDINGS_PROVIDER" in
  voyageai|voyage_multimodal)
    : "${VOYAGE_API_KEY:?VOYAGE_API_KEY is required for EMBEDDINGS_PROVIDER=${EMBEDDINGS_PROVIDER} — set it in .env}" ;;
esac

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}==> $*${RESET}"; }

# ── Parse CLI flags ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)         CONFIRM="yes";        shift ;;
    --project)        PROJECT_ID="$2";      shift 2 ;;
    --region)         REGION="$2";          shift 2 ;;
    --display-name)   DISPLAY_NAME="$2";    shift 2 ;;
    --engine-id)      ENGINE_ID="$2";       shift 2 ;;
    --staging-bucket) STAGING_BUCKET="$2";  shift 2 ;;
    -h|--help)        grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) error "Unknown argument: $1" ;;
  esac
done

# ── Resolve GCP project ───────────────────────────────────────────────────────
if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
fi
if [[ -z "${PROJECT_ID}" ]]; then
  read -rp "GCP Project ID: " PROJECT_ID
fi
[[ -n "${PROJECT_ID}" ]] || error "GCP Project ID is required."

STAGING_BUCKET="${STAGING_BUCKET:-gs://${PROJECT_ID}-agent-engine-staging}"
BUCKET_NAME="${STAGING_BUCKET#gs://}"

[[ -x "${PYTHON_BIN}" ]] || PYTHON_BIN="$(command -v python3 || true)"
[[ -n "${PYTHON_BIN}" ]] || error "No python found — create ./venv or set PYTHON=/path/to/python."

# ── gcloud credential fallback ────────────────────────────────────────────────
# Some environments (workforce identity / short-lived tokens) reject the
# gcloud user token while application-default credentials (ADC) work fine.
# Probe a cheap API call; on UNAUTHENTICATED, drive gcloud with the ADC token.
GCLOUD=(gcloud)
ADC_TOKEN_FILE=""
if ! gcloud services list --enabled --project="${PROJECT_ID}" --limit=1 >/dev/null 2>&1; then
  ADC_TOKEN_FILE="$(mktemp -t searchaas-adc-token)"
  if gcloud auth application-default print-access-token > "${ADC_TOKEN_FILE}" 2>/dev/null \
     && [[ -s "${ADC_TOKEN_FILE}" ]]; then
    GCLOUD=(gcloud --access-token-file="${ADC_TOKEN_FILE}")
    echo "[WARN]  gcloud user token rejected by the API — falling back to application-default credentials."
  else
    rm -f "${ADC_TOKEN_FILE}"
    echo "[ERROR] gcloud credentials are invalid and no ADC available." >&2
    echo "        Run: gcloud auth login && gcloud auth application-default login" >&2
    exit 1
  fi
fi

# ── Explicit opt-in gate ──────────────────────────────────────────────────────
cat <<'EOF'
============================================================================
  SearchaaS — Vertex AI Agent Engine (Reasoning Engine) Deployment
============================================================================
This deploys SearchaaS to the Gemini agent platform's MANAGED agent runtime
(Vertex AI Agent Engine). No Cloud Run service is created. Proceeding will:

  - Enable GCP APIs and create a GCS staging bucket for agent artifacts
  - Push .env secrets to Secret Manager (never baked into the agent)
  - Build and deploy the managed Agent Engine server-side (takes ~5-15 min)
  - Print the Agent Engine resource name + sample query invocation
EOF

if [[ "$CONFIRM" != "yes" ]]; then
  read -r -p "Type 'deploy-agent-engine' to confirm: " ANSWER
  if [[ "$ANSWER" != "deploy-agent-engine" ]]; then
    echo "Aborted — agent engine deployment not confirmed. Nothing was created."
    exit 0
  fi
fi

info "Project        : ${PROJECT_ID}"
info "Region         : ${REGION}"
info "Display name   : ${DISPLAY_NAME}"
info "Staging bucket : ${STAGING_BUCKET}"
info "Python         : ${PYTHON_BIN}"
[[ -n "${ENGINE_ID}" ]] && info "Update target  : engine ${ENGINE_ID} (in-place update)"

# ── 1. Enable required GCP APIs ───────────────────────────────────────────────
step "Enabling GCP APIs..."
"${GCLOUD[@]}" services enable \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  --project="${PROJECT_ID}" --quiet
success "APIs enabled"

# ── 2. Staging bucket ─────────────────────────────────────────────────────────
step "Ensuring staging bucket '${STAGING_BUCKET}'..."
if ! "${GCLOUD[@]}" storage buckets describe "${STAGING_BUCKET}" \
     --project="${PROJECT_ID}" >/dev/null 2>&1; then
  "${GCLOUD[@]}" storage buckets create "${STAGING_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --project="${PROJECT_ID}"
  success "Staging bucket created"
else
  info "Staging bucket already exists — skipping"
fi

# ── 3. Push .env keys to Secret Manager ──────────────────────────────────────
step "Storing secrets in Secret Manager..."

ENV_KEYS=()
ENV_VALUES=()
_load_env_file() {
  local file="$1"
  [[ -f "${file}" ]] || { warn "No .env file found at ${file} — skipping Secret Manager push."; return; }
  local line key val existing i
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line// }" ]] && continue
    [[ "${line#\#}" != "${line}" ]] && continue
    key="${line%%=*}"
    val="${line#*=}"
    [[ "${key}" == "${line}" ]] && continue
    if [[ "${val:0:1}" == "'" && "${val: -1}" == "'" ]]; then val="${val:1:${#val}-2}"; fi
    if [[ "${val:0:1}" == '"' && "${val: -1}" == '"' ]]; then val="${val:1:${#val}-2}"; fi
    [[ -z "${val}" ]] && { warn "Skipping ${key} (empty value)"; continue; }
    existing=""
    for ((i=0; i<${#ENV_KEYS[@]}; i++)); do
      [[ "${ENV_KEYS[i]}" == "${key}" ]] && existing="1" && break
    done
    [[ -n "${existing}" ]] && continue
    ENV_KEYS+=("${key}")
    ENV_VALUES+=("${val}")
  done < "${file}"
}

_upsert_secret() {
  local name="$1" value="$2"
  if "${GCLOUD[@]}" secrets describe "${name}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
    echo -n "${value}" | "${GCLOUD[@]}" secrets versions add "${name}" \
      --data-file=- --project="${PROJECT_ID}" --quiet
    info "Secret updated: ${name}"
  else
    echo -n "${value}" | "${GCLOUD[@]}" secrets create "${name}" \
      --data-file=- --project="${PROJECT_ID}" --quiet
    success "Secret created: ${name}"
  fi
}

# Always upsert Atlas credentials from env
_upsert_secret "ATLAS_URI" "$ATLAS_URI"
_upsert_secret "ATLAS_DB"  "$ATLAS_DB"
[[ -n "${VOYAGE_API_KEY:-}" ]]  && _upsert_secret "VOYAGE_API_KEY"  "$VOYAGE_API_KEY"
[[ -n "${GOOGLE_API_KEY:-}" ]]  && _upsert_secret "GOOGLE_API_KEY"  "$GOOGLE_API_KEY"
[[ -n "${OPENAI_API_KEY:-}" ]]  && _upsert_secret "OPENAI_API_KEY"  "$OPENAI_API_KEY"
[[ -n "${ANTHROPIC_API_KEY:-}" ]] && _upsert_secret "ANTHROPIC_API_KEY" "$ANTHROPIC_API_KEY"
[[ -n "${COHERE_API_KEY:-}" ]]  && _upsert_secret "COHERE_API_KEY"  "$COHERE_API_KEY"

# Also load anything extra from .env
_load_env_file "${REPO_ROOT}/.env"
for i in "${!ENV_KEYS[@]}"; do
  _upsert_secret "${ENV_KEYS[i]}" "${ENV_VALUES[i]}"
done
success "Secrets stored"

# ── 4. Grant the Agent Engine service agent access ───────────────────────────
step "Granting the Agent Engine service agent access to bucket + secrets..."
PROJECT_NUMBER=$("${GCLOUD[@]}" projects describe "${PROJECT_ID}" --format="value(projectNumber)")
RE_SA="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

"${GCLOUD[@]}" projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RE_SA}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None \
  --quiet >/dev/null
info "Granted roles/secretmanager.secretAccessor to ${RE_SA}"

"${GCLOUD[@]}" storage buckets add-iam-policy-binding "${STAGING_BUCKET}" \
  --member="serviceAccount:${RE_SA}" \
  --role="roles/storage.objectViewer" \
  --quiet >/dev/null 2>&1 || \
  warn "Could not grant storage.objectViewer on ${STAGING_BUCKET} — grant manually if needed."
info "Granted roles/storage.objectViewer on ${STAGING_BUCKET}"
success "IAM bindings set"

# ── 5. Deploy-time Python dependencies ───────────────────────────────────────
step "Ensuring deploy-time Python deps (Vertex AI SDK + cloudpickle)..."
if ! "${PYTHON_BIN}" -c "import cloudpickle, vertexai.agent_engines" >/dev/null 2>&1; then
  "${PYTHON_BIN}" -m pip install -q -r "${SCRIPT_DIR}/requirements-deploy.txt"
fi
"${PYTHON_BIN}" -c "import cloudpickle, vertexai.agent_engines" \
  || error "Failed to import vertexai.agent_engines/cloudpickle with ${PYTHON_BIN} — run: ${PYTHON_BIN} -m pip install -r ${SCRIPT_DIR}/requirements-deploy.txt"
success "Deploy deps present ($(${PYTHON_BIN} -c 'import google.cloud.aiplatform as a; print("aiplatform", a.__version__)'))"

# ── 6. Deploy the Agent Engine ────────────────────────────────────────────────
step "Deploying SearchaaS to Vertex AI Agent Engine (server-side build — this takes several minutes)..."

DEPLOY_ARGS=(
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --staging-bucket "${STAGING_BUCKET}"
  --display-name "${DISPLAY_NAME}"
)
[[ -n "${ENGINE_ID}" ]] && DEPLOY_ARGS+=(--engine-id "${ENGINE_ID}")

DEPLOY_LOG="$(mktemp -t searchaas-agent-engine-deploy)"
trap 'rm -f "${DEPLOY_LOG}" ${ADC_TOKEN_FILE:+"${ADC_TOKEN_FILE}"}' EXIT

"${PYTHON_BIN}" "${SCRIPT_DIR}/deploy_agent_engine.py" "${DEPLOY_ARGS[@]}" 2>&1 | tee "${DEPLOY_LOG}"

RESOURCE_NAME=$(grep '^AGENT_ENGINE_RESOURCE=' "${DEPLOY_LOG}" | tail -1 | cut -d= -f2-)
NEW_ENGINE_ID=$(grep '^AGENT_ENGINE_ID=' "${DEPLOY_LOG}" | tail -1 | cut -d= -f2-)

[[ -n "${RESOURCE_NAME}" ]] || error "Deployment finished but no resource name was reported — check the log above."
success "Agent Engine deployed: ${RESOURCE_NAME}"

# ── 7. Report ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   SearchaaS — Vertex AI Agent Engine Deployment Complete     ║${RESET}"
echo -e "${BOLD}╠══════════════════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}║${RESET}  Resource : ${GREEN}${RESOURCE_NAME}${RESET}"
echo -e "${BOLD}║${RESET}  Project  : ${PROJECT_ID}   Region: ${REGION}"
echo -e "${BOLD}║${RESET}  Name     : ${DISPLAY_NAME}   Engine ID: ${NEW_ENGINE_ID}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""

cat <<EOF
Query the agent (Python):

  import vertexai
  from vertexai import agent_engines

  vertexai.init(project="${PROJECT_ID}", location="${REGION}")
  agent = agent_engines.get("${RESOURCE_NAME}")

  # Auto mode — planner picks the retrieval strategy:
  agent.query(input="best rated hotels", top_k=5)

  # Fixed strategy:
  agent.query(input="best rated hotels", top_k=5, strategy="hybrid")

  # Streaming:
  for event in agent.stream_query(input="best rated hotels", top_k=5):
      print(event)

Console:
  https://console.cloud.google.com/vertex-ai/agents/agent-engines?project=${PROJECT_ID}

Redeploy IN PLACE (update the same engine instead of creating a new one):
  AGENT_ENGINE_ID=${NEW_ENGINE_ID} ./deployment/google/agent_runtime/deploy.sh --yes

Tear down:
  venv/bin/python -c "
  import vertexai; from vertexai import agent_engines
  vertexai.init(project='${PROJECT_ID}', location='${REGION}')
  agent_engines.delete('${RESOURCE_NAME}')
  "
  gcloud storage buckets delete ${STAGING_BUCKET} --project ${PROJECT_ID} --quiet
EOF
