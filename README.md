# AiSearch — Phase 1

MongoDB Atlas-backed retrieval platform built around the **Factory pattern**.
Phase 1 ships: query understanding (rewriting, entity & typed-fact extraction,
intent), AI-driven retrieval planning with Atlas-managed guardrails, six
retrieval strategies (vector / full-text / hybrid / graph / parent-doc /
metadata), YAML-driven configuration, a FastAPI REST surface, a FastMCP tool
surface, real aggregation-pipeline capture, and a React testing UI.

Every concern is swappable through YAML — switching embeddings, LLMs, indexes,
or retrieval strategy is a config change, not a code change.

See [docs/Instructions.md](docs/Instructions.md) for the full architecture and Phase 2 roadmap.

## Layout

```
mongodb-ai-search/
├── README.md                 # this file
├── requirements.txt          # Python dependencies
├── pytest.ini                # pytest configuration
├── .env.example              # environment-variable template (copy to .env)
├── docs/                     # architecture & reference documentation
│   ├── Instructions.md           # full architecture spec + Phase 2 roadmap
│   ├── ARCHITECTURE_DIAGRAMS.md  # system/flow diagrams
│   ├── CODEBASE_ANALYSIS.md      # module-by-module walkthrough
│   ├── QUICK_REFERENCE.md        # cheat sheet
│   ├── DOCUMENTATION_INDEX.md    # index of the docs
│   └── known-issues.md           # known limitations & gotchas
├── deployment/               # cloud deployment artifacts
│   ├── aws/                      # S3 UI + ECS Express (default) + Bedrock AgentCore
│   ├── azure/                    # Container Apps + AI Foundry (Bicep)
│   └── google/                   # Cloud Run (combined or separate services)
└── AiSearch/                # application package
    ├── config/               # YAML config + loader (single source of truth; ${VAR:-default} expansion)
    ├── infrastructure/       # AtlasFactory (Mongo client / db / collections, ping, stats)
    ├── domain/               # Pydantic models (Chunk, SourceRef)
    ├── embeddings/           # EmbeddingFactory (auto/AutoEmbeddings, Voyage, OpenAI, Gemini, Titan, Cohere, ...)
    ├── llm/                  # LLMFactory (Gemini, Azure/OpenAI, Anthropic, Bedrock)
    ├── query_understanding/  # QueryUnderstandingLayer + FactStore (rewrite, entities, typed facts, sort, intent)
    ├── planning/             # RetrievalPlanner + Atlas-managed PolicyStore
    ├── retrieval/            # RetrieverFactory (vector/fulltext/hybrid/graph/parent_doc/metadata)
    ├── observability/        # structured logging + pipeline_capture (real Atlas aggregation capture)
    ├── app/                  # Container / bootstrap — wires every factory from AppConfig
    ├── api/                  # FastAPI surface
    ├── mcp_server/           # FastMCP surface
    ├── ui_react/             # React + Vite retrieval-testing UI
    ├── tests/                # pytest suite
    ├── facts.py              # query-fact routing → Atlas pre-filters vs in-memory post-filters
    ├── filtering.py          # filter allowlist sanitization (drops non-indexable paths)
    ├── utils.py              # shared serialization / summarization helpers
    └── diagnose.py           # `python -m AiSearch.diagnose` self-check CLI
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # fill in ATLAS_URI, provider keys, ...
```

Edit `AiSearch/config/AiSearch.yaml` to pick providers/models/indexes. The
default build runs **server-side AutoEmbeddings** (Atlas embeds the query and
document text internally, `embeddings.provider: auto`, `voyage-4` model) with
**Gemini** as the planner LLM. Every value is `${VAR:-default}`-driven, so you
can retune collections, indexes, dimensions, providers, models, and strategy
via environment variables — no image rebuild required.

Two embedding modes are supported and cross-validated at startup:

- **Mode B — AutoEmbeddings (active default):** `provider: auto`,
  `embedding_key: null`, `dimensions: -1`; requires an `autoEmbed` Atlas index.
- **Mode A — client-side embeddings:** e.g. `provider: voyageai`, with
  `embedding_key`/`dimensions`/`relevance_score_fn` matching a `vector` index.

The bootstrap layer (`AiSearch/app/bootstrap.py`) refuses to start on a
provider↔index-type mismatch.

**Supported providers**

- Embeddings: `auto`, `azure_openai`, `openai`, `voyageai`, `voyage_multimodal`,
  `cohere`, `huggingface`, `gemini`, `bedrock_titan`.
- Planner LLM: `gemini`, `azure_openai`, `openai`, `anthropic`, `bedrock`.

## Run

```bash
# REST API
uvicorn AiSearch.api.app:app --host 0.0.0.0 --port 8000

# FastMCP server
python -m AiSearch.mcp_server.server

# React UI (from AiSearch/ui_react)
npm install && npm run dev

# Self-check (Atlas ping, collection stats, embedder probe)
python -m AiSearch.diagnose
```

## REST API

FastAPI surface (`AiSearch/api/app.py`). Every `/retrieve*` response includes
the chosen `strategy`, the `plan`, `results`, the `understood_query`, an LLM
`summary`, per-stage `timings`, and the **real captured Atlas aggregation
`pipeline`** that produced the results.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness; reports embeddings/LLM providers and default strategy |
| `GET` | `/settings` | Full active config (secrets redacted) — lets the UI reflect live state |
| `POST` | `/settings` | Apply config changes from the UI and rebuild the container |
| `GET` | `/diagnose` | Full self-check: Atlas ping, collection stats, embedder probe |
| `POST` | `/diagnose/vector` | Instrumented per-stage vector-search report |
| `POST` | `/retrieve/vector` | Fixed vector (`$vectorSearch` ANN) strategy |
| `POST` | `/retrieve/fulltext` | Fixed full-text (`$search`) strategy |
| `POST` | `/retrieve/hybrid` | Fixed hybrid — native `$rankFusion` (MongoDB 8.0+) |
| `POST` | `/retrieve/graph` | Fixed GraphRAG (`$graphLookup`) strategy |
| `POST` | `/retrieve/parent-doc` | Fixed parent-document strategy |
| `POST` | `/retrieve/metadata` | Structured `$match`/`$sort`/`$limit` (rankings, superlatives, exact lookups) |
| `POST` | `/retrieve` | Auto mode: understand → plan → retrieve → summarize |
| `POST` | `/query` | Alias for `/retrieve` |

Request body (`RetrieveRequest`): `query`, `top_k` (1–100), optional `filters`,
and optional per-request `atlas` / `retrieval` overrides.

## Retrieval strategies

`RetrieverFactory` (`AiSearch/retrieval/factory.py`) dispatches on the plan's
strategy:

| Strategy | What it does |
|---|---|
| `vector` | `$vectorSearch` ANN with pre-filters and candidate oversampling |
| `fulltext` | Lexical `$search` on the text field |
| `hybrid` | Native **`$rankFusion`** fusing vector + full-text server-side (MongoDB 8.0+) |
| `graph` | Multi-hop `$graphLookup` traversal over `entities` (GraphRAG) |
| `parent_doc` | Child-chunk match returning the parent document |
| `metadata` | Pure-MQL `$match` → `$sort` → `$limit` for non-semantic / superlative / ordering queries — no vector or text search |

The **metadata** stage answers questions like "top-rated movies since 2015" or
exact-value lookups entirely with structured aggregation. Query understanding
routes to it when intent is `ordering`/`lookup` and a sort or metadata filter is
present; a `$type: "number"` guard keeps empty/missing values from sorting ahead
of real numbers.

## Query understanding & planning

- **QueryUnderstandingLayer** (`query_understanding/layer.py`) — one LLM call
  yields rewrite, entities, typed `facts` (`{field, op, value}`), a validated
  Mongo `sort`, a `limit`, and an `intent`. Typed facts are canonicalized to
  indexed paths and split into fast Atlas **pre-filters** vs in-memory
  **post-filters** (`facts.py`). L1 in-process cache + L2 persistent
  **FactStore** (`query_facts` collection, audit log).
- **RetrievalPlanner** (`planning/engine.py`) — proposes a draft plan, merges
  understood-query metadata/sort/limit, forces `metadata` for structured intent,
  then clamps via the PolicyStore.
- **PolicyStore** (`planning/policy.py`) — Atlas-managed guardrails
  (`retrieval_policies` collection): clamps strategy to an allowlist, `top_k` to
  a min/max, and applies a metadata whitelist to filters.
- **Pipeline capture** (`observability/pipeline_capture.py`) — a pymongo command
  listener records the actual `aggregate` pipeline executed for each retrieval
  (query vectors summarized); surfaced in the API `pipeline` field and the UI.

## Testing the MCP Server (cURL / Postman)

The MCP server uses the **Streamable HTTP** transport (default endpoint
`http://localhost:8001/mcp`) and follows the JSON-RPC 2.0 protocol. You can
exercise it with any HTTP client (cURL, Postman, Insomnia, HTTPie, ...).

> ⚠️ **Trailing-slash gotcha.** The server mounts at `/mcp`. Hitting
> `http://localhost:8001/mcp/` returns:
>
> ```
> HTTP/1.1 307 Temporary Redirect
> location: http://localhost:8001/mcp
> ```
>
> cURL/Postman will not replay the `POST` body on a 307 by default — the
> follow-up becomes a `GET` and the call silently fails. **Always use
> `/mcp` (no trailing slash).** If you must follow the redirect, add
> `-L --post301 --post302 --post303` to cURL, or enable
> *Automatically follow redirects* + *Follow original HTTP method* in
> Postman (Settings → General).

### Required headers

Every request **must** include:

```
Content-Type: application/json
Accept: application/json, text/event-stream
```

Responses are returned as **Server-Sent Events** (SSE). When using cURL,
add `-N` (no buffering) to stream them.

### 1. Initialize a session

The first call must be `initialize`. The response includes an
`mcp-session-id` header that you must echo on every subsequent request.

```bash
curl -N -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -D - \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "curl", "version": "1.0"}
    }
  }'
```

Grab the `mcp-session-id` value from the response headers, then send the
`notifications/initialized` notification:

```bash
curl -N -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: f927b6cbfa8a489cafaa88b7b39c0529" \
  -d '{
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
    "params": {}
  }'
```

### 2. List available tools

```bash
curl -N -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <SESSION_ID>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }'
```

You should see: `vector_search`, `fulltext_search`, `hybrid_search`,
`graph_search`, `parent_doc_search`, `metadata_search`, `auto_search`.

### 3. Call a tool

```bash
curl -N -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <SESSION_ID>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "hybrid_search",
      "arguments": {
        "query": "What is vector search in MongoDB Atlas?",
        "top_k": 5
      }
    }
  }'
```

Every tool shares the same argument signature:

```json
{ "name": "vector_search",     "arguments": { "query": "...", "top_k": 10 } }
{ "name": "fulltext_search",   "arguments": { "query": "...", "top_k": 10 } }
{ "name": "graph_search",      "arguments": { "query": "...", "top_k": 10 } }
{ "name": "parent_doc_search", "arguments": { "query": "...", "top_k": 10 } }
{ "name": "metadata_search",   "arguments": { "query": "...", "top_k": 10 } }
{ "name": "auto_search",       "arguments": { "query": "...", "top_k": 10 } }
```

Each tool also accepts optional `filters` (dict), `atlas` (per-request Atlas
overrides), and `retrieval` (per-request retrieval overrides). For example,
`"filters": {"source": "docs"}` constrains results by metadata.

### Using Postman

1. Create a **POST** request to `http://localhost:8001/mcp`.
2. Under **Headers**, add:
   - `Content-Type: application/json`
   - `Accept: application/json, text/event-stream`
3. Send the `initialize` body (see step 1 above) and copy `mcp-session-id`
   from the response headers.
4. Add a new header `mcp-session-id: <SESSION_ID>` to all subsequent requests.
5. Postman renders SSE responses inline — expand the `data:` frame to see
   the JSON-RPC payload.

> Tip: save the requests as a Postman collection and use a collection
> variable (`{{sessionId}}`) populated from the `initialize` response via a
> **Tests** script:
>
> ```js
> pm.collectionVariables.set("sessionId", pm.response.headers.get("mcp-session-id"));
> ```

### Quick sanity check

If the server is running you can hit it with:

```bash
curl -i http://localhost:8001/mcp \
  -H "Accept: application/json, text/event-stream"
```

A `405` or `400` response (rather than connection refused) confirms the
server is up; MCP requires `POST` with a valid JSON-RPC body. A plain
`GET /healthz` route is also exposed for load-balancer health checks.

## React testing UI

`AiSearch/ui_react` is a Vite + TypeScript + React playground for both
backends. It shows conversation turns, per-turn results, the LLM summary, an
understood-query intent panel (rewrite, entities, filters, chosen strategy),
latency timings, and the real captured MongoDB aggregation pipeline. Backend
URLs are injected at runtime via `public/config.js` (so one build points at any
backend), falling back to `http://localhost:8000` and
`http://localhost:8001/mcp`.

```bash
cd AiSearch/ui_react
npm install
npm run dev        # dev server
npm run build      # production build → dist/
```

## End-to-end flow

```mermaid
flowchart TD
    YAML["AiSearch.yaml"] --> AppConfig["AppConfig (loader)"]
    AppConfig --> AtlasFactory["AtlasFactory"]
    AppConfig --> EmbeddingFactory["EmbeddingFactory"]
    AppConfig --> LLMFactory["LLMFactory"]

    AtlasFactory --> Collection[("Atlas collection")]
    EmbeddingFactory --> Embeddings["Embeddings"]
    LLMFactory --> ChatModel["ChatModel"]

    Collection --> VectorStore["MongoDBAtlasVectorSearch"]
    Embeddings --> VectorStore

    ChatModel --> QUL["QueryUnderstandingLayer + FactStore"]
    ChatModel --> Planner["RetrievalPlanner + PolicyStore"]
    VectorStore --> RetrieverFactory["RetrieverFactory"]
    Collection --> RetrieverFactory
    ChatModel --> RetrieverFactory

    QUL --> Surfaces
    Planner --> Surfaces
    RetrieverFactory --> Surfaces

    subgraph Surfaces["Surfaces"]
        FastAPI["FastAPI"]
        FastMCP["FastMCP"]
        UI["React UI"]
    end

    Surfaces -.->|real pipeline capture| Pipeline["Captured Atlas aggregation"]
```

**Components**

- **AiSearch.yaml** — The single source of truth for providers, models,
  indexes, and runtime options. Every value is `${VAR:-default}`-driven so the
  deployment can be retuned via environment variables without a rebuild.
- **AppConfig (loader)** — Parses the YAML, expands environment variables, and
  produces the validated Pydantic config that wires the entire container.
- **AtlasFactory** — Builds the MongoDB client, database, and collection
  handles from the Atlas config, and exposes health/stats helpers.
- **EmbeddingFactory** — Constructs the query-embedding provider (auto /
  AutoEmbeddings, Voyage, OpenAI, Gemini, Titan, ...) selected in config.
- **LLMFactory** — Constructs the chat model (Gemini, OpenAI, Anthropic,
  Bedrock, ...) used by query understanding and planning.
- **Atlas collection** — The MongoDB collection holding the documents/chunks
  and their vector/search indexes that every retriever queries.
- **Embeddings** — The concrete embedder that turns a query into a vector (or
  defers to server-side AutoEmbeddings in auto mode).
- **ChatModel** — The concrete LLM instance shared by the understanding and
  planning layers for reasoning over the query.
- **MongoDBAtlasVectorSearch** — The LangChain vector store binding the
  collection and embeddings together for `$vectorSearch` retrieval.
- **QueryUnderstandingLayer + FactStore** — Rewrites the query and extracts
  entities, typed facts, sort, limit, and intent; the FactStore caches and
  audits those results in the `query_facts` collection.
- **RetrievalPlanner + PolicyStore** — Chooses the retrieval strategy and
  parameters from the understood query, then clamps the plan against
  Atlas-managed guardrails (allowed strategies, top_k bounds, filter whitelist).
- **RetrieverFactory** — Instantiates the chosen retriever
  (vector / fulltext / hybrid / graph / parent_doc / metadata) and runs it
  against the vector store and collection.
- **Surfaces (FastAPI / FastMCP / React UI)** — The REST API, MCP tool server,
  and testing UI that expose retrieval to clients; all share the same wired
  container.
- **Captured Atlas aggregation** — The observability layer records the real
  aggregation pipeline each retrieval executed and returns it in responses for
  transparency and debugging.

---

## Deployment

Deployment artifacts live under `deployment/`. Three cloud targets are
available; each folder has its own README/guide with full details, config
knobs, and teardown.

| Target | Folder | Guide |
|---|---|---|
| **AWS** | [`deployment/aws/`](deployment/aws/) | [deployment/aws/README.md](deployment/aws/README.md) |
| **Azure** | [`deployment/azure/`](deployment/azure/) | [deployment/azure/DEPLOYMENT.md](deployment/azure/DEPLOYMENT.md) |
| **Google Cloud Run** | [`deployment/google/`](deployment/google/) | see below |

### AWS

Three independent pieces — see the [AWS deployment guide](deployment/aws/README.md)
for full details:

| # | What | Where it runs | Script | Guide | Default? |
|---|---|---|---|---|---|
| 1 | React UI | S3 static website (existing bucket) | [`s3-ui/deploy.sh`](deployment/aws/s3-ui/deploy.sh) | [s3-ui/README.md](deployment/aws/s3-ui/README.md) | yes |
| 2 | FastAPI + FastMCP backends | ECS **Express Mode** | [`ecs/deploy.sh`](deployment/aws/ecs/deploy.sh) | [ecs/README.md](deployment/aws/ecs/README.md) | **yes** |
| 3 | FastMCP backend (alternative) | Bedrock **AgentCore Runtime** | [`agentcore/deploy.sh`](deployment/aws/agentcore/deploy.sh) | [agentcore/README.md](deployment/aws/agentcore/README.md) | no — opt-in |

```bash
export AWS_REGION=us-east-1
export ATLAS_URI='mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true'
export ATLAS_DB='your_database_name'

# 1. Deploy the backends (default: ECS Express Mode). Prints two HTTPS URLs.
./deployment/aws/ecs/deploy.sh

# 2. Deploy the UI to your existing S3 bucket, pointing at those URLs.
./deployment/aws/s3-ui/deploy.sh \
  --bucket my-existing-ui-bucket \
  --api-url "https://AiSearch-fastapi.ecs.${AWS_REGION}.on.aws" \
  --mcp-url "https://AiSearch-fastmcp.ecs.${AWS_REGION}.on.aws/mcp"

# Optional: FastMCP on Bedrock AgentCore (requires typed confirmation)
./deployment/aws/agentcore/deploy.sh
```

### Azure

Deploys the three surfaces to **Azure Container Apps** and wires the MCP
endpoint into an **AI Foundry** agent. Infrastructure (resource group, ACR, Log
Analytics, Container Apps Environment, managed identity, three Container Apps) is
provisioned by subscription-scope Bicep with a readiness gate. Full walkthrough:
[Azure deployment guide](deployment/azure/DEPLOYMENT.md).

```bash
# 1. Create resource group + ACR
az deployment sub create --name AiSearch-acr --location centralindia \
  --template-file deployment/azure/infra/acr.bicep \
  --parameters deployment/azure/infra/main.parameters.json
ACR_NAME=$(az deployment sub show -n AiSearch-acr --query properties.outputs.acrName.value -o tsv)

# 2. Build & push all three images (server-side in ACR)
./deployment/azure/scripts/build-and-push.sh "$ACR_NAME" latest

# 3. Deploy infra + Container Apps
az deployment sub create --name AiSearch --location centralindia \
  --template-file deployment/azure/infra/main.bicep \
  --parameters deployment/azure/infra/main.parameters.json \
  --parameters atlasUri="$ATLAS_URI" voyageApiKey="$VOYAGE_API_KEY" \
      openaiApiKey="$OPENAI_API_KEY" mcpApiKey="$MCP_API_KEY"
```

The MCP endpoint is Bearer-gated (`MCP_API_KEY`). See the
[Azure deployment guide](deployment/azure/DEPLOYMENT.md) for config overrides and
AI Foundry agent setup, and its [Bicep templates](deployment/azure/infra/) and
[build/push script](deployment/azure/scripts/build-and-push.sh).

### Google Cloud Run

**Prerequisites:** [gcloud CLI](https://cloud.google.com/sdk/docs/install)
authenticated, Docker running, a GCP project with billing, and a populated
`.env`.

**Option A — Single service (recommended).** One image, one URL; nginx serves
the React SPA and proxies API/MCP traffic to internal backends (no CORS config).
Script: [`deployment/google/cloud_run/deploy-combined.sh`](deployment/google/cloud_run/deploy-combined.sh).

```bash
chmod +x deployment/google/cloud_run/deploy-combined.sh
./deployment/google/cloud_run/deploy-combined.sh --project <PROJECT_ID> --region <REGION>
```

| Endpoint | Description |
|---|---|
| `/` | React UI |
| `/retrieve`, `/health`, `/diagnose`, `/docs` | FastAPI REST |
| `/mcp` | FastMCP (JSON-RPC over SSE) |

**Option B — Separate services.** Three independent Cloud Run services, useful
to scale the API and MCP independently from the frontend.
Script: [`deployment/google/cloud_run/deploy.sh`](deployment/google/cloud_run/deploy.sh).

```bash
chmod +x deployment/google/cloud_run/deploy.sh
./deployment/google/cloud_run/deploy.sh --project <PROJECT_ID> --region <REGION>
```

| Cloud Run service | URL path | Description |
|---|---|---|
| `AiSearch-api` | `/retrieve*`, `/health`, `/docs` | FastAPI REST server |
| `AiSearch-mcp` | `/mcp` | FastMCP server |
| `AiSearch-frontend` | `/` | React SPA (pre-configured with backend URLs) |

**Update frontend backend URLs after deploy:**

```bash
gcloud run services update AiSearch-frontend \
  --region=<REGION> \
  --update-env-vars="AISEARCH_API_URL=https://AiSearch-api-xyz.run.app,AISEARCH_MCP_URL=https://AiSearch-mcp-xyz.run.app/mcp"
```

**Update CORS allowed origins:**

```bash
gcloud run services update AiSearch-api \
  --region=<REGION> \
  --update-env-vars="CORS_ORIGINS=https://AiSearch-frontend-xyz.run.app"
```
