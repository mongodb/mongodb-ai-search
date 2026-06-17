# SearchaaS — React UI

A LeafyGreen-styled React + Vite + TypeScript front-end for the SearchaaS
Phase 1 backends (FastAPI REST + FastMCP Streamable HTTP).

## Run

```bash
npm install
npm run dev          # → http://localhost:5173
```

Backends expected (run from the repo root):

```bash
uvicorn searchaas.api.app:app --host 0.0.0.0 --port 8000
python -m searchaas.mcp_server.server
```

Both backends enable CORS for any `http://localhost:*` origin, so the dev
server can talk to them directly.

## Layout

| Region                  | What it does                                                       |
| ----------------------- | ------------------------------------------------------------------ |
| **Side pane** (left)    | Live YAML config editor with collapse / expand. Required `atlas.*` fields are highlighted per strategy. Download produces `searchaas.yaml`. |
| **Header** (top)        | Branded hero with active backend.                                  |
| **Query panel** (right) | Backend toggle, strategy + top_k, query box, filters, run button.  |
| **Intent panel**        | Shown when auto mode returns an `understood_query` — intent, rewritten query, entities, inferred filters, and the planner-chosen strategy compared to the intent's typical mapping. |
| **Summary panel**       | LLM-generated summary of the top results (falls back to the first result snippet if absent). |
| **Results**             | Result tiles with optional `show full` / `show metadata`.          |
| **Planner output**      | Collapsible JSON.                                                  |
| **Pipeline**            | Reconstructed MongoDB aggregation pipeline driven by the current YAML, copy/download-friendly. |

## Stack

* **React 18** (LeafyGreen tracks React 18)
* **Vite + TypeScript**
* **LeafyGreen UI** (`@leafygreen-ui/*`) — see [mongodb/leafygreen-ui](https://github.com/mongodb/leafygreen-ui) and [mongodb.design](https://www.mongodb.design)
* **js-yaml** for client-side YAML serialization

## Notes

* The MCP client handles the full Streamable HTTP handshake (`initialize` →
  `notifications/initialized` → `tools/call`) and parses SSE responses.
  Session ids are cached per endpoint and auto-recover on stale-session errors.
* The aggregation pipeline shown is a faithful reconstruction of what
  `langchain-mongodb` issues against Atlas (so it can be copied into
  Compass / `mongosh`); it's built client-side from the live YAML config.
