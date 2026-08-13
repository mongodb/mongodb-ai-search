# Deploy the Employee Support Copilot on AWS Amplify Hosting

Deploys [`agents/employee-support-copilot`](../../../agents/employee-support-copilot)
— a Next.js 14 (App Router) chat UI whose BFF calls SearchaaS — to Amplify
Hosting with server-side rendering.

| | |
| --- | --- |
| Platform | `WEB_COMPUTE` (Amplify managed SSR compute) |
| Deployment method | **Continuous deployment from Git** (required — see below) |
| App root in repo | `agents/employee-support-copilot` (monorepo) |
| Runtime env var | `SEARCHAAS_BASE_URL` → the SearchaaS FastAPI endpoint on ECS |

## Current deployment

| | |
| --- | --- |
| App ID | `d37bm88drk3hu9` (us-east-1) |
| URL | https://main.d37bm88drk3hu9.amplifyapp.com |
| SearchaaS backend | `https://se-10bba725de864de489a8d0390d2c325a.ecs.us-east-1.on.aws` |

Verified working: `/chat` returns 200, and `POST /api/chat` classifies and
routes correctly (`"VPN is not connecting"` → IT Helpdesk / hybrid;
`"leave policy for new joiners"` → Employee Support / fulltext) with no errors.

⚠️ **Answers currently come back empty** (`citations: 0`). That is a data gap,
not a deployment fault — see [Retrieval returns no results](#retrieval-returns-no-results).

## Files here

| File | Purpose |
| --- | --- |
| `deploy.sh` | End-to-end: create/update the Amplify app + branch, connect the repo, set env vars, run a build, verify the SSR route |
| `amplify.yml` | Monorepo build spec (`applications:` / `appRoot`). Applied via `--build-spec`, so it does **not** need to be copied to the repo root |

---

## Why this must be Git-connected

The app is not a static site. [`src/app/api/chat/route.ts`](../../../agents/employee-support-copilot/src/app/api/chat/route.ts)
is a server route (`export const runtime = "nodejs"`) that classifies the query
and calls SearchaaS server-side, so it needs a Node runtime at request time.
`next build` confirms it:

```
Route (app)                    Size     First Load JS
├ ƒ /api/chat                  0 B      0 B          ← ƒ = server-rendered on demand
└ ○ /chat                      4.34 kB  91.6 kB
```

AWS is explicit about the consequence:

> Amplify Hosting does not support manual deploys for server-side rendered (SSR) apps.
> — [Deploying an application to Amplify without a Git repository](https://docs.aws.amazon.com/amplify/latest/userguide/manual-deploys.html)

**This failure mode is silent, so it is worth naming.** If you deploy a .zip
through `create-deployment` / `start-deployment`, the job reports `SUCCEED` and
the app still shows `platform: WEB_COMPUTE` — but Amplify serves the bundle
statically out of S3:

```
$ curl -sS -o /dev/null -D - https://main.<appid>.amplifyapp.com/
HTTP/2 404
server: AmazonS3          ← served by S3, never reached compute

$ curl -sS -o /dev/null -D - -X POST .../api/chat
HTTP/2 301
location: /api/chat/      ← S3 directory redirect, not the Next.js route
```

The [Amplify Hosting deployment specification](https://docs.aws.amazon.com/amplify/latest/userguide/ssr-deployment-specification.html)
(`deploy-manifest.json` + `compute/` + `static/`) does **not** rescue this: that
spec is read by Amplify's *build pipeline*, not by the manual-deploy API. There
is no supported zip path to SSR compute.

---

## Prerequisites

1. **AWS CLI v2**, authenticated:
   ```bash
   aws sso login --profile anuj-ps
   export AWS_PROFILE=anuj-ps AWS_REGION=us-east-1
   ```
2. **A GitHub token** with `repo` and `admin:repo_hook` scope. Amplify needs
   `admin:repo_hook` to install the webhook that triggers builds on push.
   ```bash
   export GITHUB_ACCESS_TOKEN='ghp_...'
   ```
   > On a repo owned by an organisation with SSO enforced, the token must also
   > be SSO-authorised for that org, and you need admin rights on the repo to
   > create the webhook. If you cannot meet that, use the console flow below —
   > it installs the Amplify GitHub App instead of using a token.
3. **A running SearchaaS FastAPI service.** Get its URL from the ECS deployment:
   ```bash
   aws ecs describe-express-gateway-service \
     --service-arn arn:aws:ecs:us-east-1:<ACCOUNT_ID>:service/default/searchaas-fastapi \
     --query 'service.activeConfigurations[0].ingressPaths[0].endpoint' --output text
   ```
   See [`../ecs/README.md`](../ecs/README.md).

---

## Option A — scripted (recommended)

```bash
export AWS_PROFILE=anuj-ps
export GITHUB_ACCESS_TOKEN='ghp_...'
export SEARCHAAS_BASE_URL='https://se-xxxxxxxx.ecs.us-east-1.on.aws'

./deployment/aws/amplify/deploy.sh
```

`SEARCHAAS_BASE_URL` falls back to the value in the app's `.env.local` if unset.

What it does (idempotent — safe to re-run):

1. Creates the Amplify app with `--platform WEB_COMPUTE`, connected to the repo
   via `--access-token`, with `amplify.yml` applied as the build spec.
2. Sets app-level environment variables — `SEARCHAAS_BASE_URL`,
   `SEARCHAAS_API_KEY`, and `AMPLIFY_MONOREPO_APP_ROOT`.
3. Creates the `main` branch with `--framework 'Next.js - SSR'` and auto-build on.
4. Runs `start-job --job-type RELEASE` and polls until it settles, dumping the
   per-step log URLs if it fails.
5. Prints the URL and curls `/api/chat` to confirm compute is actually serving.

## Option B — AWS console

Use this when token-based repo access is not available.

1. Amplify console → **Create new app** → **GitHub** → authorise the **AWS
   Amplify GitHub App** → pick `mongodb/mongodb-ai-search`, branch `main`.
2. On **App settings**, tick **My app is a monorepo** and enter
   `agents/employee-support-copilot` as the root.
3. Confirm Amplify detects **Next.js - SSR**. If it shows a static framework,
   the platform is wrong and `/api/chat` will 404 once deployed.
4. Expand **Advanced settings** → **Environment variables** and add:

   | Key | Value |
   | --- | --- |
   | `SEARCHAAS_BASE_URL` | `https://se-xxxxxxxx.ecs.us-east-1.on.aws` |
   | `SEARCHAAS_API_KEY` | *(blank unless SearchaaS is behind bearer auth)* |
   | `AMPLIFY_MONOREPO_APP_ROOT` | `agents/employee-support-copilot` |

5. Replace the build spec with the contents of [`amplify.yml`](./amplify.yml).
6. **Save and deploy.**

---

## Verify

Compute is serving correctly when the API route returns JSON:

```bash
URL=https://main.<appid>.amplifyapp.com

curl -sS "$URL/chat" -o /dev/null -w '%{http_code}\n'          # 200

curl -sS -X POST "$URL/api/chat" \
  -H 'content-type: application/json' \
  -d '{"query":"VPN is not connecting on my Mac"}' | jq '.routing, .citations | length'
```

A `301` to `/api/chat/` with `server: AmazonS3` means the app is being served
statically — re-check the platform (`aws amplify get-app ... --query 'app.platform'`
must be `WEB_COMPUTE`) and that the branch framework is `Next.js - SSR`.

---

## Configuration notes

### `next.config.js` must not set `output`

Amplify's Next.js adapter builds the default `.next` output and provisions
compute from it. Setting `output: "standalone"` or `output: "export"` changes
that layout and breaks the adapter. The file carries a comment to this effect.

### CORS is not involved

The browser only ever calls the Amplify origin. `/api/chat` runs server-side and
calls SearchaaS from Amplify's compute, so SearchaaS never sees a browser
`Origin` header for these requests and `SEARCHAAS_CORS_ORIGINS` does not need
the Amplify domain. (It would only matter if a client component started calling
SearchaaS directly.)

### The collection registry is build-time

`src/lib/collections.ts` hardcodes the Atlas collections (`IT_helpdesk`,
`employee_support`) and their index names, and the BFF passes them to SearchaaS
as per-request `atlas` overrides. Changing a collection or index name means
editing that file and redeploying — it is not an environment variable.

Note the BFF overrides `collection`, `vector_index`, `search_index`, `text_key`,
and `embedding_key` — but **not `database`**. Queries therefore land in whatever
database the SearchaaS service itself is configured with (`ATLAS_DB`). Both must
line up.

### Retrieval returns no results

If the UI answers "I couldn't find a specific answer…" with zero citations while
`/api/chat` returns HTTP 200 and `error: null`, the deployment is healthy and the
problem is in Atlas. As of the last check against `ai-search`:

```
IT_helpdesk       docs=0   search_indexes=[('it_helpdesk_vector_index', 'vectorSearch', 'READY')]
employee_support  docs=0   search_indexes=[('employee_support_vector_index', 'vectorSearch', 'READY')]
```

Two gaps:

1. **Both collections are empty.** Seed them — see
   [`agents/pipeline/seed_helpdesk_data.py`](../../../agents/pipeline/seed_helpdesk_data.py).
2. **The Lucene search indexes are missing.** `collections.ts` names
   `it_helpdesk_search_index` and `employee_support_search_index`, but only the
   vector indexes exist. The default strategy is `hybrid` (`$rankFusion` over
   vector + fulltext), so the fulltext half has nothing to query. Create those
   Atlas Search indexes, or point `searchIndex` at one that exists.

Confirm the backend independently of the UI:

```bash
curl -sS -X POST "$SEARCHAAS_BASE_URL/retrieve" -H 'content-type: application/json' -d '{
  "query":"VPN is not connecting","top_k":8,
  "atlas":{"collection":"IT_helpdesk","vector_index":"it_helpdesk_vector_index",
           "search_index":"it_helpdesk_search_index","text_key":"text","embedding_key":"embedding"},
  "summarize":false,"understand":false}' | jq '.results | length'
```

### Redeploys

With auto-build enabled, any push to `main` rebuilds. To force one:

```bash
aws amplify start-job --app-id <appid> --branch-name main --job-type RELEASE
```

### Teardown

```bash
aws amplify delete-app --app-id <appid> --region us-east-1
```

This removes the app, its branches, the compute resources, and the webhook.
