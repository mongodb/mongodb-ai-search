# Deploy on Amazon ECS Express Mode

Uses **`aws ecs create-express-gateway-service`** ([docs](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-getting-started.html)).
One service per process:

- **FastAPI** → `searchaas-fastapi` → `https://searchaas-fastapi.ecs.<region>.on.aws`
- **FastMCP** (streamable-http) → `searchaas-fastmcp` → `https://searchaas-fastmcp.ecs.<region>.on.aws`
- **React UI** → S3 static website (separate from ECS)

> Express Mode auto-provisions an internet-facing ALB + HTTPS URL per service.
> You can't opt out of the ALB in Express Mode — if that's a blocker, drop to
> plain Fargate.

## Files

| File                              | Purpose                                |
| --------------------------------- | -------------------------------------- |
| `primary-container-fastapi.json`  | `--primary-container` payload, FastAPI on `:8000`, command `uvicorn ...` |
| `primary-container-fastmcp.json`  | `--primary-container` payload, FastMCP on `:8001`, command `python -m searchaas.mcp_server.server` |
| `deploy.sh`                       | End-to-end: IAM roles → ECR push → create/update both services |

Same ECR image for both services; the `command` field in each payload picks
the entrypoint.

## Run it

```bash
export AWS_REGION=us-east-1
export ATLAS_URI='mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true'
export ATLAS_DB='your_database_name'   # required — the Atlas database holding the chunks collection

# Optional extra LLM keys: add them to primary-container-*.json under
# "environment" before running, e.g.
#   { "name": "OPENAI_API_KEY", "value": "sk-..." }

./deployment/aws/ecs/deploy.sh
```

What the script does (idempotent — safe to re-run):

1. Creates `ecsTaskExecutionRole` and `ecsInfrastructureRoleForExpressServices` if missing, and attaches the two AWS-managed policies.
2. Creates the `searchaas` ECR repo if missing, builds the Docker image, pushes `:latest`.
3. Renders the two `primary-container-*.json` files, substituting `<ACCOUNT_ID>`, `<REGION>`, `<ATLAS_URI>`, `<ATLAS_DB>`.
4. Calls `aws ecs create-express-gateway-service` (or `update-...` if the service already exists) for each one with `--monitor-resources` so you watch provisioning in real time.
5. Prints the two `https://...ecs.<region>.on.aws` URLs.

## Service config (defaults set in `deploy.sh`)

| Setting          | Value                       | Notes |
|------------------|-----------------------------|-------|
| CPU / memory     | `2048` / `4096` (= 2 vCPU / 4 GiB) | **Units are Fargate CPU units (1024 = 1 vCPU) and MiB**, NOT vCPUs/GB. The AWS docs example `--cpu 2 --memory 4` is misleading and produces `Invalid CPU/Memory combination`. Valid pairs include `1024/2048`, `2048/4096`, `2048/8192`, `4096/8192`, `4096/16384`. |
| Min / max tasks  | 1 / 3                       | Express Mode autoscales on CPU |
| Health check     | `/health` (FastAPI), `/healthz` (FastMCP) | `/healthz` was added to `searchaas/mcp_server/server.py` because `/mcp` requires an SSE handshake |
| Container port   | `8000` / `8001`             | Express Mode terminates TLS at the ALB → forwards plain HTTP to the container |

## React UI on S3 (static website hosting, no CloudFront)

```bash
BUCKET=searchaas-ui-$(aws sts get-caller-identity --query Account --output text)
REGION=$AWS_REGION

# 1. Build with API + MCP base URLs pointing at the Express Mode URLs
cd searchaas/ui_react
VITE_API_URL="https://searchaas-fastapi.ecs.${REGION}.on.aws" \
VITE_MCP_URL="https://searchaas-fastmcp.ecs.${REGION}.on.aws/mcp" \
  npm run build

# 2. Create the bucket and enable website hosting
aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
  $( [ "$REGION" != "us-east-1" ] && echo --create-bucket-configuration LocationConstraint=$REGION )

aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"

aws s3 website s3://$BUCKET --index-document index.html --error-document index.html

aws s3api put-bucket-policy --bucket "$BUCKET" --policy "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{
    \"Sid\": \"PublicReadGetObject\",
    \"Effect\": \"Allow\",
    \"Principal\": \"*\",
    \"Action\": \"s3:GetObject\",
    \"Resource\": \"arn:aws:s3:::$BUCKET/*\"
  }]
}"

# 3. Upload
aws s3 sync dist/ s3://$BUCKET/ --delete

echo "UI URL: http://${BUCKET}.s3-website-${REGION}.amazonaws.com"
```

> S3 website endpoints are **HTTP-only**. The Express Mode service URLs are
> **HTTPS-only**. Modern browsers block "active mixed content" — an HTTP page
> calling HTTPS APIs is fine, the reverse is not. Since the UI is on HTTP and
> the APIs on HTTPS, you're good.

## CORS

Both services read `SEARCHAAS_CORS_ORIGINS` (comma-separated list, or `*` for all)
in addition to the built-in localhost regex. The deploy script defaults to `*`
during initial setup. To lock it down, re-run with your real origins:

```bash
CORS_ORIGINS="http://searchaas-ui-123.s3-website-us-east-1.amazonaws.com,https://app.example.com" \
ATLAS_URI="..." \
./deployment/aws/ecs/deploy.sh
```

If a preflight is rejected you'll now see a one-line warning in the FastAPI
container logs:

```
CORS PREFLIGHT REJECTED: origin='http://...' method=POST path=/retrieve status=400.
Add this origin to SEARCHAAS_CORS_ORIGINS env var (comma-separated) ...
```

## Useful commands

```bash
# Watch a deploy
aws ecs monitor-express-gateway-service \
  --service-arn arn:aws:ecs:$AWS_REGION:<ACCOUNT_ID>:service/default/searchaas-fastapi

# Inspect
aws ecs describe-express-gateway-service \
  --service-arn arn:aws:ecs:$AWS_REGION:<ACCOUNT_ID>:service/default/searchaas-fastmcp

# Tear down
aws ecs delete-express-gateway-service \
  --service-arn arn:aws:ecs:$AWS_REGION:<ACCOUNT_ID>:service/default/searchaas-fastapi \
  --monitor-resources
```

## Apple Silicon / cross-architecture builds

The script uses `docker buildx build --platform linux/amd64 --push`. This is
mandatory on M1/M2/M3 Macs — without it, a native `docker build` produces an
**arm64** image, and ECS Fargate (x86_64 by default) crashes the container with:

```
exec /usr/local/bin/python: exec format error
```

If you want to run on Graviton (arm64) Fargate instead, change the platform
to `linux/arm64` and add `"runtimePlatform": {"cpuArchitecture": "ARM64"}` to
your task config — but Express Mode currently provisions x86_64 tasks, so
stick with `linux/amd64`.

## Caveats

- The Express Mode CLI commands (`create-express-gateway-service`, etc.) require a recent AWS CLI v2 release. If you get `Invalid choice`, run `aws --version` and update.
- Plain `environment` values are visible in `DescribeService` output and the ECS console. You explicitly chose env vars over Secrets Manager — fine, just don't commit a filled-in `primary-container-*.json` to git.
- `ATLAS_URI` is injected by `deploy.sh` at render time; the committed JSON only has the `<ATLAS_URI>` placeholder.
