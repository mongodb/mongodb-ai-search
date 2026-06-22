import type { AppConfig, AtlasConfig, RetrieveResponse, Strategy } from "./types";
import { STRATEGY_MAP } from "./types";

export type Backend = "fastapi" | "mcp";
const MCP_PROTOCOL_VERSION = "2024-11-05";

/** Atlas config fields the UI sends with every request (uri excluded for security). */
export interface AtlasOverrides {
  collection?:    string;
  vector_index?:  string;
  search_index?:  string;
  text_key?:      string;
  embedding_key?: string;
  dimensions?:    number;
}

/** Convert a full AtlasConfig into the overrides subset the backend accepts.
 *
 *  When AutoEmbeddings is active (server-side embedding), `embedding_key`
 *  and `dimensions` MUST NOT be sent — langchain-mongodb raises
 *  `ConfigurationError("Auto-embeddings cannot have embedding key…")`.
 *  Pass `embeddingsProvider` so we can suppress those overrides correctly. */
export function toAtlasOverrides(
  cfg: AtlasConfig,
  embeddingsProvider?: string,
): AtlasOverrides {
  const isAuto = embeddingsProvider === "auto";
  return {
    collection:    cfg.collection    || undefined,
    vector_index:  cfg.vector_index  || undefined,
    search_index:  cfg.search_index  || undefined,
    text_key:      cfg.text_key      || undefined,
    // In AutoEmbed mode the index owns the embedding field; the UI must not
    // override it. In client-side mode, send what the user typed.
    embedding_key: isAuto ? undefined : (cfg.embedding_key || undefined),
    // `dimensions = -1` is a "do not validate" sentinel in client-side mode,
    // so don't bother sending it; in AutoEmbed mode it's enforced backend-side.
    dimensions:    isAuto ? undefined : (cfg.dimensions && cfg.dimensions > 0 ? cfg.dimensions : undefined),
  };
}

/** Convert the retrieval section of AppConfig into per-request overrides. */
export function toRetrievalOverrides(cfg: AppConfig["retrieval"]): RetrievalOverrides {
  return {
    vector_weight:   cfg.hybrid?.vector_weight   ?? undefined,
    fulltext_weight: cfg.hybrid?.fulltext_weight ?? undefined,
    num_candidates:  cfg.vector?.num_candidates  ?? undefined,
  };
}

/** Retrieval tuning sent per-request (hybrid weights, vector candidates). */
export interface RetrievalOverrides {
  vector_weight?:   number;
  fulltext_weight?: number;
  num_candidates?:  number;
}

export interface SearchPayload {
  query:     string;
  top_k:     number;
  filters:   Record<string, unknown>;
  atlas?:    AtlasOverrides;
  retrieval?: RetrievalOverrides;
}

/** Shape the backend /settings endpoint accepts. */
export interface SettingsUpdate {
  atlas?: {
    uri?: string; database?: string; collection?: string;
    vector_index?: string; search_index?: string;
    text_key?: string; embedding_key?: string;
    relevance_score_fn?: string; dimensions?: number;
  };
  embeddings?: { provider?: string; config?: Record<string, unknown> };
  planner?:   { llm_provider?: string; config?: Record<string, unknown>; default_top_k?: number };
  retrieval?: { default_strategy?: string; hybrid?: Record<string, unknown>; vector?: Record<string, unknown> };
}

/* ── Settings API ─────────────────────────────────────────────────────── */

export async function getSettings(apiBase: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${apiBase.replace(/\/$/, "")}/settings`);
  if (!res.ok) throw new Error(`GET /settings ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return res.json();
}

export async function applySettings(apiBase: string, update: SettingsUpdate): Promise<void> {
  const res = await fetch(`${apiBase.replace(/\/$/, "")}/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!res.ok) throw new Error(`POST /settings ${res.status}: ${(await res.text()).slice(0, 500)}`);
}

/* ── FastAPI ──────────────────────────────────────────────────────────── */

export async function runFastAPI(
  apiBase: string,
  strategy: Strategy,
  payload: SearchPayload,
): Promise<RetrieveResponse> {
  const { restPath } = STRATEGY_MAP[strategy];
  const res = await fetch(`${apiBase.replace(/\/$/, "")}${restPath}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`FastAPI ${res.status}: ${(await res.text()).slice(0, 500)}`);
  return res.json() as Promise<RetrieveResponse>;
}

/* ── MCP (Streamable HTTP / JSON-RPC) ─────────────────────────────────── */

function parseSSE(text: string): any {
  for (const line of text.split("\n")) {
    if (line.startsWith("data:")) {
      const payload = line.slice(5).trim();
      if (payload) try { return JSON.parse(payload); } catch { return payload; }
    }
  }
  const t = text.trim();
  if (!t) throw new Error("Empty MCP response");
  return JSON.parse(t);
}

async function mcpPost(url: string, body: unknown, sessionId?: string | null, apiKey?: string | null) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
  };
  if (sessionId) headers["mcp-session-id"] = sessionId;
  if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;
  return fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
}

const _sessions = new Map<string, string>();

export const resetMcpSession = (url?: string) =>
  url ? _sessions.delete(url) : _sessions.clear();

export const getMcpSession = (url: string) => _sessions.get(url);

async function initMcp(url: string, apiKey?: string | null): Promise<string> {
  const res = await mcpPost(url, {
    jsonrpc: "2.0", id: 1, method: "initialize",
    params: {
      protocolVersion: MCP_PROTOCOL_VERSION, capabilities: {},
      clientInfo: { name: "searchaas-react", version: "0.2.0" },
    },
  }, null, apiKey);
  if (res.status === 401) throw new Error("MCP 401 Unauthorized — set the MCP API key in settings.");
  if (!res.ok) throw new Error(`MCP initialize failed: HTTP ${res.status}`);
  const sid = res.headers.get("mcp-session-id");
  if (!sid) throw new Error("MCP server did not return mcp-session-id header.");
  const ack = await mcpPost(url, { jsonrpc: "2.0", method: "notifications/initialized", params: {} }, sid, apiKey);
  if (!ack.ok) throw new Error(`MCP initialized notification failed: HTTP ${ack.status}`);
  _sessions.set(url, sid);
  return sid;
}

async function callTool(url: string, sid: string, tool: string, args: Record<string, unknown>, apiKey?: string | null): Promise<any> {
  const res = await mcpPost(url, {
    jsonrpc: "2.0", id: 2, method: "tools/call",
    params: { name: tool, arguments: args },
  }, sid, apiKey);
  if (!res.ok) throw new Error(`MCP tools/call failed: HTTP ${res.status} — ${(await res.text()).slice(0, 400)}`);
  const msg = parseSSE(await res.text());
  if (msg.error) throw new Error(`MCP error: ${JSON.stringify(msg.error)}`);
  const result = msg.result ?? {};
  if (typeof result === "object" && result !== null) {
    if ("structuredContent" in result) {
      const sc = (result as any).structuredContent;
      return (sc && typeof sc === "object" && Object.keys(sc).length === 1 && "result" in sc) ? sc.result : sc;
    }
    if ("content" in result && Array.isArray((result as any).content)) {
      const text = (result as any).content.filter((b: any) => b?.type === "text").map((b: any) => b.text ?? "").join("").trim();
      if (text) try { return JSON.parse(text); } catch { return text; }
    }
  }
  return result;
}

export async function runMcp(
  mcpUrl: string,
  strategy: Strategy,
  payload: SearchPayload,
  apiKey?: string | null,
): Promise<RetrieveResponse> {
  const { mcpTool } = STRATEGY_MAP[strategy];
  let sid = _sessions.get(mcpUrl) ?? await initMcp(mcpUrl, apiKey);
  let raw: any;
  try {
    raw = await callTool(mcpUrl, sid, mcpTool, payload as unknown as Record<string, unknown>, apiKey);
  } catch (err) {
    const msg = String(err);
    if (/session/i.test(msg) || /\b404\b/.test(msg)) {
      _sessions.delete(mcpUrl);
      sid = await initMcp(mcpUrl, apiKey);
      raw = await callTool(mcpUrl, sid, mcpTool, payload as unknown as Record<string, unknown>, apiKey);
    } else throw err;
  }
  if (raw && typeof raw === "object" && "results" in raw)
    return { strategy: raw.strategy ?? strategy, plan: raw.plan ?? {}, results: raw.results ?? [], understood_query: raw.understood_query ?? null, summary: raw.summary ?? null, timings: raw.timings ?? null };
  if (Array.isArray(raw))
    return { strategy, plan: {}, results: raw };
  return { strategy, plan: {}, results: [] };
}

/* ── Dispatcher with latency timing ──────────────────────────────────── */

export interface SearchResult {
  response: RetrieveResponse;
  latencyMs: number;
}

export async function runSearch(
  backend: Backend, endpoint: string, strategy: Strategy,
  payload: SearchPayload,
  apiKey?: string | null,
): Promise<SearchResult> {
  const t0 = performance.now();
  const response = backend === "fastapi"
    ? await runFastAPI(endpoint, strategy, payload)
    : await runMcp(endpoint, strategy, payload, apiKey);
  return { response, latencyMs: Math.round(performance.now() - t0) };
}
