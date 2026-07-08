# SearchaaS — Google Cloud Run Deployment

Deploy SearchaaS to Google Cloud Run (managed, serverless containers).

## Two deployment modes

| Mode | Script | Services | Best for |
|------|--------|----------|----------|
| **Three-service** | `deploy.sh` | API + MCP + Frontend as separate Cloud Run services | Independent scaling, cleaner separation |
| **Combined** | `deploy-combined.sh` | All three in one Cloud Run service behind nginx | Simpler setup, single URL, lower cost |

---

## Prerequisites

- [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated (`gcloud auth login`)
- Docker installed and running
- A populated `.env` file at the repo root (`cp .env.example .env`)
- GCP project with billing enabled

---

## Three-service deployment

Deploys three independent Cloud Run services:

- `searchaas-api` — FastAPI REST on port 8000
- `searchaas-mcp` — FastMCP server on port 8001 (`/mcp` endpoint)
- `searchaas-frontend` — React SPA via nginx on port 8080

```bash
# From the repo root:
./deployment/google/cloud_run/deploy.sh

# With explicit flags:
./deployment/google/cloud_run/deploy.sh \
    --project my-gcp-project \
    --region us-central1 \
    --repo searchaas
```

The script:
1. Enables `run.googleapis.com`, `artifactregistry.googleapis.com`, `secretmanager.googleapis.com`
2. Creates an Artifact Registry Docker repository
3. Pushes every `.env` key to Secret Manager
4. Builds and pushes three images (`linux/amd64`)
5. Deploys each service with Secret Manager bindings
6. Patches CORS on the API and MCP services with the live frontend URL
7. Verifies the backend is serving the correct Atlas collection/database

---

## Combined single-service deployment

Builds one image (nginx + FastAPI + FastMCP) and deploys it as a single Cloud Run service.

```bash
./deployment/google/cloud_run/deploy-combined.sh

# With flags:
./deployment/google/cloud_run/deploy-combined.sh \
    --project my-gcp-project \
    --region us-central1
```

Endpoint map (all on the same Cloud Run URL):

| Path | Backend |
|------|---------|
| `/` | React SPA |
| `/retrieve*`, `/health`, `/settings`, `/docs` | FastAPI (127.0.0.1:8000) |
| `/mcp` | FastMCP (127.0.0.1:8001) |

---

## Configuration

All configuration is driven from the repo root `.env` file. Every `KEY=VALUE`
pair is stored in Secret Manager and injected into the container at deploy time.
The `searchaas/config/searchaas.yaml` file uses `${KEY:-default}` syntax so
no image rebuild is needed to change Atlas indexes, embedding provider, or LLM.

Key environment variables:

| Variable | Purpose |
|---|---|
| `ATLAS_URI` | MongoDB Atlas connection string (`mongodb+srv://...`) |
| `ATLAS_DB` | Database name |
| `ATLAS_COLLECTION` | Collection to search |
| `GOOGLE_API_KEY` | Gemini API key (if using Gemini as LLM) |
| `VOYAGE_API_KEY` | Voyage AI key (if using VoyageAI embeddings) |
| `EMBEDDINGS_PROVIDER` | `voyageai`, `auto`, `gemini`, `openai`, etc. |
| `PLANNER_LLM_PROVIDER` | `gemini`, `openai`, `anthropic`, `bedrock`, etc. |

See `searchaas/config/searchaas.yaml` for the full list with defaults.

---

## Dockerfiles

| File | Purpose |
|------|---------|
| `Dockerfile.api` | FastAPI REST server |
| `Dockerfile` | FastMCP server |
| `Dockerfile.frontend` | React SPA via nginx |
| `Dockerfile.combined` | All-in-one combined image |

---

## Tear down

```bash
PROJECT=my-gcp-project
REGION=us-central1

# Three-service mode
gcloud run services delete searchaas-api      --region=$REGION --project=$PROJECT --quiet
gcloud run services delete searchaas-mcp      --region=$REGION --project=$PROJECT --quiet
gcloud run services delete searchaas-frontend --region=$REGION --project=$PROJECT --quiet

# Combined mode
gcloud run services delete searchaas --region=$REGION --project=$PROJECT --quiet

# Remove Artifact Registry repo (also deletes all images)
gcloud artifacts repositories delete searchaas --location=$REGION --project=$PROJECT --quiet
```

---

> For deploying the MCP server as a managed agent backend integrated with
> Vertex AI, see [`../agent_runtime/`](../agent_runtime/README.md).
