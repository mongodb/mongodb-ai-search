# Deploying SearchaaS to Azure Container Apps + AI Foundry

This guide deploys the three SearchaaS surfaces to **Azure Container Apps** and
wires the MCP endpoint into an **AI Foundry** agent.

| Surface | Image | Port | Ingress | Purpose |
|---------|-------|------|---------|---------|
| MCP server | `searchaas-mcp` | 8001 | external `/mcp` | Consumed by AI Foundry agents (Bearer-gated) |
| REST API | `searchaas-api` | 8000 | external | Playground backend |
| React UI | `searchaas-ui` | 80 | external | Retrieval-testing playground |

Infrastructure (resource group, ACR, Log Analytics, Container Apps Environment,
managed identity, three Container Apps) is provisioned by Bicep at
**subscription scope** — the resource group is created for you.

---

## Prerequisites

- Azure CLI (`az`) logged in: `az login`
- A subscription with permission to create resource groups and role assignments
- Secrets ready: `ATLAS_URI`, `VOYAGE_API_KEY` (embeddings), `OPENAI_API_KEY`
  (planner LLM — default provider), and a freshly generated `MCP_API_KEY` (any
  strong random string). `GOOGLE_API_KEY` is optional (only if you switch the
  planner back to gemini):

  ```bash
  export ATLAS_URI='mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true&w=majority'
  export VOYAGE_API_KEY='...'
  export OPENAI_API_KEY='sk-...'
  export GOOGLE_API_KEY=''          # optional
  export MCP_API_KEY="$(openssl rand -hex 32)"
  echo "MCP_API_KEY=$MCP_API_KEY"   # save this — Foundry needs it
  ```

> The container images must exist in ACR **before** the Container Apps start
> cleanly, so we deploy in two steps: (1) create RG + ACR, (2) build & push
> images, (3) deploy the apps.

---

## Step 1 — Create the resource group + ACR

```bash
az deployment sub create \
  --name searchaas-acr \
  --location eastus \
  --template-file infra/acr.bicep \
  --parameters infra/main.parameters.json

ACR_NAME=$(az deployment sub show -n searchaas-acr \
  --query properties.outputs.acrName.value -o tsv)
echo "ACR: $ACR_NAME"
```

## Step 2 — Build & push all three images (server-side in ACR)

```bash
./scripts/build-and-push.sh "$ACR_NAME" latest
```

This runs `az acr build` for `searchaas-mcp`, `searchaas-api`, and
`searchaas-ui`. Builds happen in ACR, so no local Docker or matching CPU
architecture is needed.

## Step 3 — Deploy the full stack (Container Apps)

```bash
az deployment sub create \
  --name searchaas \
  --location eastus \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters \
      atlasUri="$ATLAS_URI" \
      voyageApiKey="$VOYAGE_API_KEY" \
      openaiApiKey="$OPENAI_API_KEY" \
      googleApiKey="$GOOGLE_API_KEY" \
      mcpApiKey="$MCP_API_KEY"
```

Grab the URLs:

```bash
az deployment sub show -n searchaas --query properties.outputs -o json
```

You'll get:

```json
{
  "mcpUrl": { "value": "https://searchaas-mcp.<...>.azurecontainerapps.io/mcp" },
  "apiUrl": { "value": "https://searchaas-api.<...>.azurecontainerapps.io" },
  "uiUrl":  { "value": "https://searchaas-ui.<...>.azurecontainerapps.io" }
}
```

---

## Step 4 — Smoke test the MCP endpoint

Initialize a session (note the Bearer header):

```bash
MCP_URL="https://searchaas-mcp.<...>.azurecontainerapps.io/mcp"

curl -N -X POST "$MCP_URL" \
  -H "Authorization: Bearer $MCP_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -D - \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'
```

- Without the `Authorization` header you should get **HTTP 401**.
- With it, you get a `200` and an `mcp-session-id` response header.

> Always hit `/mcp` (no trailing slash). See `README.md` for full MCP cURL
> examples (`tools/list`, `tools/call`).

---

## Step 5 — Use the playground UI

Open the `uiUrl` in a browser. In the UI's connection settings, point it at:

- **FastAPI URL** → the `apiUrl`
- **MCP URL** → the `mcpUrl`

(The UI sends requests from the browser, so the apps' CORS is set to allow any
origin. Tighten `allowedOrigins` in `infra/resources.bicep` for production.)

---

## Step 6 — Create an AI Foundry agent backed by the MCP server

**This deployment's live values:**

| | |
|--|--|
| MCP URL | `https://searchaas-mcp.delightfulwater-e493d0b4.eastus.azurecontainerapps.io/mcp` |
| API URL | `https://searchaas-api.delightfulwater-e493d0b4.eastus.azurecontainerapps.io` |
| UI URL  | `https://searchaas-ui.delightfulwater-e493d0b4.eastus.azurecontainerapps.io` |
| Auth header | `Authorization: Bearer <MCP_API_KEY>` (key generated at deploy; rotate via `az containerapp secret set`) |

1. In the **AI Foundry** portal, open your project and create/edit an agent.
2. Add a tool of type **MCP** (Model Context Protocol).
3. Configure it:
   - **Server URL**: the `mcpUrl` above
   - **Transport**: Streamable HTTP
   - **Custom header**: `Authorization: Bearer <MCP_API_KEY>`
4. The agent will discover these tools:
   - `vector_search` — Atlas Vector Search (semantic)
   - `fulltext_search` — Atlas Search (lexical)
   - `hybrid_search` — fused vector + full-text
   - `graph_search` — multi-hop `$graphLookup` / GraphRAG
   - `parent_doc_search` — child-chunk match returning the parent doc
   - `auto_search` — planner picks the strategy + returns a summary
5. Each tool accepts `query` (string), `top_k` (int, default 20), and optional
   `filters` (object).

Test the agent in the Foundry playground with a prompt like:
*"Use hybrid_search to find docs about vector search in MongoDB Atlas."*

---

## Configuration reference

Environment variables consumed by the containers (set via Bicep secrets/env):

| Variable | Image | Source | Notes |
|----------|-------|--------|-------|
| `ATLAS_URI` | mcp, api | secret | Mongo connection string |
| `ATLAS_DB` | mcp, api | param | DB name (`atlasDb`, default `amazon`) |
| `VOYAGE_API_KEY` | mcp, api | secret | Query embeddings (voyageai) |
| `OPENAI_API_KEY` | mcp, api | secret | Planner LLM (openai, default) |
| `GOOGLE_API_KEY` | mcp, api | secret | Optional — only if planner=gemini |
| `MCP_API_KEY` | mcp | secret | Bearer token; **unset = no auth** |
| `ALLOWED_ORIGINS` | mcp | env (`*`) | CORS origins; comma-separated or `*` |

### Runtime config overrides (no image rebuild)

`searchaas/config/searchaas.yaml` is baked into the image, but **every value is
`${VAR:-default}`-driven**, so you can retune the deployment by setting env vars
via the `configOverrides` Bicep parameter — then redeploy/restart. **No image
rebuild required.**

| Override var | Controls | Default |
|--------------|----------|---------|
| `ATLAS_COLLECTION` | Mongo collection | `products-updated` |
| `ATLAS_VECTOR_INDEX` | Atlas Vector Search index | `voyage_vector_index` |
| `ATLAS_SEARCH_INDEX` | Atlas Search (Lucene) index | `default` |
| `ATLAS_TEXT_KEY` | text field name | `text` |
| `ATLAS_EMBEDDING_KEY` | vector field name | `embedding-vectors` |
| `ATLAS_DIMENSIONS` | vector dimensions | `512` |
| `EMBEDDINGS_PROVIDER` | embedding provider | `voyageai` |
| `EMBEDDINGS_MODEL` | embedding model | `voyage-4` |
| `EMBEDDINGS_OUTPUT_DIMENSION` | query-embedding dims | `512` |
| `PLANNER_LLM_PROVIDER` | planner LLM provider | `openai` |
| `PLANNER_MODEL` | planner model | `gpt-4o-mini` |
| `PLANNER_TEMPERATURE` | planner temperature | `0.1` |
| `RETRIEVAL_DEFAULT_STRATEGY` | default strategy | `hybrid` |
| `RETRIEVAL_HYBRID_VECTOR_WEIGHT` | hybrid vector weight | `0.6` |
| `RETRIEVAL_HYBRID_FULLTEXT_WEIGHT` | hybrid fulltext weight | `0.4` |
| `RETRIEVAL_VECTOR_NUM_CANDIDATES` | vector candidate pool | `200` |

Edit the `configOverrides` block in `infra/main.parameters.json` (or pass
`--parameters configOverrides='{...}'`) and rerun the Step 3 deploy command.
Only non-empty entries are injected; everything else uses the yaml default.

---

## Updating after code changes

```bash
# rebuild & push with a new tag
./scripts/build-and-push.sh "$ACR_NAME" v2

# roll the apps to the new tag
az deployment sub create \
  --name searchaas \
  --location eastus \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters imageTag=v2 \
      atlasUri="$ATLAS_URI" voyageApiKey="$VOYAGE_API_KEY" \
      openaiApiKey="$OPENAI_API_KEY" googleApiKey="$GOOGLE_API_KEY" \
      mcpApiKey="$MCP_API_KEY"
```

Container Apps creates a new revision and shifts traffic to it.

---

## Notes & recommendations

- **MCP min-replicas = 1**: the `streamable-http` transport is session-stateful
  (`mcp-session-id`), so scale-to-zero would drop active sessions. The API and
  UI apps scale to zero when idle to save cost.
- **Secrets** are stored as Container Apps secrets (per project decision). The
  YAML loader already supports `${VAR}` expansion, so migrating to **Azure Key
  Vault** later (via managed identity + secret references) requires no code
  change — only Bicep updates.
- **Tighten CORS** for production by replacing `*` in `infra/resources.bicep`
  (`corsPolicy.allowedOrigins` and the MCP `ALLOWED_ORIGINS` env) with the
  exact UI FQDN.
- **Auth hardening**: for enterprise use, consider fronting the MCP app with
  Entra ID / OAuth2 instead of (or in addition to) the static Bearer key.
