# Deploying AiSearch to Azure Container Apps + AI Foundry

This guide deploys the three AiSearch surfaces to **Azure Container Apps** and
wires the MCP endpoint into an **AI Foundry** agent.

| Surface | Image | Port | Ingress | Purpose |
|---------|-------|------|---------|---------|
| MCP server | `AiSearch-mcp` | 8001 | external `/mcp` | Consumed by AI Foundry agents (Bearer-gated) |
| REST API | `AiSearch-api` | 8000 | external | Playground backend |
| React UI | `AiSearch-ui` | 80 | external | Retrieval-testing playground |

Infrastructure (resource group, ACR, Log Analytics, Container Apps Environment,
managed identity, three Container Apps) is provisioned by Bicep at
**subscription scope** — the resource group is created for you.

The deployment is structured to be reliable and repeatable:

- **Step 1** creates the resource group + ACR only (no secrets needed).
- **Step 2** builds and pushes images into ACR.
- **Step 3** deploys everything in one `az deployment sub create`. Internally
  `main.bicep` runs two ARM child deployments in sequence — first infra
  (environment, ACR, identity), then Container Apps. A deployment-script
  readiness gate (`AiSearch-env-ready`) polls the Container Apps Environment
  until its `provisioningState` is genuinely `Succeeded`; the apps module
  depends on that gate's output, so apps never start while the environment is
  still `Updating`. This eliminates the intermittent
  `ManagedEnvironmentNotProvisioned` error.

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

---

## Step 1 — Create the resource group + ACR

Make any changes required to `main.parameters.json` (location, namePrefix, atlasDb, configOverrides) before running this step.

```bash
az deployment sub create \
  --name AiSearch-acr \
  --location centralindia \
  --template-file deployment/azure/infra/acr.bicep \
  --parameters deployment/azure/infra/main.parameters.json

ACR_NAME=$(az deployment sub show -n AiSearch-acr \
  --query properties.outputs.acrName.value -o tsv)
echo "ACR: $ACR_NAME"
```

## Step 2 — Build & push all three images (server-side in ACR)

The script automatically imports the required base images (`python:3.11-slim`,
`node:20-alpine`, `nginx:alpine`) into ACR on first run and skips them on
subsequent runs. To avoid Docker Hub anonymous pull rate limits, provide a
free Docker Hub account's credentials:

```bash
export DOCKER_HUB_USERNAME="your-dockerhub-username"
export DOCKER_HUB_TOKEN="your-dockerhub-access-token"   # hub.docker.com → Account Settings → Personal Access Tokens
```

Then build and push:

```bash
./deployment/azure/scripts/build-and-push.sh "$ACR_NAME" latest
```

This runs `az acr build` for `AiSearch-mcp`, `AiSearch-api`, and
`AiSearch-ui`. Builds happen in ACR, so no local Docker or matching CPU
architecture is needed.

## Step 3 — Deploy infrastructure + Container Apps

This single command runs two ARM deployments in sequence under the hood:
`AiSearch-resources` (infra) completes first, then `AiSearch-apps` starts
once the Container Apps Environment is in `Succeeded` state.

```bash
az deployment sub create \
  --name AiSearch \
  --location centralindia \
  --template-file deployment/azure/infra/main.bicep \
  --parameters deployment/azure/infra/main.parameters.json \
  --parameters \
      atlasUri="$ATLAS_URI" \
      voyageApiKey="$VOYAGE_API_KEY" \
      openaiApiKey="$OPENAI_API_KEY" \
      googleApiKey="${GOOGLE_API_KEY:-}" \
      mcpApiKey="$MCP_API_KEY"
```

> `GOOGLE_API_KEY` and `AZURE_OPENAI_API_KEY` are optional. Omit or pass empty
> if you are not using those providers.

Grab the URLs:

```bash
az deployment sub show -n AiSearch --query properties.outputs -o json
```

You'll get:

```json
{
  "mcpUrl": { "value": "https://AiSearch-mcp.<...>.azurecontainerapps.io/mcp" },
  "apiUrl": { "value": "https://AiSearch-api.<...>.azurecontainerapps.io" },
  "uiUrl":  { "value": "https://AiSearch-ui.<...>.azurecontainerapps.io" }
}
```

---

## Step 4 — Smoke test the MCP endpoint

Initialize a session (note the Bearer header):

```bash
MCP_URL="https://AiSearch-mcp.<...>.azurecontainerapps.io/mcp"

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
origin. Tighten `allowedOrigins` in `deployment/azure/infra/resources.bicep` for production.)

---

## Step 6 — Create an AI Foundry agent backed by the MCP server

Use the URLs from Step 3's output:

```bash
az deployment sub show -n AiSearch --query 'properties.outputs.{mcp:mcpUrl.value,api:apiUrl.value,ui:uiUrl.value}' -o json
```

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

`AiSearch/config/AiSearch.yaml` is baked into the image, but **every value is
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

Edit the `configOverrides` block in `deployment/azure/infra/main.parameters.json` (or pass
`--parameters configOverrides='{...}'`) and rerun the Step 3 deploy command.
Only non-empty entries are injected; everything else uses the yaml default.

---

## Updating after code changes

```bash
# rebuild & push with a new tag
./deployment/azure/scripts/build-and-push.sh "$ACR_NAME" v2

# roll the apps to the new tag (same command as Step 3, just add imageTag=v2)
az deployment sub create \
  --name AiSearch \
  --location centralindia \
  --template-file deployment/azure/infra/main.bicep \
  --parameters deployment/azure/infra/main.parameters.json \
  --parameters imageTag=v2 \
      atlasUri="$ATLAS_URI" voyageApiKey="$VOYAGE_API_KEY" \
      openaiApiKey="$OPENAI_API_KEY" googleApiKey="${GOOGLE_API_KEY:-}" \
      mcpApiKey="$MCP_API_KEY"
```

Container Apps creates a new revision and shifts traffic to it. The infra
phase is a no-op when nothing has changed (ARM is idempotent), so only the
apps deployment actually updates.

---

## Notes & recommendations

- **MCP min-replicas = 1**: the `streamable-http` transport is session-stateful
  (`mcp-session-id`), so scale-to-zero would drop active sessions. The API and
  UI apps scale to zero when idle to save cost.
- **Secrets** are stored as Container Apps secrets (per project decision). The
  YAML loader already supports `${VAR}` expansion, so migrating to **Azure Key
  Vault** later (via managed identity + secret references) requires no code
  change — only Bicep updates.
- **Tighten CORS** for production by replacing `*` in `deployment/azure/infra/resources.bicep`
  (`corsPolicy.allowedOrigins` and the MCP `ALLOWED_ORIGINS` env) with the
  exact UI FQDN.
- **Auth hardening**: for enterprise use, consider fronting the MCP app with
  Entra ID / OAuth2 instead of (or in addition to) the static Bearer key.

---

## Troubleshooting

### `ManagedEnvironmentNotProvisioned` during Step 3

The Container Apps Environment reports ARM success before its backend has
finished provisioning, and any re-PUT bounces it into `Updating`. If apps are
written during that window they fail with `ManagedEnvironmentNotProvisioned`.
The `AiSearch-env-ready` deployment-script gate now prevents this in fresh
runs. If you still hit it (e.g. on an older template or a partially-failed RG):

1. Wait for the environment to settle to `Succeeded`:

   ```bash
   az resource show -g rg-AiSearch -n AiSearch-env \
     --resource-type Microsoft.App/managedEnvironments \
     --query "properties.provisioningState" -o tsv
   ```

2. Deploy **only** the apps module against the ready environment (no env re-PUT):

   ```bash
   ENV_ID=$(az deployment group show -g rg-AiSearch -n AiSearch-resources \
     --query "properties.outputs.environmentId.value" -o tsv)
   ID_ID=$(az deployment group show -g rg-AiSearch -n AiSearch-resources \
     --query "properties.outputs.identityId.value" -o tsv)
   ACR=$(az deployment group show -g rg-AiSearch -n AiSearch-resources \
     --query "properties.outputs.acrLoginServer.value" -o tsv)

   az deployment group create \
     --resource-group rg-AiSearch \
     --name AiSearch-apps-only \
     --template-file deployment/azure/infra/apps.bicep \
     --parameters location=centralindia namePrefix=AiSearch imageTag=latest \
         atlasDb=sample_mflix uiEmbedMcpKey=true \
         environmentId="$ENV_ID" identityId="$ID_ID" acrServer="$ACR" \
         atlasUri="$ATLAS_URI" voyageApiKey="$VOYAGE_API_KEY" \
         openaiApiKey="$OPENAI_API_KEY" googleApiKey="${GOOGLE_API_KEY:-}" \
         azureOpenaiApiKey="${AZURE_OPENAI_API_KEY:-}" mcpApiKey="$MCP_API_KEY"
   ```

### `InvalidDeploymentLocation` — deployment already exists in another region

A subscription-scope deployment's metadata location is immutable. If a prior
run created the deployment in a different region, use a **new `--name`** (the
`--location` only stores deployment metadata; resources still deploy to the
region in `main.parameters.json`).
