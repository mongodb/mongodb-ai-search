# Deploy on Amazon ECS Express Mode

Uses **`aws ecs create-express-gateway-service`** ([docs](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-getting-started.html)).
One service per process:

- **FastAPI** → `AiSearch-fastapi` → `https://AiSearch-fastapi.ecs.<region>.on.aws`
- **FastMCP** (streamable-http) → `AiSearch-fastmcp` → `https://AiSearch-fastmcp.ecs.<region>.on.aws`

> Express Mode auto-provisions an internet-facing ALB + HTTPS URL per service.
> You can't opt out of the ALB in Express Mode — if that's a blocker, drop to
> plain Fargate.

## Files

| File                              | Purpose                                |
| --------------------------------- | -------------------------------------- |
| `primary-container-fastapi.json`  | `--primary-container` payload, FastAPI on `:8000`, command `uvicorn ...` |
| `primary-container-fastmcp.json`  | `--primary-container` payload, FastMCP on `:8001`, command `python -m AiSearch.mcp_server.server` |
| `deploy.sh`                       | End-to-end: IAM roles → ECR push → create/update both services |
| `teardown.sh`                     | Deletes both services; `--purge` also drops the ECR repos + task role |

**Two separate images / two ECR repos** — `AiSearch-fastapi` (built from
`Dockerfile.fastapi`) and `AiSearch-fastmcp` (built from `Dockerfile.fastmcp`).
Each payload's `command` field pins the entrypoint for its image.

## Run it

```bash
export AWS_REGION=us-east-1
export ATLAS_URI='mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true'
export ATLAS_DB='your_database_name'   # required — the Atlas database holding the chunks collection

# Optional — each defaults to the value baked into AiSearch/config/AiSearch.yaml
export ATLAS_COLLECTION='retail'
export EMBEDDINGS_PROVIDER='auto'      # server-side AutoEmbeddings (no API key)
export EMBEDDINGS_MODEL='voyage-4'     # must equal the autoEmbed model on the index
export CORS_ORIGINS='*'

# Any OTHER env var: add it to the "environment" array in BOTH
# primary-container-*.json files before running, e.g.
#   { "name": "VOYAGE_API_KEY", "value": "pa-..." }

./deployment/aws/ecs/deploy.sh
```

What the script does (idempotent — safe to re-run):

1. Creates `ecsTaskExecutionRole`, `ecsInfrastructureRoleForExpressServices`, and `AiSearch-task-role` if missing, attaches the two AWS-managed policies, and puts an inline `BedrockInvoke` policy on the task role (the planner LLM defaults to Bedrock).
2. Creates the `AiSearch-fastapi` and `AiSearch-fastmcp` ECR repos if missing, builds **both** images for `linux/amd64`, pushes `:latest`.
3. Renders the two `primary-container-*.json` files, substituting `<ACCOUNT_ID>`, `<REGION>`, `<ATLAS_URI>`, `<ATLAS_DB>`, `<ATLAS_COLLECTION>`, `<CORS_ORIGINS>`, `<EMBEDDINGS_PROVIDER>`, `<EMBEDDINGS_MODEL>`.
4. Calls `aws ecs create-express-gateway-service` (or `update-...` if the service already exists) for each one with `--monitor-resources` so you watch provisioning in real time.
5. Prints the two `https://...ecs.<region>.on.aws` URLs.

## Configuration options

Everything in `AiSearch/config/AiSearch.yaml` uses `${VAR:-default}` syntax, so
you configure the services purely with **environment variables** — no image
rebuild. On ECS, `deploy.sh` substitutes `<ATLAS_URI>`, `<ATLAS_DB>`,
`<ATLAS_COLLECTION>`, `<CORS_ORIGINS>`, `<EMBEDDINGS_PROVIDER>`, and
`<EMBEDDINGS_MODEL>`. To change any other option below, add it to the
`environment` array in **both** container payloads (they share one config file):

- `deployment/aws/ecs/primary-container-fastapi.json`
- `deployment/aws/ecs/primary-container-fastmcp.json`

```jsonc
// inside "environment": [ ... ]
{ "name": "EMBEDDINGS_PROVIDER", "value": "auto" },
{ "name": "ATLAS_VECTOR_INDEX",  "value": "autoembed_index" },
{ "name": "ATLAS_DIMENSIONS",    "value": "-1" }
```

Anything you leave unset falls back to the default baked into
`AiSearch/config/AiSearch.yaml`. Do **not** commit filled-in secret values —
keep placeholders in git (see Caveats).

> **Do not override the container wiring** already set in the JSON files:
> `PYTHONUNBUFFERED`, `PYTHONPATH`, `AISEARCH_CONFIG`, `AWS_REGION` /
> `AWS_DEFAULT_REGION` (boto3 needs one of these to reach Bedrock — there is no
> `region_name` in the YAML planner block), and the container ports (`8000`
> FastAPI / `8001` FastMCP). CORS is set via `AISEARCH_CORS_ORIGINS`
> (see the [CORS](#cors) section).

### Atlas connection & index

Defaults below are the ones in `AiSearch/config/AiSearch.yaml` — that file is
authored for **Mode B (server-side AutoEmbeddings)**.

| Env var | Default | Purpose |
| --- | --- | --- |
| `ATLAS_URI` | *(required, secret)* | `mongodb+srv://...` connection string |
| `ATLAS_DB` | `kaggle_dataset` | Database name |
| `ATLAS_COLLECTION` | `retail` | Collection to search |
| `ATLAS_VECTOR_INDEX` | `vector_index` | Atlas Vector Search index name |
| `ATLAS_SEARCH_INDEX` | `default` | Atlas Search (Lucene) index for fulltext/hybrid |
| `ATLAS_TEXT_KEY` | `raw_text` | Field holding chunk text (= the `autoEmbed` path) |
| `ATLAS_EMBEDDING_KEY` | *(empty ⇒ null)* | Field holding the vector — set only in client-side mode |
| `ATLAS_RELEVANCE_FN` | *(empty ⇒ null)* | Similarity function — set only in client-side mode |
| `ATLAS_DIMENSIONS` | `-1` | Vector dimensions — must match the index and the embedder (`-1` in auto mode) |

The YAML also carries `atlas.vector_index_definition` /
`search_index_definition`, pasted from the Atlas UI. These are **not**
env-overridable and drive the filter allow-list — if you point the deployment at
a different index, update them in the YAML or `$vectorSearch` pre-filters will
be silently dropped.

### Embedding provider (query embeddings)

Set `EMBEDDINGS_PROVIDER` (rendered from the env var of the same name).

| Provider | Required / key env vars |
| --- | --- |
| `auto` *(default)* | `EMBEDDINGS_MODEL` only — Atlas embeds internally; no API key |
| `voyageai` | `VOYAGE_API_KEY`, `EMBEDDINGS_MODEL` (`voyage-4`), `EMBEDDINGS_OUTPUT_DIMENSION` |
| `bedrock_titan` | ⚠️ needs a YAML edit — see below |

> ⚠️ **`bedrock_titan` is not switchable by env var today.** The YAML
> `embeddings.config` block only defines `model` and `voyage_api_key`, and
> `langchain_aws.BedrockEmbeddings` rejects both (`extra_forbidden`) — the
> container dies at startup with a pydantic `ValidationError`. To use Titan,
> first change `embeddings.config` in `AiSearch.yaml` to `model_id` +
> `region_name`, then set `EMBEDDINGS_PROVIDER=bedrock_titan`. The task role
> already grants `bedrock:InvokeModel`.

For the `voyageai` provider, add `VOYAGE_API_KEY` to the `environment` arrays.
In `auto` mode no embedding key is needed at all.

#### Two embedding modes (must match your Atlas index type)

The container **hard-fails at startup** if the provider doesn't match the live
index type (`ProviderIndexMismatch`). Pick one:

- **Mode B — server-side AutoEmbeddings** *(default)*: Atlas embeds using the
  model declared in an `autoEmbed`-type index. `EMBEDDINGS_PROVIDER=auto`,
  `ATLAS_EMBEDDING_KEY=` (empty), `ATLAS_DIMENSIONS=-1`, `ATLAS_RELEVANCE_FN=`
  (empty), and `ATLAS_VECTOR_INDEX` pointing at an autoEmbed index whose declared
  `model` equals `EMBEDDINGS_MODEL`. This is what `AiSearch.yaml` ships with.
- **Mode A — client-side embeddings**: the app embeds queries (Voyage / Bedrock
  Titan). Requires a `vector`-type index. Set `ATLAS_EMBEDDING_KEY`, a real
  `ATLAS_DIMENSIONS`, and `ATLAS_RELEVANCE_FN` (e.g. `cosine`) — plus the
  provider's own key.

Dev-only escape hatch: `AISEARCH_SKIP_PROVIDER_INDEX_CHECK=1` bypasses this
validation. Do not use in production.

### Planner LLM (query understanding + retrieval planning)

Set `PLANNER_LLM_PROVIDER`. This deployment uses AWS Bedrock; other providers
exist in code but are omitted here.

| Env var | Default | Purpose |
| --- | --- | --- |
| `PLANNER_LLM_PROVIDER` | `bedrock` | No key needed — the task role grants `bedrock:InvokeModel` |
| `PLANNER_MODEL` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | Bedrock model / inference-profile id |
| `PLANNER_TEMPERATURE` | `0.0` | |
| `PLANNER_DEFAULT_TOP_K` | `10` | Default candidate count |
| `LLM_TIMEOUT_S` | `5.0` | Hard deadline per LLM call in the QU layer + planner; on timeout the request falls back to safe defaults and still returns Atlas results. `0` disables. |
| `ENABLE_SUMMARIZATION` | `false` | LLM summary of retrieved results — adds latency and cost. Can also be set per-request via `summarize`. |

> The planner block has **no `region_name`**, so boto3 resolves the Bedrock
> region from `AWS_REGION` / `AWS_DEFAULT_REGION`. Both are set in the container
> payloads; removing them breaks the planner with `NoRegionError`.

### Retrieval strategy

| Env var | Default | Options / notes |
| --- | --- | --- |
| `RETRIEVAL_DEFAULT_STRATEGY` | `hybrid` | `vector`, `fulltext`, `hybrid`, `graph`, `parent_doc`, `metadata` |
| `RETRIEVAL_HYBRID_VECTOR_WEIGHT` | `0.6` | hybrid vector weight |
| `RETRIEVAL_HYBRID_FULLTEXT_WEIGHT` | `0.4` | hybrid fulltext weight |
| `RETRIEVAL_VECTOR_NUM_CANDIDATES` | `100` | vector oversampling |
| `MAX_TIME_MS` | `5000` | Server-side kill switch — Atlas aborts any aggregation exceeding this. `0` disables. |

> `hybrid` uses native `$rankFusion`, which requires **MongoDB 8.0+**.

> Only `auto`, `vector`, `hybrid`, and `parent_doc` build the vector store at
> startup. A `graph`/`fulltext`/`metadata` default strategy defers it, so no
> vector index is auto-created on the collection until a vector-needing request
> arrives (`AiSearch/app/bootstrap.py`).

### Logging

| Env var | Default | Purpose |
| --- | --- | --- |
| `LOG_LEVEL` | `info` | Application log level |

### Example: switch to client-side Voyage embeddings (Mode A)

`auto` is the shipped default, so no extra vars are needed for Mode B. To move to
client-side Voyage embeddings, run with `EMBEDDINGS_PROVIDER=voyageai` and add
these to the `environment` array in **both** `primary-container-*.json` files:

```jsonc
{ "name": "VOYAGE_API_KEY",      "value": "pa-..." },
{ "name": "ATLAS_VECTOR_INDEX",  "value": "voyage_vector_index" },  // a `vector`-type index
{ "name": "ATLAS_EMBEDDING_KEY", "value": "embedding" },            // == the index `path`
{ "name": "ATLAS_RELEVANCE_FN",  "value": "cosine" },
{ "name": "ATLAS_DIMENSIONS",    "value": "1024" },                 // == index numDimensions
{ "name": "EMBEDDINGS_OUTPUT_DIMENSION", "value": "1024" }          // MUST equal ATLAS_DIMENSIONS
```

## Service config (defaults set in `deploy.sh`)

| Setting          | Value                       | Notes |
|------------------|-----------------------------|-------|
| CPU / memory     | `2048` / `4096` (= 2 vCPU / 4 GiB) | **Units are Fargate CPU units (1024 = 1 vCPU) and MiB**, NOT vCPUs/GB. The AWS docs example `--cpu 2 --memory 4` is misleading and produces `Invalid CPU/Memory combination`. Valid pairs include `1024/2048`, `2048/4096`, `2048/8192`, `4096/8192`, `4096/16384`. |
| Min / max tasks  | 1 / 3                       | Express Mode autoscales on CPU |
| Health check     | `/health` (FastAPI), `/healthz` (FastMCP) | `/healthz` was added to `AiSearch/mcp_server/server.py` because `/mcp` requires an SSE handshake |
| Container port   | `8000` / `8001`             | Express Mode terminates TLS at the ALB → forwards plain HTTP to the container |

## CORS

Both services read `AISEARCH_CORS_ORIGINS` (comma-separated list, or `*` for all)
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
Add this origin to AISEARCH_CORS_ORIGINS env var (comma-separated) ...
```

## Useful commands

```bash
# Watch a deploy
aws ecs monitor-express-gateway-service \
  --service-arn arn:aws:ecs:$AWS_REGION:<ACCOUNT_ID>:service/default/AiSearch-fastapi

# Inspect
aws ecs describe-express-gateway-service \
  --service-arn arn:aws:ecs:$AWS_REGION:<ACCOUNT_ID>:service/default/AiSearch-fastmcp

# Get the public URL. NOTE: there is no top-level `service.url` field —
# the hostname lives under activeConfigurations[0].ingressPaths[0].endpoint.
aws ecs describe-express-gateway-service \
  --service-arn arn:aws:ecs:$AWS_REGION:<ACCOUNT_ID>:service/default/AiSearch-fastapi \
  --query 'service.activeConfigurations[0].ingressPaths[0].endpoint' --output text

# Check what config a running service actually loaded
curl -s https://<endpoint>/settings | jq .

# Tear down
aws ecs delete-express-gateway-service \
  --service-arn arn:aws:ecs:$AWS_REGION:<ACCOUNT_ID>:service/default/AiSearch-fastapi \
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

- **`AiSearch.yaml` is baked into the image** (`COPY AiSearch/ ./AiSearch/`), so a running service keeps serving the YAML defaults from whenever it was last built. Editing the YAML in git changes nothing until you re-run `deploy.sh`. `curl https://<endpoint>/settings` shows what a live service actually loaded — compare it against the repo before assuming they match.
- The Express Mode CLI commands (`create-express-gateway-service`, etc.) require a recent AWS CLI v2 release. If you get `Invalid choice`, run `aws --version` and update.
- Plain `environment` values are visible in `DescribeService` output and the ECS console. You explicitly chose env vars over Secrets Manager — fine, just don't commit a filled-in `primary-container-*.json` to git.
- `ATLAS_URI` is injected by `deploy.sh` at render time; the committed JSON only has the `<ATLAS_URI>` placeholder.
