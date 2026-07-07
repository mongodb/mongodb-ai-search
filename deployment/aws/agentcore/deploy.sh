#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Deploy the SearchaaS FastMCP backend to Amazon Bedrock AgentCore Runtime.
#
# ⚠  OPT-IN ONLY. This is NOT the default backend deployment. The default is
#    Amazon ECS Express Mode (deployment/aws/ecs/deploy.sh). This script only
#    runs when you explicitly invoke it AND confirm (interactive prompt or
#    --yes flag). It will refuse to proceed otherwise.
#
# What it does (idempotent):
#   1. Ensures an ECR repo, builds the FastMCP image for linux/arm64, pushes it.
#      (AgentCore Runtime mandates ARM64 and MCP served at 0.0.0.0:8000/mcp.)
#   2. Ensures an IAM execution role AgentCore can assume, with permissions to
#      pull the image, write logs, and invoke Bedrock models.
#   3. Calls create-agent-runtime with protocolConfiguration = MCP (create-only).
#   4. Prints the agent runtime ARN + the invocation URL.
#
# Docs:
#   https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html
#   https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/getting-started-custom.html
#
# Prereqs:
#   - AWS CLI v2 with the bedrock-agentcore-control service (recent release)
#   - Docker with buildx
#   - ATLAS_URI + ATLAS_DB exported (plus VOYAGE_API_KEY unless you switch the
#     embedding provider — see README "Configuration options")
# -----------------------------------------------------------------------------
set -euo pipefail

# ── Inputs ────────────────────────────────────────────────────────────────────
: "${AWS_REGION:=us-east-1}"
: "${ATLAS_URI:?Export ATLAS_URI before running (mongodb+srv://...)}"
: "${ATLAS_DB:?Export ATLAS_DB before running (the Atlas database name)}"
CONFIRM="${YES:-}"
RUNTIME_NAME="${AGENTCORE_RUNTIME_NAME:-searchaas_fastmcp}"
REPO="${AGENTCORE_ECR_REPO:-searchaas-agentcore}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# Effective embedding provider (mirror of searchaas.yaml default). The Voyage key
# is only required for the voyageai / voyage_multimodal providers.
EMBEDDINGS_PROVIDER="${EMBEDDINGS_PROVIDER:-voyageai}"
case "$EMBEDDINGS_PROVIDER" in
  voyageai|voyage_multimodal)
    : "${VOYAGE_API_KEY:?Export VOYAGE_API_KEY (EMBEDDINGS_PROVIDER=$EMBEDDINGS_PROVIDER needs a Voyage AI key)}" ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)  CONFIRM="yes"; shift ;;
    --region)  AWS_REGION="$2"; shift 2 ;;
    --name)    RUNTIME_NAME="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# ── Explicit opt-in gate ──────────────────────────────────────────────────────
cat <<'EOF'
============================================================================
  Amazon Bedrock AgentCore Runtime deployment (FastMCP)  —  OPT-IN
============================================================================
This deploys the FastMCP backend to Bedrock AgentCore Runtime. This is a
SEPARATE, non-default target. The default backend deployment is ECS Express
Mode (deployment/aws/ecs/deploy.sh).

Proceeding will: build+push an ARM64 image to ECR, create/patch an IAM role,
and create a Bedrock AgentCore runtime in your account (billable).
EOF

if [[ "$CONFIRM" != "yes" ]]; then
  read -r -p "Type 'deploy-agentcore' to confirm you want to deploy to AgentCore: " ANSWER
  if [[ "$ANSWER" != "deploy-agentcore" ]]; then
    echo "Aborted — AgentCore deployment not confirmed. Nothing was created."
    exit 0
  fi
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO}:${IMAGE_TAG}"
ROLE_NAME="searchaas-agentcore-runtime-role"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

echo "==> Account: $ACCOUNT_ID   Region: $AWS_REGION   Runtime: $RUNTIME_NAME"

# ── 1. IAM execution role for AgentCore Runtime ───────────────────────────────
echo "==> Ensuring IAM execution role '$ROLE_NAME'..."
# Trust policy: allow the AgentCore service to assume the role, scoped to this
# account and region (per AgentCore runtime permissions guidance).
TRUST=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": { "aws:SourceAccount": "${ACCOUNT_ID}" },
      "ArnLike": { "aws:SourceArn": "arn:aws:bedrock-agentcore:${AWS_REGION}:${ACCOUNT_ID}:*" }
    }
  }]
}
JSON
)

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST" >/dev/null
  echo "    created role $ROLE_NAME"
else
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" \
    --policy-document "$TRUST" >/dev/null
  echo "    role $ROLE_NAME already exists (trust policy refreshed)"
fi

# Inline permissions: pull ECR image, write CloudWatch logs, invoke Bedrock.
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name AgentCoreRuntimePolicy \
  --policy-document "$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrPull",
      "Effect": "Allow",
      "Action": [
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "ecr:BatchCheckLayerAvailability"
      ],
      "Resource": "arn:aws:ecr:${AWS_REGION}:${ACCOUNT_ID}:repository/${REPO}"
    },
    {
      "Sid": "EcrAuth",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "Logs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/*"
    },
    {
      "Sid": "Observability",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutMetricData",
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Bedrock",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:Converse",
        "bedrock:ConverseStream"
      ],
      "Resource": "*"
    }
  ]
}
JSON
)" >/dev/null
echo "    attached inline policy AgentCoreRuntimePolicy"

# ── 2. ECR repo + ARM64 image ─────────────────────────────────────────────────
echo "==> Ensuring ECR repo '$REPO'..."
aws ecr describe-repositories --repository-names "$REPO" --region "$AWS_REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$AWS_REGION" >/dev/null

echo "==> Logging in to ECR..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "==> Building FastMCP image for linux/arm64 (AgentCore mandates ARM64)..."
docker buildx build \
  --platform linux/arm64 \
  --provenance=false \
  -f "$SCRIPT_DIR/Dockerfile" \
  -t "$IMAGE_URI" \
  --push \
  "$REPO_ROOT"

# ── 3. Create the AgentCore runtime ───────────────────────────────────────────
# Environment variables passed to the container. AgentCore accepts a map of
# name->value under environmentVariables.
#
# The map is built dynamically: a fixed baseline (Atlas + MCP wiring, forced to
# the AgentCore contract of 0.0.0.0:8000) PLUS any of the SearchaaS config vars
# below that you have exported. Anything left unset falls back to the defaults
# baked into searchaas/config/searchaas.yaml, so you can switch embedding
# provider, LLM, retrieval strategy, indexes, etc. WITHOUT editing this script:
#
#   EMBEDDINGS_PROVIDER=auto ATLAS_VECTOR_INDEX=autoembed_index \
#   ATLAS_DIMENSIONS=-1 ./deploy.sh --yes
#
# See deployment/aws/agentcore/README.md → "Configuration options".

# Config vars forwarded to the runtime IF set in the current environment.
FORWARD_VARS=(
  # --- Atlas index / collection ---
  ATLAS_COLLECTION ATLAS_VECTOR_INDEX ATLAS_SEARCH_INDEX
  ATLAS_TEXT_KEY ATLAS_EMBEDDING_KEY ATLAS_RELEVANCE_FN ATLAS_DIMENSIONS
  # --- Embeddings ---
  EMBEDDINGS_PROVIDER EMBEDDINGS_MODEL EMBEDDINGS_OUTPUT_DIMENSION
  VOYAGE_API_KEY COHERE_API_KEY
  # --- Planner LLM ---
  PLANNER_LLM_PROVIDER PLANNER_MODEL PLANNER_TEMPERATURE PLANNER_DEFAULT_TOP_K
  BEDROCK_MODEL BEDROCK_REGION_NAME BEDROCK_TEMPERATURE
  OPENAI_API_KEY OPENAI_BASE_URL
  AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT AZURE_OPENAI_API_VERSION AZURE_OPENAI_API_KEY
  GOOGLE_API_KEY ANTHROPIC_API_KEY
  # --- Retrieval ---
  RETRIEVAL_DEFAULT_STRATEGY RETRIEVAL_HYBRID_VECTOR_WEIGHT
  RETRIEVAL_HYBRID_FULLTEXT_WEIGHT RETRIEVAL_VECTOR_NUM_CANDIDATES
  # --- Misc / logging ---
  LOG_LEVEL SEARCHAAS_SKIP_PROVIDER_INDEX_CHECK
)

# Baseline (always present). MCP_* are forced to the AgentCore contract and are
# intentionally NOT overridable here.
ENV_JSON=$(
  ATLAS_URI="$ATLAS_URI" ATLAS_DB="$ATLAS_DB" \
  FORWARD_LIST="${FORWARD_VARS[*]}" \
  python3 - <<'PY'
import json, os
env = {
    "ATLAS_URI":       os.environ["ATLAS_URI"],
    "ATLAS_DB":        os.environ["ATLAS_DB"],
    "MCP_HOST":        "0.0.0.0",
    "MCP_PORT":        "8000",
    "MCP_TRANSPORT":   "streamable-http",
    "PYTHONUNBUFFERED": "1",
}
for name in os.environ["FORWARD_LIST"].split():
    val = os.environ.get(name)
    if val is not None and val != "":
        env[name] = val
print(json.dumps(env))
PY
)
echo "==> Forwarding runtime env vars: $(printf '%s' "$ENV_JSON" | python3 -c 'import sys,json;print(", ".join(sorted(json.load(sys.stdin))))')"

ARTIFACT_JSON=$(cat <<JSON
{ "containerConfiguration": { "containerUri": "${IMAGE_URI}" } }
JSON
)

echo "==> Creating AgentCore runtime '$RUNTIME_NAME' (protocol=MCP)..."
CREATE_OUT=$(aws bedrock-agentcore-control create-agent-runtime \
  --region "$AWS_REGION" \
  --agent-runtime-name "$RUNTIME_NAME" \
  --agent-runtime-artifact "$ARTIFACT_JSON" \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --protocol-configuration '{"serverProtocol":"MCP"}' \
  --role-arn "$ROLE_ARN" \
  --environment-variables "$ENV_JSON")
RUNTIME_ARN=$(echo "$CREATE_OUT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["agentRuntimeArn"])')
RUNTIME_ID=$(echo "$CREATE_OUT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["agentRuntimeId"])')

# ── 4. Report ─────────────────────────────────────────────────────────────────
ENCODED_ARN=$(printf '%s' "$RUNTIME_ARN" | sed 's/:/%3A/g; s|/|%2F|g')
echo ""
echo "==> Done."
echo "    Agent Runtime ARN : $RUNTIME_ARN"
echo "    Invocation URL    : https://bedrock-agentcore.${AWS_REGION}.amazonaws.com/runtimes/${ENCODED_ARN}/invocations?qualifier=DEFAULT"
echo ""
cat <<EOF
The runtime provisions asynchronously. Check status with:
  aws bedrock-agentcore-control get-agent-runtime --region $AWS_REGION \\
    --agent-runtime-id ${RUNTIME_ID}

Invoke it (MCP over streamable-http) with a bearer token — see
deployment/aws/agentcore/README.md for a client example.

Tear down with:
  aws bedrock-agentcore-control delete-agent-runtime --region $AWS_REGION \\
    --agent-runtime-id ${RUNTIME_ID}
EOF
