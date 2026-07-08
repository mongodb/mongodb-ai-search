#!/usr/bin/env bash
# =============================================================================
# SearchaaS — Google Cloud Agent Runtime Deployment (FastMCP)
#
# ⚠  OPT-IN ONLY. This is NOT the default Google deployment. The default is
#    Cloud Run (deployment/google/cloud_run/deploy.sh). This script deploys
#    ONLY the FastMCP backend as a private Cloud Run service configured for
#    Vertex AI agent connectivity. Run it only when you explicitly want a
#    dedicated agent runtime endpoint.
#
# What it does (idempotent):
#   1. Enables required GCP APIs (Cloud Run, Artifact Registry, IAM, Secret
#      Manager, Vertex AI).
#   2. Creates/ensures a dedicated service account with Vertex AI + Secret
#      Manager roles.
#   3. Pushes .env secrets to Secret Manager.
#   4. Builds and pushes the FastMCP image (linux/amd64) to Artifact Registry.
#   5. Deploys the MCP server to Cloud Run with:
#        - --ingress=internal-and-cloud-load-balancing  (no public internet)
#        - --no-allow-unauthenticated  (requires Google identity tokens)
#        - Dedicated service account bound to Vertex AI roles
#   6. Prints the service URL and an example MCP client invocation using
#      Google identity token auth.
#
# Vertex AI agent connectivity:
#   Vertex AI agents invoke the MCP endpoint over HTTPS with a Google-signed
#   OIDC identity token. The Cloud Run service validates the token automatically.
#   Use the printed URL as the MCP server URL when registering the tool in
#   Vertex AI Agent Builder or calling from ADK-based agents.
#
# Docs:
#   https://cloud.google.com/run/docs/authenticating/service-to-service
#   https://cloud.google.com/vertex-ai/generative-ai/docs/agent-builder/overview
#   https://cloud.google.com/vertex-ai/generative-ai/docs/adk/overview
#
# Prerequisites:
#   - gcloud CLI authenticated (gcloud auth login / application-default login)
#   - Docker + buildx
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
REPO_NAME="${AR_REPO_NAME:-searchaas-agent}"
SVC_NAME="${AGENT_RUNTIME_SERVICE:-searchaas-agent-runtime}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
SA_NAME="${AGENT_SA_NAME:-searchaas-agent-runtime-sa}"

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
    --yes|-y)       CONFIRM="yes";      shift ;;
    --project)      PROJECT_ID="$2";    shift 2 ;;
    --region)       REGION="$2";        shift 2 ;;
    --service)      SVC_NAME="$2";      shift 2 ;;
    -h|--help)      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# ── Explicit opt-in gate ──────────────────────────────────────────────────────
cat <<'EOF'
============================================================================
  SearchaaS — Google Cloud Agent Runtime Deployment (FastMCP)  —  OPT-IN
============================================================================
This deploys the FastMCP backend to a PRIVATE Cloud Run service configured
for Vertex AI agent connectivity. This is a SEPARATE, non-default target.
The default Google deployment is Cloud Run (deployment/google/cloud_run/).

Proceeding will:
  - Build and push an amd64 FastMCP image to Artifact Registry
  - Create a dedicated service account with Vertex AI + Secret Manager roles
  - Deploy a private (no public internet) Cloud Run service
  - Print the MCP endpoint URL for use with Vertex AI Agent Builder / ADK
EOF

if [[ "$CONFIRM" != "yes" ]]; then
  read -r -p "Type 'deploy-agent-runtime' to confirm: " ANSWER
  if [[ "$ANSWER" != "deploy-agent-runtime" ]]; then
    echo "Aborted — agent runtime deployment not confirmed. Nothing was created."
    exit 0
  fi
fi

AR_HOST="${REGION}-docker.pkg.dev"
AR_REPO="${AR_HOST}/${PROJECT_ID}/${REPO_NAME}"
IMAGE_URI="${AR_REPO}/${SVC_NAME}:${IMAGE_TAG}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

info "Project  : ${PROJECT_ID}"
info "Region   : ${REGION}"
info "Service  : ${SVC_NAME}"
info "Image    : ${IMAGE_URI}"
info "SA       : ${SA_EMAIL}"

# ── 1. Enable required GCP APIs ───────────────────────────────────────────────
step "Enabling GCP APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com \
  iam.googleapis.com \
  --project="${PROJECT_ID}" --quiet
success "APIs enabled"

# ── 2. Dedicated service account ──────────────────────────────────────────────
step "Ensuring service account '${SA_NAME}'..."
if ! gcloud iam service-accounts describe "${SA_EMAIL}" \
     --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="SearchaaS Agent Runtime SA" \
    --project="${PROJECT_ID}"
  success "Service account created: ${SA_EMAIL}"
else
  info "Service account already exists — skipping create"
fi

# Grant Vertex AI + Secret Manager roles
for ROLE in \
  roles/aiplatform.user \
  roles/secretmanager.secretAccessor \
  roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet
  info "Granted ${ROLE} to ${SA_EMAIL}"
done
success "IAM roles set"

# ── 3. Artifact Registry repo ─────────────────────────────────────────────────
step "Ensuring Artifact Registry repository '${REPO_NAME}'..."
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

# ── 4. Push .env keys to Secret Manager ──────────────────────────────────────
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

# Grant SA access to secrets
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None \
  --quiet

# ── 5. Build and push the FastMCP image ──────────────────────────────────────
step "Building FastMCP image for linux/amd64 → ${IMAGE_URI}"
docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  -f "${SCRIPT_DIR}/Dockerfile" \
  -t "${IMAGE_URI}" \
  --push \
  "${REPO_ROOT}"
success "Image built and pushed"

# ── 6. Build --set-secrets flag ───────────────────────────────────────────────
# Collect all secret names we pushed into a single --set-secrets flag.
ALL_SECRET_KEYS=("ATLAS_URI" "ATLAS_DB")
[[ -n "${VOYAGE_API_KEY:-}" ]]    && ALL_SECRET_KEYS+=("VOYAGE_API_KEY")
[[ -n "${GOOGLE_API_KEY:-}" ]]    && ALL_SECRET_KEYS+=("GOOGLE_API_KEY")
[[ -n "${OPENAI_API_KEY:-}" ]]    && ALL_SECRET_KEYS+=("OPENAI_API_KEY")
[[ -n "${ANTHROPIC_API_KEY:-}" ]] && ALL_SECRET_KEYS+=("ANTHROPIC_API_KEY")
[[ -n "${COHERE_API_KEY:-}" ]]    && ALL_SECRET_KEYS+=("COHERE_API_KEY")
for k in "${ENV_KEYS[@]}"; do ALL_SECRET_KEYS+=("$k"); done

# Dedup — use a plain indexed array scan (bash 3.2 compatible; no declare -A)
UNIQUE_KEYS=()
for k in "${ALL_SECRET_KEYS[@]}"; do
  _found=""
  for _u in "${UNIQUE_KEYS[@]:-}"; do [[ "$_u" == "$k" ]] && _found="1" && break; done
  [[ -z "$_found" ]] && UNIQUE_KEYS+=("$k")
done

_secret_pairs=""
for k in "${UNIQUE_KEYS[@]}"; do
  _secret_pairs+="${k}=${k}:latest,"
done
_secret_pairs="${_secret_pairs%,}"

SECRET_FLAGS=()
[[ -n "${_secret_pairs}" ]] && SECRET_FLAGS+=("--set-secrets=${_secret_pairs}")

# ── Config vars forwarded via --set-env-vars ──────────────────────────────────
# Non-secret operational config forwarded from the current environment.
FORWARD_VARS=(
  ATLAS_COLLECTION ATLAS_VECTOR_INDEX ATLAS_SEARCH_INDEX
  ATLAS_TEXT_KEY ATLAS_EMBEDDING_KEY ATLAS_RELEVANCE_FN ATLAS_DIMENSIONS
  EMBEDDINGS_PROVIDER EMBEDDINGS_MODEL EMBEDDINGS_OUTPUT_DIMENSION
  PLANNER_LLM_PROVIDER PLANNER_MODEL PLANNER_TEMPERATURE PLANNER_DEFAULT_TOP_K
  RETRIEVAL_DEFAULT_STRATEGY RETRIEVAL_HYBRID_VECTOR_WEIGHT
  RETRIEVAL_HYBRID_FULLTEXT_WEIGHT RETRIEVAL_VECTOR_NUM_CANDIDATES
  LOG_LEVEL SEARCHAAS_SKIP_PROVIDER_INDEX_CHECK
)

ENV_VARS="PYTHONUNBUFFERED=1,SEARCHAAS_CONFIG=/app/searchaas/config/searchaas.yaml"
ENV_VARS+=",MCP_HOST=0.0.0.0,MCP_PORT=8000,MCP_TRANSPORT=streamable-http"
for var in "${FORWARD_VARS[@]}"; do
  val="${!var:-}"
  [[ -n "$val" ]] && ENV_VARS+=",${var}=${val}"
done

# ── 7. Deploy to Cloud Run (private) ──────────────────────────────────────────
step "Deploying '${SVC_NAME}' to Cloud Run (private / agent-runtime mode)..."
gcloud run deploy "${SVC_NAME}" \
  --image="${IMAGE_URI}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --platform=managed \
  --no-allow-unauthenticated \
  --ingress=internal-and-cloud-load-balancing \
  --port=8000 \
  --cpu=2 \
  --memory=5Gi \
  --min-instances=0 \
  --max-instances=10 \
  --service-account="${SA_EMAIL}" \
  --set-env-vars="${ENV_VARS}" \
  "${SECRET_FLAGS[@]}" \
  --quiet

SVC_URL=$(gcloud run services describe "${SVC_NAME}" \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format="value(status.url)")
MCP_ENDPOINT="${SVC_URL}/mcp"

success "Deployed → ${SVC_URL}"

# ── 8. Allow Vertex AI SA to invoke the service ───────────────────────────────
step "Granting Vertex AI service account permission to invoke '${SVC_NAME}'..."
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")
VERTEX_SA="service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com"

gcloud run services add-iam-policy-binding "${SVC_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${VERTEX_SA}" \
  --role="roles/run.invoker" \
  --quiet 2>/dev/null || \
  warn "Could not grant roles/run.invoker to ${VERTEX_SA} — grant manually if needed."

success "IAM invoke binding set (or skipped if Vertex AI SA does not exist yet)"

# ── 9. Report ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║    SearchaaS — Google Agent Runtime Deployment Complete      ║${RESET}"
echo -e "${BOLD}╠══════════════════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}║${RESET}  MCP Endpoint (Streamable HTTP)                               ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}    ${GREEN}${MCP_ENDPOINT}${RESET}"
echo -e "${BOLD}║${RESET}                                                              ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}  Service URL  : ${SVC_URL}                 ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}  Project      : ${PROJECT_ID}                         ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}  Region       : ${REGION}                             ${BOLD}║${RESET}"
echo -e "${BOLD}║${RESET}  Service acct : ${SA_EMAIL}  ${BOLD}║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""

cat <<EOF
The MCP server is private (no unauthenticated access). Callers must present a
Google-signed OIDC identity token in the Authorization header:

  # User account (gcloud auth login) — omit --audiences:
  TOKEN=\$(gcloud auth print-identity-token)
  curl -H "Authorization: Bearer \$TOKEN" "${MCP_ENDPOINT}"

  # Service account impersonation:
  TOKEN=\$(gcloud auth print-identity-token \\
    --impersonate-service-account=${SA_EMAIL} \\
    --audiences="${SVC_URL}")
  curl -H "Authorization: Bearer \$TOKEN" "${MCP_ENDPOINT}"

Use the MCP endpoint URL above when registering this server as a tool in
Vertex AI Agent Builder or from an ADK-based agent:

  Vertex AI Agent Builder:
    Tool type : OpenAPI / MCP
    Server URL: ${MCP_ENDPOINT}
    Auth      : Google OIDC (service-to-service)

  ADK (Python):
    from google.adk.tools.mcp_tool import MCPToolset, StreamableHTTPConnectionParams
    toolset = MCPToolset(
        connection_params=StreamableHTTPConnectionParams(url="${MCP_ENDPOINT}"),
    )

Check status:
  gcloud run services describe ${SVC_NAME} \\
    --region ${REGION} --project ${PROJECT_ID}

Tear down:
  gcloud run services delete ${SVC_NAME} \\
    --region ${REGION} --project ${PROJECT_ID} --quiet
  gcloud artifacts repositories delete ${REPO_NAME} \\
    --location ${REGION} --project ${PROJECT_ID} --quiet
EOF
