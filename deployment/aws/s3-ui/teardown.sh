#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Tear down the SearchaaS React UI deployed to S3 by
# deployment/aws/s3-ui/deploy.sh.
#
# The deploy script deploys into an EXISTING bucket you own and does NOT create
# it. So by default this teardown only REMOVES the UI (empties the bucket and
# disables website hosting) — it does NOT delete the bucket, matching the
# "bring your own bucket" contract of deploy.sh.
#
# What it does (default):
#   1. Empties the bucket (removes all UI objects: index.html, config.js, assets).
#   2. Deletes the static website hosting configuration.
#
# Optional (only with --delete-bucket):
#   3. Removes the bucket policy and DELETES the bucket entirely.
#
# Usage:
#   ./deployment/aws/s3-ui/teardown.sh --bucket my-bucket
#   ./deployment/aws/s3-ui/teardown.sh --bucket my-bucket --delete-bucket
#   ./deployment/aws/s3-ui/teardown.sh                       # prompts for bucket
#   S3_BUCKET=my-bucket ./deployment/aws/s3-ui/teardown.sh
#
# Prereqs:
#   - AWS CLI v2 configured (`aws configure`)
# -----------------------------------------------------------------------------
set -euo pipefail

# ── Defaults / inputs ─────────────────────────────────────────────────────────
: "${AWS_REGION:=us-east-1}"
S3_BUCKET="${S3_BUCKET:-}"
DELETE_BUCKET=false
ASSUME_YES=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket)        S3_BUCKET="$2"; shift 2 ;;
    --region)        AWS_REGION="$2"; shift 2 ;;
    --delete-bucket) DELETE_BUCKET=true; shift ;;
    --yes|-y)        ASSUME_YES=true; shift ;;
    -h|--help)       grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# ── Resolve the bucket (never guess) ──────────────────────────────────────────
if [[ -z "$S3_BUCKET" ]]; then
  read -r -p "Enter the S3 bucket the UI was deployed to: " S3_BUCKET
fi
[[ -n "$S3_BUCKET" ]] || { echo "ERROR: a bucket name is required. Aborting." >&2; exit 1; }

echo "==> Verifying bucket '$S3_BUCKET' is accessible..."
if ! aws s3api head-bucket --bucket "$S3_BUCKET" 2>/dev/null; then
  echo "ERROR: cannot access bucket '$S3_BUCKET' (missing, wrong account, or no permission)." >&2
  exit 1
fi

echo "==> Target bucket: $S3_BUCKET"
if $DELETE_BUCKET; then
  echo "==> --delete-bucket set: bucket will be EMPTIED and then DELETED."
else
  echo "==> UI files will be removed and website hosting disabled. Bucket will be KEPT."
fi

if ! $ASSUME_YES; then
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Aborted."; exit 0; }
fi

# ── 1. Empty the bucket ───────────────────────────────────────────────────────
echo "==> Emptying bucket contents..."
aws s3 rm "s3://$S3_BUCKET/" --recursive --region "$AWS_REGION"

# ── 2. Disable static website hosting ─────────────────────────────────────────
echo "==> Removing static website hosting configuration..."
aws s3api delete-bucket-website --bucket "$S3_BUCKET" 2>/dev/null \
  && echo "    website config removed" || echo "    no website config present — skipping"

# ── 3. Optional: delete the bucket entirely ───────────────────────────────────
if $DELETE_BUCKET; then
  echo "==> Removing bucket policy (if any)..."
  aws s3api delete-bucket-policy --bucket "$S3_BUCKET" 2>/dev/null || true

  echo "==> Deleting bucket '$S3_BUCKET'..."
  aws s3api delete-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION"
  echo "    bucket deleted"
fi

echo ""
echo "==> Teardown complete."
$DELETE_BUCKET || echo "    Bucket '$S3_BUCKET' kept (empty). Re-run deploy.sh to redeploy the UI."
