# SearchaaS — Vertex AI Agent Engine (Reasoning Engine) Deployment

Deploys SearchaaS to the **Google Cloud Gemini agent platform's managed agent
runtime — Vertex AI Agent Engine** (formerly "Reasoning Engine"). This is the
managed, serverless agent host: **no Cloud Run service, no locally-built
container image**. The Agent Engine service builds and runs the agent
container server-side from the pickled agent object, the `searchaas` package,
and the pip requirements uploaded to a staging bucket.

Docs:
- <https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview>
- <https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/develop/custom>

> The standard containerized deployment (React UI + REST API + MCP server)
> remains available under [`../cloud_run/`](../cloud_run/README.md).

---

## Agent Engine contract this deployment satisfies

| Requirement | How it's met |
|---|---|
| Queryable agent object | `SearchaaSAgent` in `deploy_agent_engine.py` implements `query()` + `stream_query()` (custom reasoning-engine template) |
| `set_up()` hook | Builds the SearchaaS `Container` remotely after unpickling (Atlas client, embedder, planner LLM, retrievers) |
| Code packaged | `extra_packages=["searchaas"]` — the repo package is tarred, uploaded to the staging bucket, and importable in the remote runtime |
| Dependencies | repo-root `requirements.txt` (minus `pytest`) is installed server-side |
| Config | Plain env vars (`ATLAS_DB`, `EMBEDDINGS_PROVIDER`, …) via `env_vars` |
| Secrets | `ATLAS_URI`, API keys via **Secret Manager `SecretRef`** — never baked into the agent |
| Staging | `gs://<project>-agent-engine-staging` bucket, created by `deploy.sh` |
| IAM | `deploy.sh` grants the Agent Engine service agent `secretmanager.secretAccessor` + `storage.objectViewer` on the staging bucket |

The `searchaas` application code is **not modified** — the agent wrapper lives
entirely in this directory and reuses the identical pipeline as the FastAPI
`/retrieve` endpoint and the MCP `auto_search` tool
(understand → plan → retrieve → summarize).

---

## Files

| File | Purpose |
|---|---|
| `deploy.sh` | End-to-end deploy: APIs → staging bucket → secrets → IAM → Agent Engine |
| `deploy_agent_engine.py` | `SearchaaSAgent` wrapper class + `AgentEngine.create()/update()` driver |
| `requirements-deploy.txt` | Deploy-time deps installed into the local venv (Vertex AI SDK + cloudpickle) |

---

## Run it

```bash
export ATLAS_URI='mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true'
export ATLAS_DB='your_database_name'

# Interactive confirmation (type 'deploy-agent-engine' when prompted):
./deployment/google/agent_runtime/deploy.sh

# Or skip the prompt in CI:
YES=yes ./deployment/google/agent_runtime/deploy.sh --yes

# With explicit project/region:
./deployment/google/agent_runtime/deploy.sh \
    --project my-gcp-project \
    --region us-central1 \
    --yes

# Update an existing engine in place (no new resource created):
AGENT_ENGINE_ID=<engine-id> ./deployment/google/agent_runtime/deploy.sh --yes
```

The server-side build takes **~5–15 minutes**. On success the script prints:

```
AGENT_ENGINE_RESOURCE=projects/<project>/locations/us-central1/reasoningEngines/<id>
```

---

## Query the deployed agent

```python
import vertexai
from vertexai import agent_engines

vertexai.init(project="my-gcp-project", location="us-central1")
agent = agent_engines.get("projects/.../locations/us-central1/reasoningEngines/<id>")

# Auto mode — the planner picks the retrieval strategy:
resp = agent.query(input="best rated hotels", top_k=5)
print(resp["summary"], resp["results"])

# Fixed strategy — vector | fulltext | hybrid | graph | parent_doc | metadata:
resp = agent.query(input="best rated hotels", top_k=5, strategy="hybrid")

# Optional metadata pre-filters:
resp = agent.query(input="hotels in Paris", top_k=5,
                   filters={"imdb.rating": 8})

# Optional per-request overrides (same semantics as the REST /retrieve body):
# target another collection in the same database + tune retrieval weights.
resp = agent.query(
    input="How do I connect to the VPN?", top_k=5,
    atlas={
        "collection": "IT_helpdesk",
        "vector_index": "it_helpdesk_vector_index",
        "search_index": "it_helpdesk_search_index",
        "text_key": "text",
        "embedding_key": None,          # autoEmbed index — no client-side key
    },
    retrieval={"vector_weight": 0.55, "fulltext_weight": 0.45,
               "num_candidates": 150},
)

# Streaming (progress events + final result):
for event in agent.stream_query(input="best rated hotels", top_k=5):
    print(event)
```

The response dict matches the REST `/retrieve` payload: `strategy`, `summary`,
`results`, `understood_query`, `plan`, `timings`.

### From the console / Gemini agent platform

Open **Vertex AI → Agent Builder → Agent Engines**
(<https://console.cloud.google.com/vertex-ai/agents/agent-engines>) — the
`searchaas-agent` engine appears there once deployed, with a built-in
playground for `query` / `streamQuery` calls, and can be registered as a tool
for Gemini Enterprise agents.

---

## Configuration

All configuration in `searchaas/config/searchaas.yaml` uses `${VAR:-default}`
syntax, so the deployed agent is configured purely with **environment
variables** — no redeploy of code needed beyond re-running `deploy.sh`.
Export variables before deploying and they are forwarded:

```bash
export EMBEDDINGS_PROVIDER=auto
export ATLAS_VECTOR_INDEX=autoembed_index
export ATLAS_DIMENSIONS=-1
./deployment/google/agent_runtime/deploy.sh --yes
```

### Atlas connection & index

| Env var | Default | Purpose |
|---|---|---|
| `ATLAS_URI` | *(required)* | `mongodb+srv://...` connection string (Secret Manager) |
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
| `RETRIEVAL_DEFAULT_STRATEGY` | `hybrid` | `vector`, `fulltext`, `hybrid`, `graph`, `parent_doc`, `metadata` |
| `RETRIEVAL_HYBRID_VECTOR_WEIGHT` | `0.6` | hybrid vector weight |
| `RETRIEVAL_HYBRID_FULLTEXT_WEIGHT` | `0.4` | hybrid fulltext weight |
| `RETRIEVAL_VECTOR_NUM_CANDIDATES` | `200` | vector oversampling |

> `hybrid` requires **MongoDB 8.0+** (uses native `$rankFusion`).

---

## Manage / tear down

```bash
PROJECT=my-gcp-project
REGION=us-central1

# List engines
venv/bin/python - <<'PY'
import vertexai
from vertexai import agent_engines
vertexai.init(project="my-gcp-project", location="us-central1")
for e in agent_engines.list():
    print(e.resource_name, e.display_name)
PY

# Delete the engine
venv/bin/python - <<'PY'
import vertexai
from vertexai import agent_engines
vertexai.init(project="my-gcp-project", location="us-central1")
agent_engines.delete("projects/my-gcp-project/locations/us-central1/reasoningEngines/<id>")
PY

# Delete the staging bucket (also removes staged artifacts)
gcloud storage buckets delete gs://${PROJECT}-agent-engine-staging \
    --project $PROJECT --quiet
```

---

## Notes / caveats

- **Server-side build.** `AgentEngine.create()` uploads the pickled agent,
  `searchaas` package, and requirements to the staging bucket; the managed
  service then pip-installs requirements and boots the runtime. Expect
  ~5–15 minutes per deploy.
- **Python version.** The remote runtime Python matches the local interpreter
  (3.10–3.13 supported). Use the repo `./venv` (3.13) — it is the default.
- **Cold starts.** The first `query` on a fresh engine builds the SearchaaS
  container (Atlas connection, embedder, planner) — allow 30–60 s. Subsequent
  calls are warm.
- **Secrets.** Atlas URI and API keys are referenced via Secret Manager
  `SecretRef` env vars and injected at runtime — they are never baked into
  the pickled agent or printed by the deploy script.
- **Updates.** Pass `AGENT_ENGINE_ID=<id>` (or `--engine-id`) to update the
  same engine in place; without it, each deploy creates a new engine resource.
