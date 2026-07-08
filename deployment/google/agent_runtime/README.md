# SearchaaS — Google Cloud Agent Runtime Deployment (opt-in)

> **This is NOT the default deployment.** The default Google target is
> **Cloud Run** (`deployment/google/cloud_run/`). Deploy to Agent Runtime only
> when you explicitly want a dedicated private MCP endpoint integrated with
> Vertex AI. The script refuses to run without confirmation.

This packages the SearchaaS FastMCP surface (`searchaas/mcp_server/server.py`)
as an `amd64` container and deploys it as a **private** Cloud Run service
configured for Vertex AI agent connectivity.

Docs:
- <https://cloud.google.com/run/docs/authenticating/service-to-service>
- <https://cloud.google.com/vertex-ai/generative-ai/docs/agent-builder/overview>
- <https://cloud.google.com/vertex-ai/generative-ai/docs/adk/overview>

---

## Agent Runtime contract this deployment satisfies

| Requirement | How it's met |
|---|---|
| Platform: `linux/amd64` | `Dockerfile` targets `linux/amd64`; build uses `buildx` |
| MCP served at `0.0.0.0:8000/mcp` | `MCP_HOST=0.0.0.0`, `MCP_PORT=8000`, transport `streamable-http` |
| Image in Artifact Registry | Script creates `searchaas-agent` repo and pushes the image |
| Private / authenticated | `--no-allow-unauthenticated`, `--ingress=internal-and-cloud-load-balancing` |
| Vertex AI can invoke it | Script binds `roles/run.invoker` to the Vertex AI service account |
| Dedicated service account | Script creates `searchaas-agent-runtime-sa` with Vertex AI + Secret Manager roles |

---

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | amd64 image, FastMCP on `:8000/mcp` (build context = repo root) |
| `deploy.sh` | Opt-in end-to-end: confirm → APIs → SA → secrets → image → Cloud Run |

---

## Run it

```bash
export ATLAS_URI='mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true'
export ATLAS_DB='your_database_name'

# Interactive confirmation (type 'deploy-agent-runtime' when prompted):
./deployment/google/agent_runtime/deploy.sh

# Or skip the prompt in CI:
YES=yes ./deployment/google/agent_runtime/deploy.sh --yes

# With explicit project/region:
./deployment/google/agent_runtime/deploy.sh \
    --project my-gcp-project \
    --region us-central1 \
    --yes
```

The script prints the **MCP endpoint URL** and example invocations:

```
https://searchaas-agent-runtime-<hash>-<region>.run.app/mcp
```

---

## Configuration options

All configuration in `searchaas/config/searchaas.yaml` uses `${VAR:-default}`
syntax, so you configure the runtime purely with **environment variables** —
no image rebuild needed. Export variables before running `deploy.sh` and they
will be forwarded:

```bash
export EMBEDDINGS_PROVIDER=auto
export ATLAS_VECTOR_INDEX=autoembed_index
export ATLAS_DIMENSIONS=-1
./deployment/google/agent_runtime/deploy.sh --yes
```

### Atlas connection & index

| Env var | Default | Purpose |
|---|---|---|
| `ATLAS_URI` | *(required)* | `mongodb+srv://...` connection string |
| `ATLAS_DB` | `amazon` | Database name |
| `ATLAS_COLLECTION` | `pdf_multimodal_chunks` | Collection to search |
| `ATLAS_VECTOR_INDEX` | `aisearch_vector_index` | Atlas Vector Search index |
| `ATLAS_SEARCH_INDEX` | `default` | Atlas Search (Lucene) index for fulltext/hybrid |
| `ATLAS_TEXT_KEY` | `raw_text` | Field holding chunk text |
| `ATLAS_EMBEDDING_KEY` | `embedding` | Field holding the vector |
| `ATLAS_RELEVANCE_FN` | `cosine` | Similarity function |
| `ATLAS_DIMENSIONS` | `1024` | Vector dimensions (must match index and embedder) |

### Embedding provider

| Provider | Required env vars |
|---|---|
| `voyageai` *(default)* | `VOYAGE_API_KEY`, `EMBEDDINGS_MODEL` (`voyage-4`) |
| `gemini` | `GOOGLE_API_KEY`, `EMBEDDINGS_MODEL` (e.g. `text-embedding-004`) |
| `auto` (server-side Atlas) | `EMBEDDINGS_MODEL` only — no API key needed |
| `openai` | `OPENAI_API_KEY`, `EMBEDDINGS_MODEL` |

`EMBEDDINGS_OUTPUT_DIMENSION` must equal `ATLAS_DIMENSIONS`.

#### Two embedding modes (must match your Atlas index type)

- **Mode A — client-side** (default): the app embeds queries locally. Requires
  a `vector`-type index. Keep `ATLAS_EMBEDDING_KEY`, `ATLAS_DIMENSIONS`, and
  `ATLAS_RELEVANCE_FN` set.
- **Mode B — server-side AutoEmbeddings**: Atlas embeds internally. Set
  `EMBEDDINGS_PROVIDER=auto`, `ATLAS_EMBEDDING_KEY=` (empty),
  `ATLAS_DIMENSIONS=-1`, `ATLAS_RELEVANCE_FN=` (empty), and point
  `ATLAS_VECTOR_INDEX` at an `autoEmbed`-type index.

Dev escape hatch: `SEARCHAAS_SKIP_PROVIDER_INDEX_CHECK=1` bypasses startup
validation. Do not use in production.

### Planner LLM

| Provider | Key env vars |
|---|---|
| `gemini` *(recommended for Google Cloud)* | `GOOGLE_API_KEY`, `PLANNER_MODEL` (e.g. `gemini-2.0-flash`) |
| `openai` | `OPENAI_API_KEY`, `PLANNER_MODEL` |
| `anthropic` | `ANTHROPIC_API_KEY`, `PLANNER_MODEL` |
| `bedrock` | `BEDROCK_MODEL`, `BEDROCK_REGION_NAME` |

### Retrieval strategy

| Env var | Default | Options |
|---|---|---|
| `RETRIEVAL_DEFAULT_STRATEGY` | `hybrid` | `vector`, `fulltext`, `hybrid`, `graph`, `parent_doc` |
| `RETRIEVAL_HYBRID_VECTOR_WEIGHT` | `0.6` | hybrid vector weight |
| `RETRIEVAL_HYBRID_FULLTEXT_WEIGHT` | `0.4` | hybrid fulltext weight |
| `RETRIEVAL_VECTOR_NUM_CANDIDATES` | `200` | vector oversampling |

> `hybrid` requires **MongoDB 8.0+** (uses native `$rankFusion`).

---

## Invoking the deployed MCP server

The service is private — all callers must present a Google-signed OIDC identity
token.

### gcloud + curl

```bash
# User account (gcloud auth login) — omit --audiences:
TOKEN=$(gcloud auth print-identity-token)
curl -H "Authorization: Bearer $TOKEN" \
     https://searchaas-agent-runtime-<hash>-uc.run.app/mcp

# Service account impersonation:
TOKEN=$(gcloud auth print-identity-token \
    --impersonate-service-account=searchaas-agent-runtime-sa@PROJECT.iam.gserviceaccount.com \
    --audiences="https://searchaas-agent-runtime-<hash>-uc.run.app")
curl -H "Authorization: Bearer $TOKEN" \
     https://searchaas-agent-runtime-<hash>-uc.run.app/mcp
```

### Python MCP client

```python
import asyncio
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVICE_URL = "https://searchaas-agent-runtime-<hash>-uc.run.app"
MCP_URL     = f"{SERVICE_URL}/mcp"

def get_token() -> str:
    request = Request()
    return id_token.fetch_id_token(request, SERVICE_URL)

async def main():
    headers = {"Authorization": f"Bearer {get_token()}"}
    async with streamablehttp_client(MCP_URL, headers, timeout=120,
                                     terminate_on_close=False) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print(await s.list_tools())
            print(await s.call_tool("hybrid_search",
                                    {"query": "best rated hotels", "top_k": 5}))

asyncio.run(main())
```

### Vertex AI Agent Builder

1. Open **Vertex AI Agent Builder** in the GCP console.
2. Create or edit an agent.
3. Add a **Tool** → type **MCP**.
4. Set the **Server URL** to `https://<service-url>/mcp`.
5. Set authentication to **Google OIDC (service-to-service)**.
6. The agent can now call `vector_search`, `hybrid_search`, `fulltext_search`,
   `graph_search`, `parent_doc_search`, `metadata_search`, `auto_search`.

### Google ADK (Python)

```python
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import MCPToolset, StreamableHTTPConnectionParams

MCP_URL = "https://searchaas-agent-runtime-<hash>-uc.run.app/mcp"

toolset = MCPToolset(
    connection_params=StreamableHTTPConnectionParams(url=MCP_URL),
)

agent = LlmAgent(
    model="gemini-2.0-flash",
    name="search_agent",
    instruction="Use the available search tools to answer user queries.",
    tools=[toolset],
)
```

---

## Tools exposed

`vector_search`, `fulltext_search`, `hybrid_search`, `graph_search`,
`parent_doc_search`, `metadata_search`, `auto_search`

(all from `searchaas/mcp_server/server.py`)

---

## Manage / tear down

```bash
PROJECT=my-gcp-project
REGION=us-central1

# Check status
gcloud run services describe searchaas-agent-runtime \
    --region $REGION --project $PROJECT

# View logs
gcloud logging read \
    "resource.type=cloud_run_revision AND resource.labels.service_name=searchaas-agent-runtime" \
    --project $PROJECT --limit 50

# Delete service
gcloud run services delete searchaas-agent-runtime \
    --region $REGION --project $PROJECT --quiet

# Delete Artifact Registry repo (also removes images)
gcloud artifacts repositories delete searchaas-agent \
    --location $REGION --project $PROJECT --quiet

# Delete service account
gcloud iam service-accounts delete \
    searchaas-agent-runtime-sa@${PROJECT}.iam.gserviceaccount.com \
    --project $PROJECT --quiet
```

---

## Notes / caveats

- **Private by default.** The service has `--ingress=internal-and-cloud-load-balancing`
  and `--no-allow-unauthenticated`. Direct browser access will return `403`.
  This is intentional — agent runtimes are called service-to-service only.
- **Cold starts.** `--min-instances=0` means the first request may take 5–15 s.
  Set `--min-instances=1` in `deploy.sh` to eliminate cold starts (adds cost).
- **Secrets.** Atlas URI and API keys are stored in Secret Manager and mounted
  at deploy time — they are never baked into the image.
- **Vertex AI SA.** The script tries to grant `roles/run.invoker` to the
  Vertex AI service account. If Vertex AI has not been used in the project yet,
  this SA may not exist; grant the binding manually after enabling the API.
