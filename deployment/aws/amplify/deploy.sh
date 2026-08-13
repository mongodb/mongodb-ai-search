#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Deploy the Employee Support Copilot (Next.js 14, App Router) to AWS Amplify
# Hosting as an SSR app.
#
# WHY GIT-CONNECTED: the app has a server-side BFF route
# (`src/app/api/chat/route.ts`, marked `runtime = "nodejs"`), so it needs
# Amplify's managed SSR compute (platform WEB_COMPUTE). AWS states plainly:
#
#     "Amplify Hosting does not support manual deploys for server-side
#      rendered (SSR) apps."
#     https://docs.aws.amazon.com/amplify/latest/userguide/manual-deploys.html
#
# A manual .zip deploy DOES return SUCCEED, but Amplify then serves the bundle
# statically from S3 — `/` 404s and `/api/chat` never reaches compute. The
# `deploy-manifest.json` deployment specification is consumed by Amplify's
# BUILD pipeline, not by the manual-deploy API, so it does not rescue that path.
# Continuous deployment from a Git branch is the only supported route.
#
# Idempotent — safe to re-run. Reuses the app/branch if they already exist.
#
# Prereqs:
#   - AWS CLI v2 configured (`aws sso login --profile <profile>`)
#   - A GitHub token with `repo` + `admin:repo_hook` scope, exported as
#     GITHUB_ACCESS_TOKEN (Amplify needs the hook scope to install the webhook
#     that triggers builds on push).
#
# Usage:
#   export GITHUB_ACCESS_TOKEN='ghp_...'
#   export SEARCHAAS_BASE_URL='https://<fastapi-host>.ecs.us-east-1.on.aws'
#   ./deployment/aws/amplify/deploy.sh
# -----------------------------------------------------------------------------
set -euo pipefail

# ── Inputs ────────────────────────────────────────────────────────────────────
: "${AWS_REGION:=us-east-1}"
: "${APP_NAME:=employee-support-copilot}"
: "${BRANCH_NAME:=main}"
: "${REPO_URL:=https://github.com/mongodb/mongodb-ai-search}"
: "${MONOREPO_APP_ROOT:=agents/employee-support-copilot}"
: "${GITHUB_ACCESS_TOKEN:?Export GITHUB_ACCESS_TOKEN (repo + admin:repo_hook scope)}"
: "${SEARCHAAS_BASE_URL:=}"
: "${SEARCHAAS_API_KEY:=}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
APP_DIR="$REPO_ROOT/$MONOREPO_APP_ROOT"

# Default the SearchaaS endpoint from .env.local so a plain run reproduces the
# local configuration.
if [[ -z "$SEARCHAAS_BASE_URL" && -f "$APP_DIR/.env.local" ]]; then
  SEARCHAAS_BASE_URL=$(grep -E '^SEARCHAAS_BASE_URL=' "$APP_DIR/.env.local" | tail -1 | cut -d= -f2-)
  echo "==> SEARCHAAS_BASE_URL not set; using .env.local value"
fi
: "${SEARCHAAS_BASE_URL:?Set SEARCHAAS_BASE_URL (the SearchaaS FastAPI https:// endpoint)}"

# Amplify injects these into both the build and the SSR compute runtime.
# AMPLIFY_MONOREPO_APP_ROOT is what makes the `applications:`/`appRoot` build
# spec resolve to the right subdirectory.
ENV_VARS="SEARCHAAS_BASE_URL=${SEARCHAAS_BASE_URL},SEARCHAAS_API_KEY=${SEARCHAAS_API_KEY},AMPLIFY_MONOREPO_APP_ROOT=${MONOREPO_APP_ROOT},AMPLIFY_DIFF_DEPLOY=false"

echo "==> App: $APP_NAME   Branch: $BRANCH_NAME   Region: $AWS_REGION"
echo "==> Repo: $REPO_URL   appRoot: $MONOREPO_APP_ROOT"
echo "==> SEARCHAAS_BASE_URL: $SEARCHAAS_BASE_URL"

# ── 1. Create (or update) the Amplify app, connected to the repo ─────────────
echo "==> Ensuring Amplify app exists..."
APP_ID=$(aws amplify list-apps --region "$AWS_REGION" \
          --query "apps[?name=='${APP_NAME}'].appId | [0]" --output text 2>/dev/null || echo "None")

if [[ "$APP_ID" == "None" || -z "$APP_ID" ]]; then
  APP_ID=$(aws amplify create-app \
            --region "$AWS_REGION" \
            --name "$APP_NAME" \
            --repository "$REPO_URL" \
            --access-token "$GITHUB_ACCESS_TOKEN" \
            --platform WEB_COMPUTE \
            --build-spec "file://${SCRIPT_DIR}/amplify.yml" \
            --environment-variables "$ENV_VARS" \
            --enable-branch-auto-build \
            --query 'app.appId' --output text)
  echo "    created app $APP_ID"
else
  echo "    reusing app $APP_ID"
  aws amplify update-app --region "$AWS_REGION" --app-id "$APP_ID" \
    --repository "$REPO_URL" \
    --access-token "$GITHUB_ACCESS_TOKEN" \
    --platform WEB_COMPUTE \
    --build-spec "file://${SCRIPT_DIR}/amplify.yml" \
    --environment-variables "$ENV_VARS" \
    --enable-branch-auto-build \
    >/dev/null
  echo "    refreshed repo connection, build spec and env vars"
fi

# ── 2. Create (or reuse) the branch ──────────────────────────────────────────
if ! aws amplify get-branch --region "$AWS_REGION" --app-id "$APP_ID" \
      --branch-name "$BRANCH_NAME" >/dev/null 2>&1; then
  aws amplify create-branch --region "$AWS_REGION" --app-id "$APP_ID" \
    --branch-name "$BRANCH_NAME" \
    --framework 'Next.js - SSR' \
    --enable-auto-build \
    --environment-variables "$ENV_VARS" \
    >/dev/null
  echo "    created branch $BRANCH_NAME"
else
  aws amplify update-branch --region "$AWS_REGION" --app-id "$APP_ID" \
    --branch-name "$BRANCH_NAME" \
    --framework 'Next.js - SSR' \
    --enable-auto-build \
    --environment-variables "$ENV_VARS" \
    >/dev/null
  echo "    reusing branch $BRANCH_NAME"
fi

# ── 3. Kick off a build ──────────────────────────────────────────────────────
echo "==> Starting build job..."
JOB_ID=$(aws amplify start-job --region "$AWS_REGION" --app-id "$APP_ID" \
           --branch-name "$BRANCH_NAME" --job-type RELEASE \
           --query 'jobSummary.jobId' --output text)
echo "    job $JOB_ID"

# ── 4. Poll until the job settles ────────────────────────────────────────────
echo "==> Waiting for job $JOB_ID (builds typically take 3-6 min)..."
for _ in $(seq 1 90); do
  STATUS=$(aws amplify get-job --region "$AWS_REGION" --app-id "$APP_ID" \
             --branch-name "$BRANCH_NAME" --job-id "$JOB_ID" \
             --query 'job.summary.status' --output text)
  echo "    status=$STATUS"
  case "$STATUS" in
    SUCCEED)          break ;;
    FAILED|CANCELLED)
      echo "ERROR: build $STATUS. Step detail:" >&2
      aws amplify get-job --region "$AWS_REGION" --app-id "$APP_ID" \
        --branch-name "$BRANCH_NAME" --job-id "$JOB_ID" \
        --query 'job.steps[].{step:stepName,status:status,log:logUrl}' --output table >&2
      exit 1 ;;
  esac
  sleep 10
done

# ── 5. Report + verify ───────────────────────────────────────────────────────
DOMAIN=$(aws amplify get-app --region "$AWS_REGION" --app-id "$APP_ID" \
           --query 'app.defaultDomain' --output text)
URL="https://${BRANCH_NAME}.${DOMAIN}"

echo ""
echo "==> Done."
echo "    app id : $APP_ID"
echo "    URL    : $URL"
echo ""
echo "==> Verifying SSR route (must be JSON, not 404/301)..."
curl -sS -m 60 -X POST "${URL}/api/chat" \
  -H 'content-type: application/json' \
  -d '{"query":"VPN is not connecting"}' \
  -o /dev/null -w "    POST /api/chat -> HTTP %{http_code}\n" || true
