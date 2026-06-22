export type Strategy =
  | "auto" | "vector" | "fulltext" | "hybrid" | "graph" | "parent-doc";

export const STRATEGIES: Strategy[] = [
  "auto", "vector", "fulltext", "hybrid", "graph", "parent-doc",
];

export const AUTO_STRATEGY_CHOICES: Strategy[] = ["hybrid", "vector", "fulltext"];

export const INTENT_STRATEGY_HINT: Record<string, Strategy> = {
  exact_lookup: "fulltext",
  policy_lookup: "fulltext",
  semantic_search: "vector",
  summarization: "vector",
  analytical: "hybrid",
  troubleshooting: "hybrid",
};

export const STRATEGY_MAP: Record<Strategy, { restPath: string; mcpTool: string }> = {
  auto:         { restPath: "/retrieve",            mcpTool: "auto_search" },
  vector:       { restPath: "/retrieve/vector",     mcpTool: "vector_search" },
  fulltext:     { restPath: "/retrieve/fulltext",   mcpTool: "fulltext_search" },
  hybrid:       { restPath: "/retrieve/hybrid",     mcpTool: "hybrid_search" },
  graph:        { restPath: "/retrieve/graph",      mcpTool: "graph_search" },
  "parent-doc": { restPath: "/retrieve/parent-doc", mcpTool: "parent_doc_search" },
};

export const STRATEGY_COLOR: Record<string, string> = {
  auto:       "accent",
  vector:     "purple",
  fulltext:   "blue",
  hybrid:     "green",
  graph:      "amber",
  "parent-doc": "gray",
};

export const REQUIRED_ATLAS_FIELDS: Record<Strategy, string[]> = {
  auto:         ["uri","database","collection","vector_index","search_index","embedding_key","text_key","dimensions"],
  vector:       ["uri","database","collection","vector_index","embedding_key","text_key","dimensions"],
  fulltext:     ["uri","database","collection","search_index","text_key"],
  hybrid:       ["uri","database","collection","vector_index","search_index","embedding_key","text_key","dimensions"],
  graph:        ["uri","database","collection","text_key","embedding_key"],
  "parent-doc": ["uri","database","collection","vector_index","embedding_key","text_key","dimensions"],
};

export interface AtlasConfig {
  uri: string; database: string; collection: string;
  vector_index: string; search_index: string;
  text_key: string; embedding_key: string;
  relevance_score_fn: "cosine" | "euclidean" | "dotProduct";
  dimensions: number;
}

export interface AppConfig {
  atlas: AtlasConfig;
  embeddings: { provider: string; config: Record<string, unknown> };
  planner: { llm_provider: string; config: Record<string, unknown>; default_top_k: number };
  retrieval: {
    default_strategy: "vector" | "fulltext" | "hybrid" | "graph" | "parent-doc";
    hybrid: { vector_weight: number; fulltext_weight: number };
    vector: { num_candidates: number };
  };
  server: {
    host: string; port: number;
    mcp_host: string; mcp_port: number;
    mcp_transport: "streamable-http" | "stdio" | "sse";
    log_level: "debug" | "info" | "warning" | "error";
  };
}

export interface UnderstoodQuery {
  raw: string; corrected?: string; rewritten: string;
  entities: string[];
  metadata_filters: Record<string, unknown>;
  intent: string;
}

export interface RetrieveResult {
  content: string;
  metadata: Record<string, unknown>;
  score?: number | null;
}

/** Server-side timing breakdown returned by the backend.
 *  All values are wall-clock milliseconds. `mongo_ms` is the time spent
 *  inside the Atlas aggregation ($vectorSearch / $search / hybrid). */
export interface Timings {
  mongo_ms?: number | null;
  planning_ms?: number | null;
  understanding_ms?: number | null;
  summarize_ms?: number | null;
  total_ms?: number | null;
}

export interface RetrieveResponse {
  strategy: string;
  plan: Record<string, unknown>;
  results: RetrieveResult[];
  understood_query?: UnderstoodQuery | null;
  summary?: string | null;
  timings?: Timings | null;
  latencyMs?: number;
}

/** One full search turn kept in conversation history */
export interface SearchTurn {
  id: string;
  query: string;
  strategy: Strategy;
  topK: number;
  filters: Record<string, unknown>;
  /** Snapshot of the Atlas config that was active when this query ran. */
  atlasConfig: AtlasConfig;
  response: RetrieveResponse;
  latencyMs: number;
  timestamp: Date;
}
