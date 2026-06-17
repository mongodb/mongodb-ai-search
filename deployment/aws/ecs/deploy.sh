#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Deploy SearchaaS (FastAPI + FastMCP) using Amazon ECS Express Mode.
#
# Express Mode auto-provisions an internet-facing ALB and an HTTPS URL of the
# form  https://<service>.ecs.<region>.on.aws  for each service.
#
# Docs: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-getting-started.html
#
# Prereqs:
#   - AWS CLI v2 configured (`aws configure`)
#   - Docker installed
#   - ATLAS_URI exported in your shell
# -----------------------------------------------------------------------------
set -euo pipefail

# ── Inputs ────────────────────────────────────────────────────────────────────
: "${AWS_REGION:=us-east-1}"
: "${ATLAS_URI:?Export ATLAS_URI before running (mongodb+srv://...)}"
: "${ATLAS_DB:?Export ATLAS_DB before running (the Atlas database name)}"
# Comma-separated list of origins the React UI will call from.
# Use "*" to allow ALL origins (handy during initial setup).
: "${CORS_ORIGINS:=*}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REPO=searchaas
IMAGE_TAG=latest
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO}:${IMAGE_TAG}"

EXEC_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/ecsTaskExecutionRole"
INFRA_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/ecsInfrastructureRoleForExpressServices"
TASK_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/searchaas-task-role"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Account: $ACCOUNT_ID   Region: $AWS_REGION"

# ── 1. IAM roles (idempotent) ─────────────────────────────────────────────────
echo "==> Ensuring IAM roles exist..."

create_role_if_missing() {
  local name=$1 trust=$2
  if ! aws iam get-role --role-name "$name" >/dev/null 2>&1; then
    aws iam create-role --role-name "$name" --assume-role-policy-document "$trust" >/dev/null
    echo "    created role $name"
  else
    echo "    role $name already exists"
  fi
}

TRUST_TASKS='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
TRUST_ECS='{"Version":"2012-10-17","Statement":[{"Sid":"AllowAccessInfrastructureForECSExpressServices","Effect":"Allow","Principal":{"Service":"ecs.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

create_role_if_missing ecsTaskExecutionRole "$TRUST_TASKS"
create_role_if_missing ecsInfrastructureRoleForExpressServices "$TRUST_ECS"
create_role_if_missing searchaas-task-role "$TRUST_TASKS"

aws iam attach-role-policy --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy 2>/dev/null || true
aws iam attach-role-policy --role-name ecsInfrastructureRoleForExpressServices \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices 2>/dev/null || true

# Inline Bedrock policy for the task role (covers InvokeModel + streaming variants)
aws iam put-role-policy --role-name searchaas-task-role --policy-name BedrockInvoke --policy-document '{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream"
    ],
    "Resource": "*"
  }]
}' >/dev/null
echo "    attached Bedrock policy to searchaas-task-role"

# ── 2. ECR repo + image ───────────────────────────────────────────────────────
echo "==> Ensuring ECR repo exists..."
aws ecr describe-repositories --repository-names "$REPO" --region "$AWS_REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$AWS_REGION" >/dev/null

echo "==> Logging in to ECR..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "==> Building image for linux/amd64 (ECS Fargate runs x86_64 by default)..."
# buildx + --push uploads the correctly-platformed image in one shot,
# avoiding the local Docker daemon trying to load a cross-arch image.
docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  -t "$IMAGE_URI" \
  --push \
  "$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── 3. Build --primary-container JSON payloads with placeholders filled in ───
echo "==> Rendering primary-container payloads..."
render() {
  local src=$1 dst=$2
  sed -e "s|<ACCOUNT_ID>|${ACCOUNT_ID}|g" \
      -e "s|<REGION>|${AWS_REGION}|g" \
      -e "s|<ATLAS_URI>|${ATLAS_URI}|g" \
      -e "s|<ATLAS_DB>|${ATLAS_DB}|g" \
      -e "s|<CORS_ORIGINS>|${CORS_ORIGINS}|g" \
      "$src" > "$dst"
}
render "$SCRIPT_DIR/primary-container-fastapi.json" "$SCRIPT_DIR/.rendered-fastapi.json"
render "$SCRIPT_DIR/primary-container-fastmcp.json" "$SCRIPT_DIR/.rendered-fastmcp.json"

# ── 4. Create the two Express Mode services ──────────────────────────────────
create_or_update_service() {
  local name=$1 container_json=$2 health_path=$3
  local arn
  arn=$(aws ecs list-services --cluster default --region "$AWS_REGION" \
          --query "serviceArns[?contains(@, ':service/default/${name}')]|[0]" \
          --output text 2>/dev/null || echo "None")

  if [[ "$arn" == "None" || -z "$arn" ]]; then
    echo "==> Creating Express Mode service: $name"
    aws ecs create-express-gateway-service \
      --region "$AWS_REGION" \
      --service-name "$name" \
      --execution-role-arn "$EXEC_ROLE_ARN" \
      --infrastructure-role-arn "$INFRA_ROLE_ARN" \
      --task-role-arn "$TASK_ROLE_ARN" \
      --primary-container "file://${container_json}" \
      --cpu 2048 \
      --memory 4096 \
      --health-check-path "$health_path" \
      --scaling-target '{"minTaskCount":1,"maxTaskCount":3}' \
      --monitor-resources
  else
    echo "==> Updating existing Express Mode service: $name ($arn)"
    aws ecs update-express-gateway-service \
      --region "$AWS_REGION" \
      --service-arn "$arn" \
      --task-role-arn "$TASK_ROLE_ARN" \
      --execution-role-arn "$EXEC_ROLE_ARN" \
      --primary-container "file://${container_json}" \
      --monitor-resources
  fi
}

create_or_update_service "searchaas-fastapi" "$SCRIPT_DIR/.rendered-fastapi.json" "/health"
# NOTE: FastMCP's /mcp endpoint expects an SSE handshake; a plain GET won't
# return 200, so the ALB health check will fail. Add a /healthz route to
# FastMCP (see README) or change this path to one that returns 200.
create_or_update_service "searchaas-fastmcp" "$SCRIPT_DIR/.rendered-fastmcp.json" "/healthz"

# ── 5. Show the generated URLs ───────────────────────────────────────────────
echo ""
echo "==> Done. Service URLs:"
for svc in searchaas-fastapi searchaas-fastmcp; do
  arn=$(aws ecs list-services --cluster default --region "$AWS_REGION" \
          --query "serviceArns[?contains(@, ':service/default/${svc}')]|[0]" \
          --output text)
  url=$(aws ecs describe-express-gateway-service \
          --region "$AWS_REGION" --service-arn "$arn" \
          --query 'service.url' --output text 2>/dev/null || echo "(not ready)")
  printf "    %-20s %s\n" "$svc" "$url"
done

# Cleanup rendered files (they contain ATLAS_URI)
rm -f "$SCRIPT_DIR/.rendered-fastapi.json" "$SCRIPT_DIR/.rendered-fastmcp.json"
