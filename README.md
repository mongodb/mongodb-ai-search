# SearchaaS — Phase 1

MongoDB Atlas-backed retrieval platform built around the **Factory pattern**.
Phase 1 ships: query understanding, AI-driven retrieval planning, vector /
full-text / hybrid / graph / parent-doc retrieval, YAML configuration, a
FastAPI REST surface, a FastMCP tool surface, and a React testing UI.

See `Instructions.md` for the full architecture.

## Layout

```
searchaas/
  config/             # YAML config + loader (single source of truth)
  infrastructure/     # AtlasFactory (Mongo client / db / collections)
  domain/             # Pydantic models (Chunk, SourceRef)
  embeddings/         # EmbeddingFactory (Gemini, Bedrock Titan, OpenAI, ...)
  llm/                # LLMFactory (Gemini, Azure/OpenAI, Anthropic, Bedrock)
  query_understanding/# Query rewriting, entity & metadata extraction, intent
  planning/           # RetrievalPlanner + Atlas-managed PolicyStore
  retrieval/          # RetrieverFactory (vector/fulltext/hybrid/graph/parent_doc)
  app/                # build_container() — wires every factory from AppConfig
  api/                # FastAPI surface
  mcp_server/         # FastMCP surface
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # fill in ATLAS_URI, GOOGLE_API_KEY, ...
```

Edit `searchaas/config/searchaas.yaml` to pick providers/models. By default
this build uses **Gemini** for both embeddings and the planner LLM; flip to
`bedrock_titan` to use Amazon Titan embeddings.

## Run

```bash
# REST API
uvicorn searchaas.api.app:app --host 0.0.0.0 --port 8000

# FastMCP server
python -m searchaas.mcp_server.server

```

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
`graph_search`, `parent_doc_search`, `auto_search`.

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

Other tools accept the same shape:

```json
{ "name": "vector_search",     "arguments": { "query": "...", "top_k": 10 } }
{ "name": "fulltext_search",   "arguments": { "query": "...", "top_k": 10 } }
{ "name": "graph_search",      "arguments": { "query": "...", "top_k": 10 } }
{ "name": "parent_doc_search", "arguments": { "query": "...", "top_k": 10 } }
{ "name": "auto_search",       "arguments": { "query": "...", "top_k": 10 } }
```

Optional `filters` (a dict) may be passed in `arguments` to constrain
metadata, e.g. `"filters": {"source": "docs"}`.

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
server is up; MCP requires `POST` with a valid JSON-RPC body.

## End-to-end flow

```
YAML  ->  AppConfig
          |
          +-- AtlasFactory  ---> collection
          +-- EmbeddingFactory ---> Embeddings
          +-- LLMFactory       ---> ChatModel
          |
          +-- MongoDBAtlasVectorSearch(collection, embeddings, ...)
          |
          +-- QueryUnderstandingLayer(llm)
          +-- RetrievalPlanner(llm, PolicyStore)
          +-- RetrieverFactory(vector_store, llm, collection, ...)
          |
          v
FastAPI  /  FastMCP  /  React UI
```

Every concern is swappable through YAML — no code changes required to
switch embeddings, LLMs, or retrieval strategy.

---

## Deploy to Google Cloud Run

All deployment files live under `deployment/`. Two options are available.

### Prerequisites

- [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated (`gcloud auth login`)
- Docker installed and running
- A GCP project with billing enabled
- A populated `.env` file (`cp .env.example .env` then fill in secrets)

---

### Option A — Single service (recommended)

One image, one Cloud Run URL. nginx serves the React SPA and proxies all API
and MCP traffic to internal backends — no CORS configuration needed.

```bash
chmod +x deployment/google/deploy-combined.sh
./deployment/google/deploy-combined.sh --project <PROJECT_ID> --region <REGION>
```

**What it deploys:**

| Endpoint | Description |
|---|---|
| `/` | React UI |
| `/retrieve`, `/health`, `/diagnose`, `/docs` | FastAPI REST |
| `/mcp` | FastMCP (JSON-RPC over SSE) |

**Files used:**

| File | Purpose |
|---|---|
| `deployment/google/deploy-combined.sh` | Build, push, and deploy the combined image |
| `deployment/google/Dockerfile.combined` | 3-stage build: Node (Vite) → Python (pip) → nginx+Python runtime |
| `deployment/google/nginx-combined.conf` | nginx reverse-proxy config for SPA + backends |
| `deployment/google/docker-entrypoint-combined.sh` | Starts backends, waits for `/health`, then starts nginx |

---

### Option B — Separate services

Three independent Cloud Run services — useful when you want to scale the API
and MCP server independently from the frontend.

```bash
chmod +x deployment/google/deploy.sh
./deployment/google/deploy.sh --project <PROJECT_ID> --region <REGION>
```

**What it deploys:**

| Cloud Run service | URL path | Description |
|---|---|---|
| `searchaas-api` | `/retrieve*`, `/health`, `/docs` | FastAPI REST server |
| `searchaas-mcp` | `/mcp` | FastMCP server |
| `searchaas-frontend` | `/` | React SPA (pre-configured with backend URLs) |

**Files used:**

| File | Purpose |
|---|---|
| `deployment/google/deploy.sh` | Build, push, and deploy all three services |
| `deployment/google/Dockerfile.api` | FastAPI REST server image |
| `deployment/google/Dockerfile` | FastMCP server image |
| `deployment/google/Dockerfile.frontend` | React SPA via nginx |
| `deployment/google/nginx.conf` | nginx config with SPA routing and asset caching |
| `deployment/google/docker-entrypoint-frontend.sh` | Injects backend URLs into `/config.js` at startup |

**Updating the frontend backend URLs after deploy:**

```bash
gcloud run services update searchaas-frontend \
  --region=<REGION> \
  --update-env-vars="SEARCHAAS_API_URL=https://searchaas-api-xyz.run.app,SEARCHAAS_MCP_URL=https://searchaas-mcp-xyz.run.app/mcp"
```

**Updating CORS allowed origins:**

```bash
gcloud run services update searchaas-api \
  --region=<REGION> \
  --update-env-vars="CORS_ORIGINS=https://searchaas-frontend-xyz.run.app"
```
