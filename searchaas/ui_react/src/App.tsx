import { useMemo, useRef, useEffect, useState } from "react";

import ConfigPane from "./components/ConfigPane";
import IntentPanel from "./components/IntentPanel";
import SummaryPanel from "./components/SummaryPanel";
import ResultsList from "./components/ResultsList";
import PipelinePanel from "./components/PipelinePanel";
import ChatInputBar from "./components/ChatInputBar";
import { Banner, Chip, CodeBlock, Latency, Segmented } from "./components/UI";

import { DEFAULT_CONFIG, mergeBackendSettings } from "./lib/defaults";
import type { AppConfig, Strategy, SearchTurn, Timings } from "./lib/types";
import { buildPipeline } from "./lib/pipeline";
import { getSettings, runSearch, toAtlasOverrides, toRetrievalOverrides, type Backend } from "./lib/api";
import { Field, TextInput } from "./components/UI";

export default function App() {
  // Sidebar config — hydrated from the backend GET /settings on mount so it
  // reflects the live searchaas.yaml (with secrets redacted). DEFAULT_CONFIG
  // is only used as the initial placeholder while that fetch is in flight.
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG);
  const [configLoading, setConfigLoading] = useState<boolean>(true);
  const [configError, setConfigError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);

  // Backend connection — defaults can be overridden at runtime via /config.js
  // (injected by the Docker/Cloud Run entrypoint as window.__SEARCHAAS_CONFIG__)
  const runtimeCfg = (typeof window !== "undefined"
    ? (window as unknown as { __SEARCHAAS_CONFIG__?: { FASTAPI_URL?: string; MCP_URL?: string; MCP_API_KEY?: string } }).__SEARCHAAS_CONFIG__
    : undefined) || {};
  const [backend, setBackend] = useState<Backend>("fastapi");
  const [fastapiUrl, setFastapiUrl] = useState(
    runtimeCfg.FASTAPI_URL ?? (window as any).SEARCHAAS_API_URL ?? "http://localhost:8000",
  );
  const [mcpUrl, setMcpUrl] = useState(
    runtimeCfg.MCP_URL ?? (window as any).SEARCHAAS_MCP_URL ?? "http://localhost:8001/mcp",
  );
  const [mcpApiKey, setMcpApiKey] = useState(runtimeCfg.MCP_API_KEY || "");
  const [showUrlPopup, setShowUrlPopup] = useState(false);

  // Query controls
  const [strategy, setStrategy] = useState<Strategy>("auto");
  const [topK, setTopK] = useState(20);
  const [query, setQuery] = useState("");
  const [filtersText, setFiltersText] = useState("{}");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Conversation turns
  const [turns, setTurns] = useState<SearchTurn[]>([]);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new turns / loading
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, loading]);

  // Close popup when clicking outside
  useEffect(() => {
    if (!showUrlPopup) return;
    const close = (e: MouseEvent) => {
      const target = e.target as Element;
      if (!target.closest(".conn-url-popup") && !target.closest(".conn-url-btn")) {
        setShowUrlPopup(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [showUrlPopup]);

  /* ── Hydrate config from backend ───────────────────────────────────────
     Pull the live YAML config from FastAPI `GET /settings` and merge it
     over DEFAULT_CONFIG so the side pane shows what the backend is really
     using. Re-runs whenever the FastAPI URL changes. */
  const reloadConfig = async (signal?: AbortSignal) => {
    setConfigLoading(true);
    setConfigError(null);
    try {
      const live = await getSettings(fastapiUrl);
      if (signal?.aborted) return;
      setConfig(mergeBackendSettings(live));
    } catch (e) {
      if (signal?.aborted) return;
      setConfigError(
        `Could not load /settings from ${fastapiUrl} — showing default config. ` +
        `(${String((e as Error).message ?? e)})`,
      );
    } finally {
      if (!signal?.aborted) setConfigLoading(false);
    }
  };

  useEffect(() => {
    const ctrl = new AbortController();
    reloadConfig(ctrl.signal);
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fastapiUrl]);

  const backendLabel = backend === "fastapi" ? "FastAPI REST" : "FastMCP";
  const activeUrl = backend === "fastapi" ? fastapiUrl : mcpUrl;

  const parsedFilters = useMemo<Record<string, unknown>>(() => {
    if (!filtersText.trim()) return {};
    try {
      const v = JSON.parse(filtersText);
      return typeof v === "object" && !Array.isArray(v) ? v : {};
    } catch { return {}; }
  }, [filtersText]);

  const onRun = async (q: string) => {
    if (!q.trim()) return;
    // Block searches until the live config has been hydrated from the backend
    // (searchaas.yaml + .env). Sending overrides built from the placeholder
    // DEFAULT_CONFIG would silently override the YAML on the server.
    if (configLoading) {
      setError("Still loading server config (searchaas.yaml + .env)… please retry in a moment.");
      return;
    }
    if (configError) {
      setError(
        `Cannot search: failed to load server config from ${fastapiUrl}. ` +
        `Click ↻ Reload to retry. (${configError})`,
      );
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const endpoint = backend === "fastapi" ? fastapiUrl : mcpUrl;
      const result = await runSearch(backend, endpoint, strategy, {
        query:     q,
        top_k:     topK,
        filters:   parsedFilters,
        atlas:     toAtlasOverrides(config.atlas, config.embeddings.provider),
        retrieval: toRetrievalOverrides(config.retrieval),
      }, mcpApiKey);
      const turn: SearchTurn = {
        id: Date.now().toString(),
        query: q,
        strategy,
        topK,
        filters: parsedFilters,
        atlasConfig: config.atlas,
        response: result.response,
        latencyMs: result.latencyMs,
        timestamp: new Date(),
      };
      setTurns(prev => [...prev, turn]);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setLoading(false);
    }
  };

  const hintQueries = [
    "What are the top-rated wireless headphones?",
    "Find Bluetooth speakers under $50",
    "Show me items with 4+ star reviews",
  ];

  // Active turn for the right pane (always the latest)
  const activeTurn = turns.length > 0 ? turns[turns.length - 1] : null;

  return (
    <div className={`app-shell ${collapsed ? "config-collapsed" : ""} ${rightCollapsed ? "details-collapsed" : ""}`}>
      {/* Left: YAML config sidebar */}
      <ConfigPane
        collapsed={collapsed}
        setCollapsed={setCollapsed}
        config={config}
        setConfig={setConfig}
        strategy={strategy}
        fastapiUrl={fastapiUrl}
        loading={configLoading}
        loadError={configError}
        onReload={() => reloadConfig()}
      />

      {/* Center: chat interface */}
      <main className="main-area">

        {/* ── Connection bar ── */}
        <div className="conn-bar">
          <div className="conn-bar-logo">
            {/* MongoDB official logomark */}
            <img
              className="mdb-leaf"
              src="https://storage-us-gcs.bfldr.com/6x3q9bsq4nj777n8sbbnp6/v/1069931050/original/MongoDB_Logomark_ForestGreen.png?Expires=1781031805&KeyName=gcs-bfldr-prod&Signature=9v8y6honF7fagYoVDeH1DsUn5dw="
              alt="MongoDB"
            />
            <span className="conn-bar-logo-text">
              MongoDB <span>Search-as-Service</span>
            </span>
          </div>
          <div className="conn-divider" />

          {/* Backend toggle */}
          <Segmented
            value={backend}
            onChange={(b) => setBackend(b as Backend)}
            options={[
              { value: "fastapi", label: "FastAPI REST" },
              { value: "mcp",    label: "FastMCP" },
            ]}
          />
          <div className="conn-divider" />

          {/* Active URL (click to edit) */}
          <button
            className="conn-url-btn"
            onClick={() => setShowUrlPopup(v => !v)}
            title="Click to edit API URLs"
          >
            <span className="dot-status" />
            {activeUrl}
          </button>

          {showUrlPopup && (
            <div className="conn-url-popup">
              <div className="popup-title">API Endpoints</div>
              <Field label="FastAPI REST URL">
                <TextInput
                  value={fastapiUrl}
                  onChange={e => setFastapiUrl(e.target.value)}
                  placeholder="http://localhost:8000"
                />
              </Field>
              <Field label="FastMCP URL">
                <TextInput
                  value={mcpUrl}
                  onChange={e => setMcpUrl(e.target.value)}
                  placeholder="http://localhost:8001/mcp"
                />
              </Field>
              <Field label="MCP API Key (Bearer)">
                <TextInput
                  type="password"
                  value={mcpApiKey}
                  onChange={e => setMcpApiKey(e.target.value)}
                  placeholder="required for authenticated MCP endpoint"
                />
              </Field>
            </div>
          )}

          <div className="conn-spacer" />
          <span className="conn-badge">
            <span className="dot-live" />
            {backendLabel}
          </span>
        </div>

        {/* ── Chat area ── */}
        <div className="chat-area">

          {/* Empty state */}
          {turns.length === 0 && !loading && (
            <div className="empty-state">
              <div className="empty-icon">🔍</div>
              <h2>Ask your knowledge base</h2>
              <p>
                Configure the YAML settings in the left panel, select a retrieval
                strategy below, then type your query.
              </p>
              <div className="empty-hints">
                {hintQueries.map(hint => (
                  <button key={hint} className="empty-hint"
                    onClick={() => setQuery(hint)}>
                    {hint}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Conversation turns */}
          {turns.map(turn => (
            <TurnView key={turn.id} turn={turn} />
          ))}

          {/* Loading indicator */}
          {loading && (
            <div className="turn">
              <div className="query-bubble">{query}</div>
              <div className="response-block" style={{ maxWidth: 120 }}>
                <div className="typing"><span /><span /><span /></div>
              </div>
            </div>
          )}

          {/* Error banner */}
          {error && <Banner variant="danger">{error}</Banner>}

          <div ref={chatEndRef} />
        </div>

        {/* ── Input bar ── treats `configLoading` as `loading` so the Run
            button is disabled until searchaas.yaml + .env have been
            mirrored into the UI. Otherwise an early request would carry
            placeholder atlas overrides that silently shadow the YAML. */}
        <ChatInputBar
          strategy={strategy}       setStrategy={setStrategy}
          topK={topK}               setTopK={setTopK}
          query={query}             setQuery={setQuery}
          filtersText={filtersText} setFiltersText={setFiltersText}
          loading={loading || configLoading}
          onRun={onRun}
        />
      </main>

      {/* Right: Pipeline & Planner details pane */}
      <DetailsPane
        turn={activeTurn}
        config={config}
        loading={loading}
        collapsed={rightCollapsed}
        setCollapsed={setRightCollapsed}
      />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   DetailsPane — right side panel showing Pipeline & Planner for latest turn
───────────────────────────────────────────────────────────────────────── */
function DetailsPane({
  turn,
  config,
  loading,
  collapsed,
  setCollapsed,
}: {
  turn: SearchTurn | null;
  config: AppConfig;
  loading: boolean;
  collapsed: boolean;
  setCollapsed: (v: boolean) => void;
}) {
  const effective = turn?.response.strategy.replace(/_/g, "-") ?? "";

  const pipeline = useMemo(
    () =>
      turn
        ? buildPipeline(
            effective,
            config,
            turn.response.understood_query?.rewritten ?? turn.query,
            turn.topK,
            turn.filters,
          )
        : null,
    [turn, effective, config],
  );

  const plan = turn?.response?.plan;
  const timings = turn?.response?.timings;
  const wallMs  = turn?.latencyMs;

  if (collapsed) {
    return (
      <aside className="details-rail">
        <button
          className="btn icon-only"
          title="Expand details"
          onClick={() => setCollapsed(false)}
        >
          ◀
        </button>
        <span className="rail-label">Details</span>
      </aside>
    );
  }

  return (
    <aside className="details-pane">
      <div className="details-head">
        <div className="details-head-title">
          <span className="dot" />
          Details
        </div>
        <button
          className="btn icon-dark"
          title="Collapse"
          onClick={() => setCollapsed(true)}
        >
          ▶
        </button>
      </div>

      <div className="details-body">
        {!turn && !loading && (
          <div className="details-empty">
            <div className="details-empty-icon">🔍</div>
            <p>Run a query to see the Planner output and MongoDB pipeline here.</p>
          </div>
        )}

        {loading && (
          <div className="details-empty">
            <div className="typing"><span /><span /><span /></div>
            <p style={{ marginTop: 10 }}>Waiting for response…</p>
          </div>
        )}

        {turn && !loading && (
          <>
            <div className="details-section">
              <div className="details-section-title">⏱ Timings</div>
              <TimingsPanel timings={timings} wallMs={wallMs} />
            </div>

            {plan && Object.keys(plan).length > 0 && (
              <div className="details-section">
                <div className="details-section-title">🗺 Planner Output</div>
                <CodeBlock code={JSON.stringify(plan, null, 2)} />
              </div>
            )}

            {pipeline && (
              <div className="details-section">
                <div className="details-section-title">📜 MongoDB Pipeline — {effective}</div>
                <PipelinePanel pipeline={pipeline} effectiveStrategy={effective} />
              </div>
            )}

            {!plan && !pipeline && (
              <div className="details-empty">
                <p>No planner output or pipeline available for this query.</p>
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   TimingsPanel — server-side latency breakdown.

   `mongo_ms` is the time Atlas spent executing the aggregation
   ($vectorSearch / $search / hybrid). It is the headline figure since
   the user explicitly asked for "MongoDB query latency".

   `wallMs` (client wall-clock) is shown for context so you can see the
   network + FastAPI overhead = wallMs - total_ms.
───────────────────────────────────────────────────────────────────────── */
function TimingsPanel({
  timings, wallMs,
}: {
  timings: Timings | null | undefined;
  wallMs: number | undefined;
}) {
  // Empty state — server didn't return timings (older backend).
  const hasAnyTiming = timings != null && Object.values(timings).some(v => v != null);
  if (!hasAnyTiming) {
    return (
      <div className="details-empty" style={{ padding: "12px 0" }}>
        <p>No server timings reported for this query.</p>
        {wallMs != null && (
          <p style={{ marginTop: 4 }}>
            Client-side wall clock: <strong>{fmtMs(wallMs)}</strong>
          </p>
        )}
      </div>
    );
  }

  const mongo = timings.mongo_ms ?? 0;
  // Color the MongoDB latency the same way the chat header colors wall clock.
  const mongoClass = mongo < 800 ? "fast" : mongo < 2500 ? "mid" : "slow";

  const networkMs =
    wallMs != null && timings.total_ms != null
      ? Math.max(0, Math.round(wallMs - timings.total_ms))
      : null;

  return (
    <div className="timings-panel">
      {/* Headline: MongoDB query latency */}
      <div className="timings-headline">
        <span className="timings-headline-label">
          <span className="mdb-dot" />
          MongoDB query
        </span>
        <span className={`latency ${mongoClass}`}>⚡ {fmtMs(mongo)}</span>
      </div>

      {/* Breakdown rows */}
      <div className="timings-rows">
        {timings.understanding_ms != null && (
          <TimingRow label="Query understanding" ms={timings.understanding_ms} />
        )}
        {timings.planning_ms != null && (
          <TimingRow label="Planner" ms={timings.planning_ms} />
        )}
        <TimingRow label="MongoDB aggregation" ms={mongo} emphasize />
        {timings.summarize_ms != null && (
          <TimingRow label="LLM summarize" ms={timings.summarize_ms} />
        )}
        {timings.total_ms != null && (
          <TimingRow label="Server total" ms={timings.total_ms} />
        )}
        {networkMs != null && (
          <TimingRow label="Network + FastAPI" ms={networkMs} muted />
        )}
        {wallMs != null && (
          <TimingRow label="Wall clock (browser)" ms={wallMs} muted />
        )}
      </div>
    </div>
  );
}

function TimingRow({
  label, ms, emphasize, muted,
}: {
  label: string; ms: number; emphasize?: boolean; muted?: boolean;
}) {
  return (
    <div className={`timing-row${emphasize ? " emphasize" : ""}${muted ? " muted" : ""}`}>
      <span className="timing-label">{label}</span>
      <span className="timing-value">{fmtMs(ms)}</span>
    </div>
  );
}

function fmtMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  if (ms >= 10)   return `${Math.round(ms)} ms`;
  return `${ms.toFixed(1)} ms`;
}

/* ─────────────────────────────────────────────────────────────────────────
   TurnView — renders one complete search turn (query bubble + response)
───────────────────────────────────────────────────────────────────────── */
function TurnView({ turn }: { turn: SearchTurn }) {
  const { query, strategy, response, latencyMs, timestamp } = turn;
  const effective = response.strategy.replace(/_/g, "-");
  const autoMode = strategy === "auto";

  const timeStr = timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const latClass = latencyMs < 800 ? "fast" : latencyMs < 2500 ? "mid" : "slow";
  const latDisplay = latencyMs >= 1000 ? `${(latencyMs / 1000).toFixed(1)}s` : `${latencyMs}ms`;

  return (
    <div className="turn">
      {/* User bubble */}
      <div className="query-bubble">{query}</div>
      <div className="query-meta">
        <span>{timeStr}</span>
        {autoMode && <Chip label="AUTO" variant="accent" />}
        <Chip label={effective} variant="green" />
        <Latency ms={latencyMs} />
      </div>

      {/* Response block */}
      <div className="response-block">

        {/* Dark header with strategy + stats */}
        <div className="resp-header">
          <span className="resp-header-title">🤖 SearchaaS</span>
          {autoMode && <span className="chip dark-white">AUTO</span>}
          <span className="chip dark-white">{effective}</span>
          <div className="spacer" />
          <span className={`latency dark ${latClass}`}>⚡ {latDisplay}</span>
          <span className="chip dark-white">{response.results.length} result{response.results.length !== 1 ? "s" : ""}</span>
        </div>

        {/* Stats strip */}
        <div className="resp-stats">
          <div className="resp-stat highlight">
            <span>⏱</span>
            <strong>{latDisplay}</strong>
            <span>latency</span>
          </div>
          <div className="resp-stat">
            <span>📊</span>
            <strong>{response.results.length}</strong>
            <span>results</span>
          </div>
          <div className="resp-stat">
            <span>🎯</span>
            <strong>{effective}</strong>
            {autoMode && <span style={{ color: "var(--blue)", fontWeight: 600 }}>(auto)</span>}
          </div>
          <div className="resp-stat">
            <span>🔌</span>
            <strong>{turn.topK}</strong>
            <span>top-K</span>
          </div>
        </div>

        {/* Intent panel */}
        {response.understood_query && (
          <IntentPanel
            understood={response.understood_query}
            resolvedStrategy={effective}
            autoMode={autoMode}
          />
        )}

        {/* Summary */}
        <SummaryPanel
          summary={response.summary}
          results={response.results}
        />

        {/* Results — collapsed by default */}
        <details className="acc results-acc">
          <summary>
            📚 Results
            <span className="results-count-badge">{response.results.length}</span>
          </summary>
          <div className="acc-body results-acc-body">
            <ResultsList results={response.results} />
          </div>
        </details>

      </div>
    </div>
  );
}
