/**
 * searchaas-client.ts — Thin typed wrapper around the SearchaaS REST API.
 *
 * Two transports are supported, selected automatically from SEARCHAAS_BASE_URL:
 *   - Local/self-hosted FastAPI: POST <base>/retrieve, optional static bearer
 *     (SEARCHAAS_API_KEY).
 *   - Vertex AI Agent Engine (Reasoning Engine): POST <base>:query with the
 *     {"class_method","input"} envelope, authenticated with a Google OAuth2
 *     token minted from Application Default Credentials (google-auth-library).
 *
 * Responsibilities:
 *   - Build the SearchaaS request payload from typed inputs.
 *   - Surface structured errors.
 *   - Log every outbound payload + response timing.
 *
 * All retrieval in this application flows through this file.
 * Never call MongoDB Atlas directly from the frontend or BFF.
 */

import { CollectionConfig } from "./collections";

// ---------------------------------------------------------------------------
// Types mirroring the SearchaaS request/response shape
// ---------------------------------------------------------------------------

export interface SearchaaSAtlasOverrides {
  collection: string;
  vector_index: string;
  search_index: string;
  text_key: string;
  embedding_key: string | null;
  dimensions?: number;
}

export interface SearchaaSRetrievalOverrides {
  vector_weight?: number;
  fulltext_weight?: number;
  num_candidates?: number;
}

export interface SearchaaSRequest {
  query: string;
  top_k: number;
  filters?: Record<string, unknown>;
  atlas?: SearchaaSAtlasOverrides;
  retrieval?: SearchaaSRetrievalOverrides;
  summarize?: boolean;
  understand?: boolean;
}

export interface SearchaaSChunk {
  content: string;
  metadata: Record<string, unknown>;
  score?: number | null;
}

export interface SearchaaSTimings {
  mongo_ms?: number | null;
  planning_ms?: number | null;
  understanding_ms?: number | null;
  summarize_ms?: number | null;
  total_ms?: number | null;
}

export interface SearchaaSResponse {
  strategy: string;
  plan: Record<string, unknown>;
  results: SearchaaSChunk[];
  understood_query?: {
    raw: string;
    rewritten: string;
    intent?: string;
    entities?: string[];
    metadata_filters?: Record<string, unknown>;
  } | null;
  summary?: string | null;
  timings?: SearchaaSTimings | null;
}

export interface SearchaaSError {
  status: number;
  message: string;
  detail?: unknown;
}

// ---------------------------------------------------------------------------
// Payload builder
// ---------------------------------------------------------------------------

export type RetrievalBias = "auto" | "vector-heavy" | "fulltext-heavy";

export interface BuildPayloadOptions {
  query: string;
  collection: CollectionConfig;
  topK?: number;
  filters?: Record<string, unknown>;
  bias?: RetrievalBias;
  summarize?: boolean;
}

/**
 * Construct the SearchaaS request payload from application-level options.
 * This is the single place where collection config is translated into the
 * wire format — change the shape here and nothing else needs to change.
 */
export function buildPayload(opts: BuildPayloadOptions): SearchaaSRequest {
  const {
    query,
    collection,
    topK = 8,
    filters = {},
    bias = "auto",
    summarize = false,
  } = opts;

  // Per-collection vector search overrides — always passed so SearchaaS
  // queries the right collection with its own indexes and field names.
  const atlasOverrides: SearchaaSAtlasOverrides = {
    collection: collection.collection,
    vector_index: collection.vectorIndex,
    search_index: collection.searchIndex,
    text_key: collection.textKey,
    embedding_key: collection.embeddingKey,
    ...(collection.dimensions !== -1 ? { dimensions: collection.dimensions } : {}),
  };

  // Retrieval weights — nudge SearchaaS based on the query bias signal.
  let retrievalOverrides: SearchaaSRetrievalOverrides = {
    num_candidates: collection.numCandidates,
  };
  if (bias === "vector-heavy") {
    retrievalOverrides = {
      ...retrievalOverrides,
      vector_weight: 0.75,
      fulltext_weight: 0.25,
    };
  } else if (bias === "fulltext-heavy") {
    retrievalOverrides = {
      ...retrievalOverrides,
      vector_weight: 0.3,
      fulltext_weight: 0.7,
    };
  } else {
    // Use the collection's own default hybrid weights.
    retrievalOverrides = {
      ...retrievalOverrides,
      vector_weight: collection.hybridWeights.vectorWeight,
      fulltext_weight: collection.hybridWeights.fulltextWeight,
    };
  }

  return {
    query,
    top_k: topK,
    filters: Object.keys(filters).length ? filters : undefined,
    atlas: atlasOverrides,
    retrieval: retrievalOverrides,
    summarize,
    understand: false, // NLU handled in the BFF classifier; skip SearchaaS NLU
  };
}

// ---------------------------------------------------------------------------
// HTTP client
// ---------------------------------------------------------------------------

const SEARCHAAS_BASE_URL = (
  process.env.SEARCHAAS_BASE_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");
const SEARCHAAS_API_KEY = process.env.SEARCHAAS_API_KEY ?? "";

/**
 * Vertex AI Agent Engine (Reasoning Engine) endpoints look like:
 *   https://<region>-aiplatform.googleapis.com/v1/projects/<p>/locations/<r>/reasoningEngines/<id>
 *
 * They differ from the local FastAPI surface in three ways:
 *   1. Auth   — a Google OAuth2 bearer token is required (minted from
 *               Application Default Credentials; never a static key).
 *   2. Wire   — custom methods are invoked via POST <base>:query with body
 *               {"class_method": "query", "input": {...}}.
 *   3. Result — the response wraps the method return value in {"output": {...}}.
 */
const IS_AGENT_ENGINE =
  /aiplatform\.googleapis\.com\/v\d+\/projects\/[^/]+\/locations\/[^/]+\/reasoningEngines\/[^/]+$/.test(
    SEARCHAAS_BASE_URL
  );

// Cached GoogleAuth instance — getAccessToken() caches and auto-refreshes the
// short-lived ADC access token, so no manual token management is needed.
let _googleAuth: import("google-auth-library").GoogleAuth | null = null;

async function getGoogleAccessToken(): Promise<string> {
  if (!_googleAuth) {
    // Dynamic import: keeps google-auth-library out of any client bundle and
    // only loads it when an Agent Engine call is actually made.
    const { GoogleAuth } = await import("google-auth-library");
    _googleAuth = new GoogleAuth({
      scopes: "https://www.googleapis.com/auth/cloud-platform",
    });
  }
  const token = await _googleAuth.getAccessToken();
  if (!token) {
    throw {
      status: 0,
      message:
        "No Google credentials available for the Agent Engine call. " +
        "Run `gcloud auth application-default login` locally, or attach a " +
        "service account to the hosting runtime.",
    } as SearchaaSError;
  }
  return token;
}

/**
 * Call SearchaaS with the given payload.
 * Local FastAPI mode:  POST <base>/retrieve          (body = payload as-is)
 * Agent Engine mode:   POST <base>:query             (body = query envelope)
 * Throws a structured {@link SearchaaSError} on non-2xx responses.
 */
export async function callSearchaaS(
  payload: SearchaaSRequest,
  endpoint: string = "/retrieve"
): Promise<{ response: SearchaaSResponse; latencyMs: number }> {
  const url = IS_AGENT_ENGINE
    ? `${SEARCHAAS_BASE_URL}:query`
    : `${SEARCHAAS_BASE_URL}${endpoint}`;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  let body: string;
  if (IS_AGENT_ENGINE) {
    headers.Authorization = `Bearer ${await getGoogleAccessToken()}`;
    body = JSON.stringify({
      class_method: "query",
      input: {
        input: payload.query,
        top_k: payload.top_k,
        ...(payload.filters ? { filters: payload.filters } : {}),
        ...(payload.atlas ? { atlas: payload.atlas } : {}),
        ...(payload.retrieval ? { retrieval: payload.retrieval } : {}),
      },
    });
  } else {
    if (SEARCHAAS_API_KEY) {
      headers.Authorization = `Bearer ${SEARCHAAS_API_KEY}`;
    }
    body = JSON.stringify(payload);
  }

  // --- Structured request log (server-side only) ---
  console.log("[searchaas-client] →", JSON.stringify({
    url,
    collection: payload.atlas?.collection,
    query: payload.query,
    top_k: payload.top_k,
    bias: payload.retrieval,
  }));

  const t0 = Date.now();
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers,
      body,
      // Abort after 30 s so a slow Atlas aggregation doesn't hang the BFF.
      signal: AbortSignal.timeout(30_000),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw { status: 0, message: `SearchaaS unreachable: ${msg}` } as SearchaaSError;
  }
  const latencyMs = Date.now() - t0;

  if (!res.ok) {
    let detail: unknown;
    try { detail = await res.json(); } catch { detail = await res.text(); }
    const err: SearchaaSError = { status: res.status, message: `SearchaaS error ${res.status}`, detail };
    console.error("[searchaas-client] ✗", err);
    throw err;
  }

  const json = await res.json();
  // Agent Engine wraps the method's return value: {"output": {...}}.
  const response = (
    IS_AGENT_ENGINE ? (json as { output: unknown }).output : json
  ) as SearchaaSResponse;

  // --- Structured response log ---
  console.log("[searchaas-client] ✓", JSON.stringify({
    collection: payload.atlas?.collection,
    strategy: response.strategy,
    result_count: response.results?.length ?? 0,
    latency_ms: latencyMs,
    mongo_ms: response.timings?.mongo_ms,
  }));

  return { response, latencyMs };
}

/**
 * Call SearchaaS for two collections in parallel and return both results.
 * Used by the router when classification confidence is low.
 */
export async function callBothCollections(
  query: string,
  collections: [CollectionConfig, CollectionConfig],
  topK: number = 5
): Promise<Array<{ response: SearchaaSResponse; latencyMs: number; collection: CollectionConfig }>> {
  const results = await Promise.allSettled(
    collections.map(async (col) => {
      const payload = buildPayload({ query, collection: col, topK, bias: "auto" });
      const { response, latencyMs } = await callSearchaaS(payload);
      return { response, latencyMs, collection: col };
    })
  );

  return results
    .filter((r): r is PromiseFulfilledResult<{ response: SearchaaSResponse; latencyMs: number; collection: CollectionConfig }> => r.status === "fulfilled")
    .map((r) => r.value);
}
