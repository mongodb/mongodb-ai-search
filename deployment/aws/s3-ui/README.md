# Deploy the React UI to S3 (static website)

Deploys `searchaas/ui_react` to an **existing** S3 bucket as a static website.

> **The script never creates a bucket.** You supply the name of a bucket you
> already own. If it can't reach the bucket, it stops and tells you how to
> create one yourself.

## How the UI gets its backend URLs

The SPA reads its backend endpoints at **runtime** from
`window.__SEARCHAAS_CONFIG__`, which is set by a `config.js` loaded in
`index.html`. This script generates that `config.js` from the URLs you pass, so
the same build can point at any backend — no rebuild needed to change URLs.

```
FASTAPI_URL → App.tsx fastapiUrl
MCP_URL     → App.tsx mcpUrl
MCP_API_KEY → App.tsx mcpApiKey
```

## Usage

```bash
# Fully specified
./deployment/aws/s3-ui/deploy.sh \
  --bucket my-existing-ui-bucket \
  --region us-east-1 \
  --api-url "https://searchaas-fastapi.ecs.us-east-1.on.aws" \
  --mcp-url "https://searchaas-fastmcp.ecs.us-east-1.on.aws/mcp"

# Interactive — prompts for the bucket and (optionally) the backend URLs
./deployment/aws/s3-ui/deploy.sh
```

Environment variable equivalents: `S3_BUCKET`, `AWS_REGION`, `VITE_API_URL`
(or `API_URL`), `VITE_MCP_URL` (or `MCP_URL`), `MCP_API_KEY`.

## What it does

1. Resolves and **verifies** the target bucket exists (via `head-bucket`).
2. Detects the bucket's region for the correct website endpoint hostname.
3. Builds the UI (`npm ci && npm run build`).
4. Writes `dist/config.js` with your backend URLs.
5. Enables static website hosting (index + SPA fallback to `index.html`).
6. Syncs `dist/` — hashed assets get a 1-year immutable cache;
   `index.html` and `config.js` get `no-cache` so URL changes take effect
   immediately.
7. Prints the website URL.

## Bucket must allow public reads

Static-website objects must be publicly readable. If the site returns 403,
open up public access and attach a read policy (the script prints these exact
commands on completion):

```bash
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"

aws s3api put-bucket-policy --bucket "$BUCKET" --policy '{
  "Version":"2012-10-17",
  "Statement":[{"Sid":"PublicReadGetObject","Effect":"Allow",
    "Principal":"*","Action":"s3:GetObject",
    "Resource":"arn:aws:s3:::YOUR_BUCKET/*"}]}'
```

## HTTP vs HTTPS

S3 website endpoints are **HTTP-only**; the ECS Express Mode backend URLs are
**HTTPS**. An HTTP page calling HTTPS APIs is allowed by browsers (the reverse
is blocked), so this pairing works.
