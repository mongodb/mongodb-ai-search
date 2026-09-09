#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Tear down the classic Bedrock Agent deployment created by
# deployment/aws/bedrock-agent/deploy.sh.
#
# Deletes:
#   - Bedrock agent `searchaas-mcp-agent` (with --skip-resource-in-use-check,
#     which removes its aliases, versions, and the action group with it)
#   - Lambda function `searchaas-mcp-bridge`
#
# Optional (only with --purge):
#   - IAM roles: searchaas-mcp-bridge-role,
#                AmazonBedrockExecutionRoleForAgents_searchaas
#   - CloudWatch log group /aws/lambda/searchaas-mcp-bridge
#
# NEVER touched (owned by other deployments):
#   - the ECS FastMCP service and everything under deployment/aws/ecs
#
# Usage:
#   ./deployment/aws/bedrock-agent/teardown.sh            # agent + lambda
#   ./deployment/aws/bedrock-agent/teardown.sh --purge    # also IAM roles + logs
#   ./deployment/aws/bedrock-agent/teardown.sh --yes      # skip confirmation
# -----------------------------------------------------------------------------
set -euo pipefail

: "${AWS_PROFILE:=anuj-ps}"
: "${AWS_REGION:=us-east-1}"
export AWS_PROFILE AWS_REGION

: "${AGENT_NAME:=searchaas-mcp-agent}"
: "${LAMBDA_NAME:=searchaas-mcp-bridge}"
AGENT_ROLE=AmazonBedrockExecutionRoleForAgents_searchaas
LAMBDA_ROLE=searchaas-mcp-bridge-role

PURGE=false
ASSUME_YES=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=true; shift ;;
    --yes|-y) ASSUME_YES=true; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "==> Account: $ACCOUNT_ID   Region: $AWS_REGION   Profile: $AWS_PROFILE"
echo "==> This will DELETE agent '$AGENT_NAME' and Lambda '$LAMBDA_NAME'."
$PURGE && echo "==> --purge set: will ALSO delete IAM roles '$LAMBDA_ROLE', '$AGENT_ROLE' and the Lambda log group."

if ! $ASSUME_YES; then
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Aborted."; exit 0; }
fi

# ── 1. Agent (takes its aliases, versions and action group with it) ──────────
AGENT_ID=$(aws bedrock-agent list-agents \
             --query "agentSummaries[?agentName=='${AGENT_NAME}'].agentId | [0]" \
             --output text 2>/dev/null || echo "None")
if [[ "$AGENT_ID" == "None" || -z "$AGENT_ID" ]]; then
  echo "    agent $AGENT_NAME not found (already deleted?) — skipping"
else
  echo "==> Deleting agent $AGENT_NAME ($AGENT_ID)..."
  aws bedrock-agent delete-agent --agent-id "$AGENT_ID" \
    --skip-resource-in-use-check >/dev/null
  echo "    deleted"
fi

# ── 2. Bridge Lambda ─────────────────────────────────────────────────────────
if aws lambda get-function --function-name "$LAMBDA_NAME" >/dev/null 2>&1; then
  echo "==> Deleting Lambda $LAMBDA_NAME..."
  aws lambda delete-function --function-name "$LAMBDA_NAME"
  echo "    deleted"
else
  echo "    lambda $LAMBDA_NAME not found (already deleted?) — skipping"
fi

# ── 3. Optional purge: IAM roles + logs ──────────────────────────────────────
if $PURGE; then
  echo "==> Deleting IAM role '$LAMBDA_ROLE'..."
  aws iam detach-role-policy --role-name "$LAMBDA_ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null || true
  aws iam delete-role --role-name "$LAMBDA_ROLE" 2>/dev/null \
    && echo "    deleted" || echo "    not found or still in use — skipping"

  echo "==> Deleting IAM role '$AGENT_ROLE'..."
  aws iam delete-role-policy --role-name "$AGENT_ROLE" --policy-name InvokeModel 2>/dev/null || true
  aws iam delete-role --role-name "$AGENT_ROLE" 2>/dev/null \
    && echo "    deleted" || echo "    not found or still in use — skipping"

  echo "==> Deleting log group /aws/lambda/${LAMBDA_NAME}..."
  aws logs delete-log-group --log-group-name "/aws/lambda/${LAMBDA_NAME}" 2>/dev/null \
    && echo "    deleted" || echo "    not found — skipping"
fi

echo ""
echo "==> Teardown complete."
echo "    The ECS FastMCP service was NOT touched."
