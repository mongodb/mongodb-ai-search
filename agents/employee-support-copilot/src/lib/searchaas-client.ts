/**
 * searchaas-client.ts — Thin typed wrapper around the SearchaaS REST API.
 *
 * Responsibilities:
 *   - Build the SearchaaS request payload from typed inputs.
 *   - Call POST /retrieve (or a specialised strategy endpoint).
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

const SEARCHAAS_BASE_URL =
  process.env.SEARCHAAS_BASE_URL ?? "http://localhost:8000";
const SEARCHAAS_API_KEY = process.env.SEARCHAAS_API_KEY ?? "";

/**
 * Call SearchaaS POST /retrieve with the given payload.
 * Throws a structured {@link SearchaaSError} on non-2xx responses.
 */
export async function callSearchaaS(
  payload: SearchaaSRequest,
  endpoint: string = "/retrieve"
): Promise<{ response: SearchaaSResponse; latencyMs: number }> {
  const url = `${SEARCHAAS_BASE_URL}${endpoint}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(SEARCHAAS_API_KEY ? { Authorization: `Bearer ${SEARCHAAS_API_KEY}` } : {}),
  };

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
      body: JSON.stringify(payload),
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

  const response = (await res.json()) as SearchaaSResponse;

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
