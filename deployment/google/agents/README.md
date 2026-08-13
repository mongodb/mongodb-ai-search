# agents/ — Cloud Run Deployment (Employee Support Copilot)

Deploys the `agents/employee-support-copilot` Next.js app (chat UI + BFF
`/api/chat`) as a single Cloud Run service, wired to the SearchaaS backend.

When the backend is a **Vertex AI Agent Engine (Reasoning Engine)** resource —
the Gemini agent platform's managed agent runtime — the BFF authenticates with
Google Application Default Credentials. On Cloud Run, ADC is the attached
service account; this script creates it (`employee-support-copilot-sa`) and
grants it `roles/aiplatform.user` so it can call `reasoningEngines:query`.
No API keys or tokens are baked into the image.

> The other entries under `agents/` (`pipeline/`, `scripts/`) are data-seeding
> utilities, not deployable services.

## Prerequisites

- gcloud CLI authenticated (`gcloud auth login` + `gcloud auth application-default login`)
- Docker with buildx
- The SearchaaS backend already deployed (Agent Engine via
  [`../agent_runtime/`](../agent_runtime/README.md), or Cloud Run via
  [`../cloud_run/`](../cloud_run/README.md))
- `agents/employee-support-copilot/.env.local` with `SEARCHAAS_BASE_URL`
  pointing at that backend (or pass `--searchaas-url`)

## Deploy

```bash
./deployment/google/agents/deploy.sh \
    --project my-gcp-project \
    --region us-central1
```

All flags are optional and default to the gcloud config / `.env.local` values:

| Flag | Default | Purpose |
|---|---|---|
| `--project` | gcloud config | GCP project ID |
| `--region` | `us-central1` | Cloud Run region |
| `--repo` | `searchaas` | Artifact Registry repo |
| `--service` | `employee-support-copilot` | Cloud Run service name |
| `--searchaas-url` | `SEARCHAAS_BASE_URL` from the app's `.env.local` | Backend the BFF calls |

The script ends with a smoke test: it POSTs a sample query to
`<url>/api/chat` and reports the resolved domain / strategy / citation count.

> **First deploy:** the project-level `roles/aiplatform.user` grant can take
> up to ~10 minutes to propagate to the Vertex AI data plane. If the smoke
> test reports HTTP 503 / "SearchaaS error 403" right after a first-time
> deploy, wait a few minutes and reload the app — no redeploy needed.

Environment notes:
- On machines where the gcloud user token is rejected by Google APIs
  (workforce identity federation), the script automatically falls back to
  application-default credentials for all gcloud calls and builds the image
  server-side with Cloud Build instead of local docker push (the AR docker
  credential helper only supports the gcloud user token).

## Files

| File | Description |
|---|---|
| `deploy.sh` | End-to-end deploy (APIs → registry → image → SA/IAM → service → smoke test) |
| `Dockerfile` | Multi-stage Next.js standalone image (build context: `agents/employee-support-copilot/`) |

## How it fits together

```
Browser ──► Cloud Run: employee-support-copilot (Next.js UI + /api/chat BFF)
                 │  Google ADC via attached service account (roles/aiplatform.user)
                 ▼
            Vertex AI Agent Engine :query  (SearchaaSAgent — Gemini agent platform)
                 │  per-request atlas/retrieval overrides
                 ▼
            MongoDB Atlas (ai_search.employee_support / ai_search.IT_helpdesk)
```

## Redeploy / reconfigure

```bash
# Same command is idempotent — rebuilds the image and updates the service.
./deployment/google/agents/deploy.sh

# Point at a different backend (e.g. the Cloud Run FastAPI service):
SEARCHAAS_BASE_URL=https://searchaas-api-<project-number>.us-central1.run.app \
  ./deployment/google/agents/deploy.sh
```
