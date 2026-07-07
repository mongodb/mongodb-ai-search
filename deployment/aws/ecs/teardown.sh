#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Tear down the SearchaaS ECS Express Mode deployment created by
# deployment/aws/ecs/deploy.sh.
#
# Deletes (mirrors the resource names used by deploy.sh):
#   - Express Gateway services: searchaas-fastapi, searchaas-fastmcp
#     (this also tears down the auto-provisioned ALB, tasks, and the
#      https://<service>.ecs.<region>.on.aws URLs)
#
# Optional (only with --purge):
#   - ECR repos: searchaas-fastapi, searchaas-fastmcp (and all images in them)
#   - IAM role:  searchaas-task-role (and its inline BedrockInvoke policy)
#
# NEVER deleted (shared / AWS-managed, other services may depend on them):
#   - ecsTaskExecutionRole
#   - ecsInfrastructureRoleForExpressServices
#
# Usage:
#   ./deployment/aws/ecs/teardown.sh            # delete services only
#   ./deployment/aws/ecs/teardown.sh --purge    # also delete ECR repo + task role
#   ./deployment/aws/ecs/teardown.sh --yes       # skip confirmation prompt
#
# Prereqs:
#   - AWS CLI v2 configured (`aws configure`)
# -----------------------------------------------------------------------------
set -euo pipefail

# ── Inputs ────────────────────────────────────────────────────────────────────
: "${AWS_REGION:=us-east-1}"
REPOS=(searchaas-fastapi searchaas-fastmcp)
TASK_ROLE=searchaas-task-role
SERVICES=(searchaas-fastapi searchaas-fastmcp)

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
echo "==> Account: $ACCOUNT_ID   Region: $AWS_REGION"
echo "==> This will DELETE the ECS Express services: ${SERVICES[*]}"
$PURGE && echo "==> --purge set: will ALSO delete ECR repos '${REPOS[*]}' and IAM role '$TASK_ROLE'."

if ! $ASSUME_YES; then
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Aborted."; exit 0; }
fi

# ── 1. Delete Express Gateway services ────────────────────────────────────────
for svc in "${SERVICES[@]}"; do
  arn=$(aws ecs list-services --cluster default --region "$AWS_REGION" \
          --query "serviceArns[?contains(@, ':service/default/${svc}')]|[0]" \
          --output text 2>/dev/null || echo "None")
  if [[ "$arn" == "None" || -z "$arn" ]]; then
    echo "    service $svc not found (already deleted?) — skipping"
    continue
  fi
  echo "==> Deleting Express Gateway service: $svc ($arn)"
  aws ecs delete-express-gateway-service --region "$AWS_REGION" --service-arn "$arn"
done

# ── 2. Optional purge: ECR repo + task role ───────────────────────────────────
if $PURGE; then
  for repo in "${REPOS[@]}"; do
    echo "==> Deleting ECR repo '$repo' (with all images)..."
    aws ecr delete-repository --repository-name "$repo" \
      --region "$AWS_REGION" --force 2>/dev/null \
      && echo "    deleted" || echo "    repo $repo not found — skipping"
  done

  echo "==> Deleting IAM role '$TASK_ROLE'..."
  # Remove inline policy created by deploy.sh, then the role.
  aws iam delete-role-policy --role-name "$TASK_ROLE" \
    --policy-name BedrockInvoke 2>/dev/null || true
  aws iam delete-role --role-name "$TASK_ROLE" 2>/dev/null \
    && echo "    deleted" || echo "    role not found or still in use — skipping"

  echo ""
  echo "    NOTE: shared roles were NOT touched:"
  echo "      - ecsTaskExecutionRole"
  echo "      - ecsInfrastructureRoleForExpressServices"
fi

echo ""
echo "==> Teardown complete."
echo "    Express services delete asynchronously; the .ecs.${AWS_REGION}.on.aws"
echo "    URLs stop serving within a few minutes. Verify with:"
echo "      aws ecs list-services --cluster default --region $AWS_REGION"
