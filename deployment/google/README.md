# SearchaaS — Google Cloud Deployment

Two deployment targets are available under this directory:

| Target | Folder | Description |
|---|---|---|
| **Cloud Run** | [`cloud_run/`](cloud_run/README.md) | Standard serverless containers — default deployment |
| **Agent Runtime** | [`agent_runtime/`](agent_runtime/README.md) | Private Cloud Run service configured for Vertex AI agent connectivity (opt-in) |

---

## Which should I use?

```
Start here
    │
    ├─ I want to deploy the full application
    │  (React UI + REST API + MCP server)
    │                    │
    │          ┌─────────┴──────────┐
    │          │                    │
    │   Three services         One service
    │   (independent scale)   (simpler, single URL)
    │          │                    │
    │    cloud_run/            cloud_run/
    │    deploy.sh             deploy-combined.sh
    │
    └─ I want a private MCP endpoint
       for Vertex AI Agent Builder / ADK
                    │
              agent_runtime/
              deploy.sh  (opt-in)
```

---

## Quick start

### Default — Cloud Run (three services)

```bash
export ATLAS_URI='mongodb+srv://...'
export ATLAS_DB='your_database'

./deployment/google/cloud_run/deploy.sh \
    --project my-gcp-project \
    --region us-central1
```

### Default — Cloud Run (combined single service)

```bash
./deployment/google/cloud_run/deploy-combined.sh \
    --project my-gcp-project \
    --region us-central1
```

### Opt-in — Vertex AI Agent Runtime

```bash
export ATLAS_URI='mongodb+srv://...'
export ATLAS_DB='your_database'
export GOOGLE_API_KEY='your-gemini-key'   # if using Gemini as LLM

./deployment/google/agent_runtime/deploy.sh \
    --project my-gcp-project \
    --region us-central1 \
    --yes
```

---

## Prerequisites (all targets)

- [gcloud CLI](https://cloud.google.com/sdk/docs/install) authenticated (`gcloud auth login`)
- Docker with `buildx` support
- GCP project with billing enabled
- `.env` file at the repo root (copy from `.env.example` and fill in secrets)

---

## Directory layout

```
deployment/google/
├── README.md                   ← this file
│
├── cloud_run/                  ← Standard Cloud Run deployment
│   ├── README.md
│   ├── deploy.sh               Three-service deploy (API + MCP + Frontend)
│   ├── deploy-combined.sh      Single combined-image deploy
│   ├── Dockerfile              FastMCP server image
│   ├── Dockerfile.api          FastAPI REST server image
│   ├── Dockerfile.frontend     React SPA via nginx
│   ├── Dockerfile.combined     All-in-one combined image
│   ├── docker-entrypoint-combined.sh
│   ├── docker-entrypoint-frontend.sh
│   ├── nginx.conf              nginx config for frontend-only service
│   ├── nginx-combined.conf     nginx config for combined service
│   └── supervisord.conf        (reference — not used by default entrypoint)
│
└── agent_runtime/              ← Vertex AI Agent Runtime (opt-in)
    ├── README.md
    ├── Dockerfile              FastMCP image for agent runtime
    └── deploy.sh               Opt-in deploy with confirmation gate
```
