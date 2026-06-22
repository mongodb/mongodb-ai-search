import type { AppConfig } from "./types";

// Typed scaffold ONLY for the first paint, before GET /settings completes.
// The backend (searchaas.yaml + .env) is the sole source of truth — see
// `mergeBackendSettings` below, which IGNORES this scaffold once the live
// settings arrive. Do NOT add real defaults here: any value that lingers
// here can leak as a per-request override and silently override the YAML.
//
// We still need a fully-typed object because:
//   1. React's `useState<AppConfig>(...)` requires a non-null initial value
//      that satisfies the AppConfig type for the form-rendering paths in
//      ConfigPane to compile without nullable-access guards everywhere.
//   2. Until the first /settings response arrives, the UI is in a loading
//      state and searches are blocked, so these placeholder values are
//      never serialized into a request payload.
export const DEFAULT_CONFIG: AppConfig = {
  atlas: {
    uri: "${ATLAS_URI}",
    database: "${ATLAS_DB:-amazon}",
    collection: "products-updated",
    vector_index: "autoembed_index",
    search_index: "default",
    text_key: "text",
    // AutoEmbeddings: server manages embedding; these are null/-1 on disk.
    // The TS type narrows to non-null for form rendering — we use empty
    // sentinels here which ConfigPane's auto-embed banner replaces anyway.
    embedding_key: "",
    relevance_score_fn: "cosine",
    dimensions: -1,
  },
  embeddings: {
    provider: "auto",
    config: {
      model: "voyage-4",
    },
  },
  planner: {
    llm_provider: "gemini",
    config: {
      model: "gemini-2.5-flash",
      google_api_key: "${GOOGLE_API_KEY}",
      temperature: 0.1,
    },
    default_top_k: 20,
  },
  retrieval: {
    default_strategy: "hybrid",
    hybrid: { vector_weight: 0.6, fulltext_weight: 0.4 },
    vector: { num_candidates: 200 },
  },
  server: {
    host: "0.0.0.0",
    port: 8000,
    mcp_host: "0.0.0.0",
    mcp_port: 8001,
    mcp_transport: "streamable-http",
    log_level: "info",
  },
};

/* ─────────────────────────────────────────────────────────────────────────
   Merge the backend's GET /settings response into an AppConfig.

   Why we need this:
     - The backend returns the live `searchaas.yaml` (secrets redacted), but
       AutoEmbeddings mode sends `embedding_key: null`, `relevance_score_fn: null`,
       and `dimensions: -1` — fields the UI form still needs to render.
     - We start from DEFAULT_CONFIG (which has UI-friendly defaults) and
       overlay every non-null backend value on top. Null backend values are
       kept as-is from the backend (they're meaningful — they tell the UI
       to render the auto-embed banner instead of the embedding inputs).
   ───────────────────────────────────────────────────────────────────────── */
function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Deep-merge `patch` into `base`, returning a new object.
 *  - Plain objects are merged key-by-key.
 *  - Arrays / primitives / nulls in `patch` REPLACE the value in `base`.
 *  - Keys present in `patch` but `undefined` are ignored. */
function deepMerge<T>(base: T, patch: unknown): T {
  if (!isPlainObject(patch)) return base;
  if (!isPlainObject(base)) return patch as T;
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) continue;
    if (isPlainObject(v) && isPlainObject(out[k])) {
      out[k] = deepMerge(out[k], v);
    } else {
      out[k] = v;
    }
  }
  return out as T;
}

/** Merge what the backend GET /settings returned on top of DEFAULT_CONFIG. */
export function mergeBackendSettings(
  backend: Record<string, unknown> | null | undefined,
): AppConfig {
  if (!backend) return DEFAULT_CONFIG;
  return deepMerge(DEFAULT_CONFIG, backend);
}
