# SearchaaS — Google Cloud Deployment

Three deployment targets are available under this directory:

| Target | Folder | Description |
|---|---|---|
| **Cloud Run** | [`cloud_run/`](cloud_run/README.md) | Standard serverless containers — default deployment |
| **Agent Engine** | [`agent_runtime/`](agent_runtime/README.md) | Vertex AI Agent Engine (Reasoning Engine) — the Gemini agent platform's managed agent runtime |
| **Agents (apps)** | [`agents/`](agents/README.md) | Apps from `agents/` (e.g. Employee Support Copilot) as Cloud Run services wired to either backend |

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
    ├─ I want a managed agent on the Gemini
    │  agent platform (Vertex AI Agent Engine)
    │                     │
    │               agent_runtime/
    │               deploy.sh
    │
    └─ I want to deploy an app from agents/
       (e.g. the Employee Support Copilot UI)
                     │
                agents/
                deploy.sh
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

### Vertex AI Agent Engine (Reasoning Engine)

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
└── agent_runtime/              ← Vertex AI Agent Engine (Reasoning Engine)
    ├── README.md
    ├── deploy.sh               End-to-end Agent Engine deploy (confirmation gate)
    ├── deploy_agent_engine.py  SearchaaSAgent wrapper + AgentEngine create/update
    └── requirements-deploy.txt Deploy-time deps (Vertex AI SDK + cloudpickle)

└── agents/                     ← Apps from agents/ (Employee Support Copilot)
    ├── README.md
    ├── deploy.sh               Cloud Run deploy + Agent Engine wiring + smoke test
    └── Dockerfile              Next.js standalone image (context: agents/employee-support-copilot)
```
