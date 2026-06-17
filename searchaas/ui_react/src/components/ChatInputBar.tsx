import { useState } from "react";
import type { Strategy } from "../lib/types";
import { STRATEGIES } from "../lib/types";

interface Props {
  strategy: Strategy;    setStrategy: (s: Strategy) => void;
  topK: number;          setTopK: (n: number) => void;
  query: string;         setQuery: (q: string) => void;
  filtersText: string;   setFiltersText: (s: string) => void;
  loading: boolean;
  onRun: (query: string) => void;
}

const STRATEGY_ICONS: Record<string, string> = {
  auto:        "✨",
  vector:      "🔮",
  fulltext:    "🔤",
  hybrid:      "⚡",
  graph:       "🕸",
  "parent-doc": "📄",
};

export default function ChatInputBar({
  strategy, setStrategy,
  topK, setTopK,
  query, setQuery,
  filtersText, setFiltersText,
  loading, onRun,
}: Props) {
  const [filtersError, setFiltersError] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(false);

  const onFilters = (v: string) => {
    setFiltersText(v);
    if (!v.trim()) { setFiltersError(null); return; }
    try {
      const parsed = JSON.parse(v);
      if (typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Must be a JSON object");
      }
      setFiltersError(null);
    } catch (e) {
      setFiltersError((e as Error).message);
    }
  };

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (!loading && query.trim() && !filtersError) onRun(query);
    }
  };

  const handleSend = () => {
    if (!loading && query.trim() && !filtersError) {
      onRun(query);
      setQuery("");
    }
  };

  const canSend = !loading && query.trim().length > 0 && !filtersError;

  return (
    <div className="chat-input-bar">

      {/* Filters drawer (collapsible) */}
      {showFilters && (
        <div className="filters-drawer">
          <label>Metadata Filters (JSON)</label>
          <textarea
            className="textarea mono"
            rows={3}
            value={filtersText}
            onChange={e => onFilters(e.target.value)}
            placeholder='e.g. {"category": "electronics", "rating": 4}'
          />
          {filtersError && (
            <span style={{ fontSize: 11, color: "var(--red)", marginTop: 4, display: "block" }}>
              ⚠ {filtersError}
            </span>
          )}
        </div>
      )}

      {/* Options row: strategy pills + top-K + filters toggle */}
      <div className="chat-input-options">
        <span className="label">Strategy:</span>
        <div className="strategy-pills">
          {STRATEGIES.map(s => (
            <button
              key={s}
              type="button"
              className={`strategy-pill ${s === strategy ? "active" : ""}`}
              onClick={() => setStrategy(s)}
              title={s}
            >
              {STRATEGY_ICONS[s] ?? "🔍"} {s}
            </button>
          ))}
        </div>

        <div className="topk-wrap">
          <span className="topk-label">Top K:</span>
          <input
            type="number"
            className="topk-input"
            min={1} max={50}
            value={topK}
            onChange={e => setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 20)))}
          />
        </div>

        <button
          type="button"
          className={`filters-toggle ${showFilters ? "open" : ""}`}
          onClick={() => setShowFilters(v => !v)}
        >
          🔍 Filters {showFilters ? "▲" : "▼"}
          {filtersError && <span style={{ color: "var(--red)", marginLeft: 2 }}>●</span>}
        </button>
      </div>

      {/* Main input row */}
      <div className="chat-input-row">
        <textarea
          placeholder="Ask the knowledge base… (Cmd/Ctrl + Enter to send)"
          value={query}
          rows={1}
          onChange={e => {
            setQuery(e.target.value);
            // Auto-resize
            e.target.style.height = "auto";
            e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px";
          }}
          onKeyDown={handleKey}
        />
        <button
          className="send-btn"
          disabled={!canSend}
          onClick={handleSend}
          title="Send (Cmd/Ctrl+Enter)"
        >
          {loading ? <span className="spin" /> : "↑"}
        </button>
      </div>

      <div className="chat-input-hint">
        <b>{strategy}</b> strategy · top {topK} results · Cmd/Ctrl+Enter to send
      </div>
    </div>
  );
}
