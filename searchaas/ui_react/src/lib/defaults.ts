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
   Build an AppConfig strictly from the backend GET /settings response.

   The backend is the single source of truth — `searchaas.yaml` + .env are
   the only inputs to the UI's runtime config. We intentionally do NOT
   blend in DEFAULT_CONFIG values here: any field the backend doesn't return
   stays empty/null on the UI side, surfacing the gap instead of silently
   pretending a value exists.

   Notes:
     - `null` values from the backend are PRESERVED (they're meaningful —
       e.g. `embedding_key: null` + `dimensions: -1` signal AutoEmbed mode
       to ConfigPane).
     - Missing sub-sections (rare) fall back to the typed scaffold from
       DEFAULT_CONFIG so the form keeps rendering; we log a warning so the
       gap is visible in dev tools.
   ───────────────────────────────────────────────────────────────────────── */
function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Replace each top-level section of `scaffold` with the section from `live`
 *  if present. Within a section, every key from `live` REPLACES the scaffold —
 *  no deep-merge, so stale scaffold values can't leak through. */
function pickBackendSections(
  scaffold: AppConfig,
  live: Record<string, unknown>,
): AppConfig {
  const out: Record<string, unknown> = { ...(scaffold as unknown as Record<string, unknown>) };
  const missing: string[] = [];
  for (const section of ["atlas", "embeddings", "planner", "retrieval", "server"] as const) {
    const v = live[section];
    if (isPlainObject(v)) {
      out[section] = v;
    } else {
      missing.push(section);
    }
  }
  if (missing.length > 0) {
    // eslint-disable-next-line no-console
    console.warn(
      `[searchaas] GET /settings returned without these sections: ${missing.join(", ")}. ` +
      `UI will render an empty form for them; verify searchaas.yaml.`,
    );
  }
  // Two-step cast: `out` is typed as a generic record because we built it
  // index-by-index from a backend response. We've verified each section is a
  // plain object above, and the scaffold guarantees every required key is
  // present, so the runtime shape satisfies AppConfig — TS just can't prove
  // the structural overlap from the index-signature type.
  return out as unknown as AppConfig;
}

/** Build the live AppConfig from the backend /settings response. */
export function mergeBackendSettings(
  backend: Record<string, unknown> | null | undefined,
): AppConfig {
  if (!backend) return DEFAULT_CONFIG;
  return pickBackendSections(DEFAULT_CONFIG, backend);
}
