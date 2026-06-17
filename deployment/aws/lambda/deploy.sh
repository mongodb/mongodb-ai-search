#!/usr/bin/env bash
# =============================================================================
# Deploy SearchaaS (FastAPI + FastMCP) as two AWS Lambda functions, each
# exposed publicly via a Lambda Function URL.
#
#   searchaas-fastapi   ->  buffered Function URL  (REST API)
#   searchaas-fastmcp   ->  RESPONSE_STREAM URL    (MCP streamable-http)
#
# Both functions are container-image Lambdas built from
#   deployment/aws/lambda/Dockerfile.fastapi
#   deployment/aws/lambda/Dockerfile.fastmcp
# and run AWS Lambda Web Adapter, so the FastAPI/Uvicorn and FastMCP/Starlette
# servers run unchanged.
#
# Prereqs:
#   - AWS CLI v2, Docker with buildx
#   - ATLAS_URI / ATLAS_DB exported (and any other ${VAR} your searchaas.yaml
#     references — GOOGLE_API_KEY, VOYAGE_API_KEY, AWS_PROFILE, ...)
#
# Idempotent: re-running updates the image, code, env, and Function URL config.
# =============================================================================
set -euo pipefail

# ── Inputs (override via env) ────────────────────────────────────────────────
: "${AWS_REGION:=us-east-1}"
: "${ATLAS_URI:?Export ATLAS_URI before running (mongodb+srv://...)}"
: "${ATLAS_DB:?Export ATLAS_DB before running}"
: "${CORS_ORIGINS:=*}"          # SEARCHAAS_CORS_ORIGINS passthrough
: "${LAMBDA_MEMORY_MB:=3008}"   # heavy deps + embeddings; bump if cold-start OOMs
: "${LAMBDA_TIMEOUT_SEC:=120}"  # max 900
: "${IMAGE_TAG:=latest}"

# Optional secrets — only injected into the function env if set in the shell.
OPTIONAL_ENV_KEYS=(
  GOOGLE_API_KEY VOYAGE_API_KEY OPENAI_API_KEY COHERE_API_KEY
  ANTHROPIC_API_KEY HUGGINGFACEHUB_API_TOKEN AWS_PROFILE
)

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

REPO_FASTAPI=searchaas-fastapi
REPO_FASTMCP=searchaas-fastmcp
FN_FASTAPI=searchaas-fastapi
FN_FASTMCP=searchaas-fastmcp
ROLE_NAME=searchaas-lambda-role

ECR_HOST="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMG_FASTAPI="${ECR_HOST}/${REPO_FASTAPI}:${IMAGE_TAG}"
IMG_FASTMCP="${ECR_HOST}/${REPO_FASTMCP}:${IMAGE_TAG}"

echo "==> Account: $ACCOUNT_ID  Region: $AWS_REGION"

# ── 1. IAM execution role ────────────────────────────────────────────────────
echo "==> Ensuring Lambda execution role: $ROLE_NAME"
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST" >/dev/null
  echo "    created role $ROLE_NAME"
else
  echo "    role $ROLE_NAME already exists"
fi

# Basic execution (CloudWatch Logs) + Bedrock invoke (parity with ECS task role).
aws iam attach-role-policy --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
  2>/dev/null || true

aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name BedrockInvoke \
  --policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Action":[
      "bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse","bedrock:ConverseStream"
    ],"Resource":"*"}]}' >/dev/null

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
echo "    role arn: $ROLE_ARN"

# IAM eventual consistency: wait for the trust policy to propagate before
# Lambda's CreateFunction call accepts the role.
echo "    waiting 10s for IAM propagation..."
sleep 10

# ── 2. ECR repos + build/push both images ────────────────────────────────────
ensure_repo() {
  local name=$1
  aws ecr describe-repositories --repository-names "$name" --region "$AWS_REGION" \
    >/dev/null 2>&1 || \
    aws ecr create-repository --repository-name "$name" --region "$AWS_REGION" \
      --image-scanning-configuration scanOnPush=true >/dev/null
}
ensure_repo "$REPO_FASTAPI"
ensure_repo "$REPO_FASTMCP"

echo "==> Logging in to ECR (private)..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_HOST"

# ECR Public hosts the Lambda Web Adapter base image. Anonymous pulls are
# rate-limited and frequently return 403 from Docker Desktop; authenticating
# (ecr-public lives only in us-east-1) fixes it.
echo "==> Logging in to ECR Public (for aws-lambda-adapter)..."
aws ecr-public get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin public.ecr.aws

# Lambda runs x86_64 by default; force linux/amd64 to match.
echo "==> Building + pushing FastAPI image: $IMG_FASTAPI"
docker buildx build \
  --platform linux/amd64 --provenance=false \
  -f "$SCRIPT_DIR/Dockerfile.fastapi" \
  -t "$IMG_FASTAPI" --push "$REPO_ROOT"

echo "==> Building + pushing FastMCP image: $IMG_FASTMCP"
docker buildx build \
  --platform linux/amd64 --provenance=false \
  -f "$SCRIPT_DIR/Dockerfile.fastmcp" \
  -t "$IMG_FASTMCP" --push "$REPO_ROOT"

# ── 3. Render environment variables payload ──────────────────────────────────
# Build a JSON object of env vars to pass to the Lambda. We do it in Python to
# get bullet-proof escaping of secrets that may contain quotes or backslashes.
build_env_json() {
  python3 - "$@" <<'PY'
import json, os, sys
keys = sys.argv[1:]
out = {}
for k in keys:
    v = os.environ.get(k)
    if v:
        out[k] = v
print(json.dumps({"Variables": out}))
PY
}

ENV_KEYS=(ATLAS_URI ATLAS_DB SEARCHAAS_CORS_ORIGINS "${OPTIONAL_ENV_KEYS[@]}")
export SEARCHAAS_CORS_ORIGINS="$CORS_ORIGINS"
ENV_JSON=$(build_env_json "${ENV_KEYS[@]}")

# ── 4. Create or update each Lambda ──────────────────────────────────────────
create_or_update_fn() {
  local name=$1 image=$2
  if aws lambda get-function --function-name "$name" --region "$AWS_REGION" \
       >/dev/null 2>&1; then
    echo "==> Updating function: $name"
    aws lambda update-function-code --region "$AWS_REGION" \
      --function-name "$name" --image-uri "$image" --publish >/dev/null
    aws lambda wait function-updated --region "$AWS_REGION" --function-name "$name"
    aws lambda update-function-configuration --region "$AWS_REGION" \
      --function-name "$name" \
      --role "$ROLE_ARN" \
      --memory-size "$LAMBDA_MEMORY_MB" \
      --timeout "$LAMBDA_TIMEOUT_SEC" \
      --environment "$ENV_JSON" >/dev/null
    aws lambda wait function-updated --region "$AWS_REGION" --function-name "$name"
  else
    echo "==> Creating function: $name"
    aws lambda create-function --region "$AWS_REGION" \
      --function-name "$name" \
      --package-type Image \
      --code "ImageUri=$image" \
      --role "$ROLE_ARN" \
      --memory-size "$LAMBDA_MEMORY_MB" \
      --timeout "$LAMBDA_TIMEOUT_SEC" \
      --environment "$ENV_JSON" \
      --architectures x86_64 >/dev/null
    aws lambda wait function-active --region "$AWS_REGION" --function-name "$name"
  fi
}

create_or_update_fn "$FN_FASTAPI" "$IMG_FASTAPI"
create_or_update_fn "$FN_FASTMCP" "$IMG_FASTMCP"

# ── 5. Function URLs (one buffered, one streaming) ──────────────────────────
# CORS on the URL itself is permissive — the application also enforces CORS
# via SEARCHAAS_CORS_ORIGINS, which is the source of truth.
URL_CORS='{
  "AllowOrigins":["*"],
  "AllowMethods":["*"],
  "AllowHeaders":["*"],
  "ExposeHeaders":["mcp-session-id","x-request-id"],
  "MaxAge":86400
}'

ensure_function_url() {
  local name=$1 mode=$2   # BUFFERED | RESPONSE_STREAM
  if aws lambda get-function-url-config --function-name "$name" \
       --region "$AWS_REGION" >/dev/null 2>&1; then
    echo "==> Updating Function URL for $name (mode=$mode)"
    aws lambda update-function-url-config --region "$AWS_REGION" \
      --function-name "$name" \
      --auth-type NONE \
      --invoke-mode "$mode" \
      --cors "$URL_CORS" >/dev/null
  else
    echo "==> Creating Function URL for $name (mode=$mode)"
    aws lambda create-function-url-config --region "$AWS_REGION" \
      --function-name "$name" \
      --auth-type NONE \
      --invoke-mode "$mode" \
      --cors "$URL_CORS" >/dev/null
    # Auth NONE requires an explicit resource policy granting public invoke.
    aws lambda add-permission --region "$AWS_REGION" \
      --function-name "$name" \
      --statement-id FunctionURLAllowPublicAccess \
      --action lambda:InvokeFunctionUrl \
      --principal "*" \
      --function-url-auth-type NONE >/dev/null 2>&1 || true
  fi
}

ensure_function_url "$FN_FASTAPI" "BUFFERED"
ensure_function_url "$FN_FASTMCP" "RESPONSE_STREAM"

# ── 6. Output the public URLs ────────────────────────────────────────────────
echo ""
echo "==> Done. Public endpoints:"
for fn in "$FN_FASTAPI" "$FN_FASTMCP"; do
  url=$(aws lambda get-function-url-config --region "$AWS_REGION" \
          --function-name "$fn" --query 'FunctionUrl' --output text)
  printf "    %-22s %s\n" "$fn" "$url"
done

cat <<'EOF'

Quick smoke tests:

  # FastAPI
  curl -s "$FASTAPI_URL/health" | jq

  # FastMCP (streamable-http) — initialize handshake
  curl -N -X POST "$FASTMCP_URL/mcp" \
       -H 'Content-Type: application/json' \
       -H 'Accept: application/json, text/event-stream' \
       -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2024-11-05",
                      "capabilities":{},
                      "clientInfo":{"name":"curl","version":"0"}}}'
EOF
