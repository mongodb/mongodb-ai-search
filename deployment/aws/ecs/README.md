# Deploy on Amazon ECS Express Mode

Uses **`aws ecs create-express-gateway-service`** ([docs](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-getting-started.html)).
One service per process:

- **FastAPI** → `searchaas-fastapi` → `https://searchaas-fastapi.ecs.<region>.on.aws`
- **FastMCP** (streamable-http) → `searchaas-fastmcp` → `https://searchaas-fastmcp.ecs.<region>.on.aws`

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

## Configuration options

Everything in `searchaas/config/searchaas.yaml` uses `${VAR:-default}` syntax, so
you configure the services purely with **environment variables** — no image
rebuild. On ECS, `deploy.sh` only substitutes `<ATLAS_URI>`, `<ATLAS_DB>`, and
`<CORS_ORIGINS>`. To change any other option below, add it to the `environment`
array in **both** container payloads (they run the same image / config):

- `deployment/aws/ecs/primary-container-fastapi.json`
- `deployment/aws/ecs/primary-container-fastmcp.json`

```jsonc
// inside "environment": [ ... ]
{ "name": "EMBEDDINGS_PROVIDER", "value": "auto" },
{ "name": "ATLAS_VECTOR_INDEX",  "value": "autoembed_index" },
{ "name": "ATLAS_DIMENSIONS",    "value": "-1" }
```

Anything you leave unset falls back to the default baked into
`searchaas/config/searchaas.yaml`. Do **not** commit filled-in secret values —
keep placeholders in git (see Caveats).

> **Do not override the container wiring** already set in the JSON files:
> `PYTHONUNBUFFERED`, `PYTHONPATH`, `SEARCHAAS_CONFIG`, and the container ports
> (`8000` FastAPI / `8001` FastMCP). CORS is set via `SEARCHAAS_CORS_ORIGINS`
> (see the [CORS](#cors) section).

### Atlas connection & index

| Env var | Default | Purpose |
| --- | --- | --- |
| `ATLAS_URI` | *(required, secret)* | `mongodb+srv://...` connection string |
| `ATLAS_DB` | `amazon` | Database name |
| `ATLAS_COLLECTION` | `pdf_multimodal_chunks` | Collection to search |
| `ATLAS_VECTOR_INDEX` | `aisearch_vector_index` | Atlas Vector Search index name |
| `ATLAS_SEARCH_INDEX` | `default` | Atlas Search (Lucene) index for fulltext/hybrid |
| `ATLAS_TEXT_KEY` | `raw_text` | Field holding chunk text |
| `ATLAS_EMBEDDING_KEY` | `embedding` | Field holding the vector (empty ⇒ null, for auto mode) |
| `ATLAS_RELEVANCE_FN` | `cosine` | Similarity function (empty ⇒ null, for auto mode) |
| `ATLAS_DIMENSIONS` | `1024` | Vector dimensions — must match the index and the embedder (`-1` for auto mode) |

### Embedding provider (query embeddings)

Set `EMBEDDINGS_PROVIDER`. This deployment documents the AWS-native and
MongoDB/Voyage options; other providers exist in code but are omitted here.

| Provider | Required / key env vars |
| --- | --- |
| `voyageai` *(default)* | `VOYAGE_API_KEY`, `EMBEDDINGS_MODEL` (`voyage-4`), `EMBEDDINGS_OUTPUT_DIMENSION` (`1024`) |
| `auto` (server-side) | `EMBEDDINGS_MODEL` only — Atlas embeds internally; no API key |
| `bedrock_titan` | `EMBEDDINGS_MODEL`/model id (e.g. `amazon.titan-embed-text-v2:0`), `BEDROCK_REGION_NAME` — uses the task role's Bedrock access |

`EMBEDDINGS_OUTPUT_DIMENSION` **must equal** `ATLAS_DIMENSIONS`.

For the `voyageai` provider, add `VOYAGE_API_KEY` to the `environment` arrays.
Switch to `auto` or `bedrock_titan` and no Voyage key is needed.

#### Two embedding modes (must match your Atlas index type)

The container **hard-fails at startup** if the provider doesn't match the live
index type (`ProviderIndexMismatch`). Pick one:

- **Mode A — client-side embeddings** (default): the app embeds queries
  (Voyage / Bedrock Titan). Requires a `vector`-type index. Keep
  `ATLAS_EMBEDDING_KEY`, a real `ATLAS_DIMENSIONS`, and `ATLAS_RELEVANCE_FN` set.
- **Mode B — server-side AutoEmbeddings**: Atlas embeds using the model declared
  in an `autoEmbed`-type index. Set `EMBEDDINGS_PROVIDER=auto`,
  `ATLAS_EMBEDDING_KEY=` (empty), `ATLAS_DIMENSIONS=-1`, `ATLAS_RELEVANCE_FN=`
  (empty), and point `ATLAS_VECTOR_INDEX` at the autoEmbed index whose declared
  `model` equals `EMBEDDINGS_MODEL`.

Dev-only escape hatch: `SEARCHAAS_SKIP_PROVIDER_INDEX_CHECK=1` bypasses this
validation. Do not use in production.

### Planner LLM (query understanding + retrieval planning)

Set `PLANNER_LLM_PROVIDER`. This deployment uses AWS Bedrock; other providers
exist in code but are omitted here.

| Provider | Key env vars |
| --- | --- |
| `bedrock` *(default)* | `BEDROCK_MODEL` (default Claude Haiku), `BEDROCK_REGION_NAME` (`us-east-1`), `BEDROCK_TEMPERATURE` (`0.1`) — no key needed if the task role grants `bedrock:InvokeModel` |

`PLANNER_DEFAULT_TOP_K` (default `20`) controls default candidate count.

### Retrieval strategy

| Env var | Default | Options / notes |
| --- | --- | --- |
| `RETRIEVAL_DEFAULT_STRATEGY` | `hybrid` | `vector`, `fulltext`, `hybrid`, `graph`, `parent_doc` |
| `RETRIEVAL_HYBRID_VECTOR_WEIGHT` | `0.6` | hybrid vector weight |
| `RETRIEVAL_HYBRID_FULLTEXT_WEIGHT` | `0.4` | hybrid fulltext weight |
| `RETRIEVAL_VECTOR_NUM_CANDIDATES` | `200` | vector oversampling |

> `hybrid` uses native `$rankFusion`, which requires **MongoDB 8.0+**.

### Logging

| Env var | Default | Purpose |
| --- | --- | --- |
| `LOG_LEVEL` | `info` | Application log level |

### Example: switch to server-side AutoEmbeddings

Add these to the `environment` array in both `primary-container-*.json` files
(no `VOYAGE_API_KEY` needed):

```jsonc
{ "name": "EMBEDDINGS_PROVIDER", "value": "auto" },
{ "name": "EMBEDDINGS_MODEL",    "value": "voyage-4" },        // must equal the index's autoEmbed model
{ "name": "ATLAS_VECTOR_INDEX",  "value": "autoembed_index" },
{ "name": "ATLAS_EMBEDDING_KEY", "value": "" },                // empty ⇒ null
{ "name": "ATLAS_RELEVANCE_FN",  "value": "" },                // empty ⇒ null
{ "name": "ATLAS_DIMENSIONS",    "value": "-1" }
```

## Service config (defaults set in `deploy.sh`)

| Setting          | Value                       | Notes |
|------------------|-----------------------------|-------|
| CPU / memory     | `2048` / `4096` (= 2 vCPU / 4 GiB) | **Units are Fargate CPU units (1024 = 1 vCPU) and MiB**, NOT vCPUs/GB. The AWS docs example `--cpu 2 --memory 4` is misleading and produces `Invalid CPU/Memory combination`. Valid pairs include `1024/2048`, `2048/4096`, `2048/8192`, `4096/8192`, `4096/16384`. |
| Min / max tasks  | 1 / 3                       | Express Mode autoscales on CPU |
| Health check     | `/health` (FastAPI), `/healthz` (FastMCP) | `/healthz` was added to `searchaas/mcp_server/server.py` because `/mcp` requires an SSE handshake |
| Container port   | `8000` / `8001`             | Express Mode terminates TLS at the ALB → forwards plain HTTP to the container |

## CORS

Both services read `SEARCHAAS_CORS_ORIGINS` (comma-separated list, or `*` for all)
in addition to the built-in localhost regex. The deploy script defaults to `*`
during initial setup. To lock it down, re-run with your real origins:

```bash
CORS_ORIGINS="https://app.example.com,https://ui.example.com" \
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
