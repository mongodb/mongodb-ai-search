# Lambda deployment — FastAPI + FastMCP

Deploys two AWS Lambda functions, each fronted by a public **Lambda Function URL**:

| Function            | Purpose                          | Function URL invoke mode |
| ------------------- | -------------------------------- | ------------------------ |
| `searchaas-fastapi` | REST API (`searchaas.api.app`)   | `BUFFERED`               |
| `searchaas-fastmcp` | MCP server, `streamable-http`    | `RESPONSE_STREAM`        |

Both functions are **container-image Lambdas** running the [AWS Lambda Web
Adapter](https://github.com/awslabs/aws-lambda-web-adapter) (LWA). LWA proxies
each invocation to a local HTTP server, so the existing uvicorn/Starlette
servers run unchanged — the same image strategy as the ECS deployment, just
hosted on Lambda.

## Why streaming for FastMCP

FastMCP's `streamable-http` transport returns chunked / SSE responses. A
buffered Function URL would block until the response completed and break the
SSE handshake, so we set:

- `AWS_LWA_INVOKE_MODE=response_stream` in `Dockerfile.fastmcp`
- `--invoke-mode RESPONSE_STREAM` on the Function URL

## Prereqs

- AWS CLI v2, Docker w/ `buildx`
- An ECR-pushable role/identity
- The same environment variables your `searchaas.yaml` expects, exported in
  the shell that runs `deploy.sh`. At minimum: `ATLAS_URI`, `ATLAS_DB`.

## Deploy

```bash
export ATLAS_URI='mongodb+srv://...'
export ATLAS_DB='searchaas'
# optional, depending on which providers your config enables:
export GOOGLE_API_KEY=...
export VOYAGE_API_KEY=...
# optional CORS override (default "*"):
export CORS_ORIGINS='https://app.example.com,https://localhost:5173'

./deployment/aws/lambda/deploy.sh
```

Re-running the script is idempotent: it updates code, env vars, and URL config.

## Tunables

| Env var             | Default      | Notes                                     |
| ------------------- | ------------ | ----------------------------------------- |
| `AWS_REGION`        | `us-east-1`  |                                           |
| `LAMBDA_MEMORY_MB`  | `3008`       | Bump to 4096–10240 if cold-start OOMs.    |
| `LAMBDA_TIMEOUT_SEC`| `120`        | Max `900`.                                |
| `IMAGE_TAG`         | `latest`     | Use git SHA for safe rollbacks.           |

## Limits to be aware of

- **Buffered Function URL**: 6 MB response cap (FastAPI).
- **Response-streaming Function URL**: 20 MB response cap, 15 min timeout
  (FastMCP).
- **Cold starts**: the image is ~1 GB; expect 5–15 s first-invocation latency.
  `AWS_LWA_ASYNC_INIT=true` lets the app keep initialising past the 10 s INIT
  budget, after which the first real invoke pays the remaining startup cost.

## Teardown

```bash
aws lambda delete-function-url-config --function-name searchaas-fastapi
aws lambda delete-function-url-config --function-name searchaas-fastmcp
aws lambda delete-function --function-name searchaas-fastapi
aws lambda delete-function --function-name searchaas-fastmcp
aws ecr delete-repository --repository-name searchaas-fastapi --force
aws ecr delete-repository --repository-name searchaas-fastmcp --force
aws iam delete-role-policy --role-name searchaas-lambda-role --policy-name BedrockInvoke
aws iam detach-role-policy --role-name searchaas-lambda-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name searchaas-lambda-role
```
