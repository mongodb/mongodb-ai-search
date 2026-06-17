import { useState } from "react";
import { Button, CodeBlock } from "./UI";
import type { RetrieveResult } from "../lib/types";

export default function ResultsList({ results }: { results: RetrieveResult[] }) {
  if (!results?.length) return <p className="muted">No results returned.</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {results.map((r, i) => <ResultTile key={i} index={i + 1} item={r} />)}
    </div>
  );
}

function getScoreClass(score: number): "high" | "mid" | "low" | "none" {
  // Normalize: if score > 1, assume it's on a larger scale (e.g. BM25)
  const normalized = score > 1 ? score / 10 : score;
  if (normalized >= 0.75) return "high";
  if (normalized >= 0.45) return "mid";
  return "low";
}

function formatScore(score: number): string {
  if (score > 1) return score.toFixed(3);
  return `${(score * 100).toFixed(1)}%`;
}

function scoreBarWidth(score: number): number {
  const normalized = score > 1 ? Math.min(score / 10, 1) : Math.abs(score);
  return Math.round(Math.min(normalized * 100, 100));
}

function ResultTile({ index, item }: { index: number; item: RetrieveResult }) {
  const [showFull, setShowFull] = useState(false);
  const [showMeta, setShowMeta] = useState(false);

  const content = item.content ?? "";
  const isLong = content.length > 500;
  const visible = !showFull && isLong ? content.slice(0, 500) + "…" : content;
  const hasMeta = item.metadata && Object.keys(item.metadata).length > 0;

  const score = item.score ?? null;
  const hasScore = score !== null && score !== undefined;
  const scoreClass = hasScore ? getScoreClass(score) : "none";
  const barWidth = hasScore ? scoreBarWidth(score) : 0;

  const scoreIcon = { high: "🟢", mid: "🟡", low: "🔴", none: "⚪" }[scoreClass];
  const scoreColor = {
    high: "var(--green)",
    mid:  "var(--amber)",
    low:  "var(--red)",
    none: "var(--text-3)",
  }[scoreClass];

  const title = item.metadata?.title ? String(item.metadata.title) : null;
  const source = item.metadata?.source ? String(item.metadata.source).split("/").pop() ?? null : null;

  return (
    <div className="result-card" style={{
      borderRadius: 8,
      border: "1px solid var(--border-soft)",
      background: "var(--surface-1)",
      padding: 16,
      transition: "all 0.2s ease",
    }}
    onMouseEnter={(e) => {
      if (e.currentTarget) e.currentTarget.style.boxShadow = "0 2px 8px rgba(0,0,0,0.1)";
      if (e.currentTarget) e.currentTarget.style.borderColor = "var(--border-active)";
    }}
    onMouseLeave={(e) => {
      if (e.currentTarget) e.currentTarget.style.boxShadow = "none";
      if (e.currentTarget) e.currentTarget.style.borderColor = "var(--border-soft)";
    }}
    >
      {/* Header: rank + title/source + score */}
      <div className="result-card-head" style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flex: 1 }}>
          <div className="result-rank" style={{
            width: 32,
            height: 32,
            borderRadius: "50%",
            background: scoreColor,
            color: "white",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: "600",
            fontSize: "0.95em",
          }}>
            {index}
          </div>

          <div style={{ flex: 1 }}>
            {title && (
              <div style={{
                fontWeight: "600",
                fontSize: "0.95em",
                marginBottom: 4,
                color: "var(--text-1)",
              }}>
                {title}
              </div>
            )}
            {source && (
              <div style={{
                fontSize: "0.85em",
                color: "var(--text-3)",
              }}>
                📄 {source}
              </div>
            )}
          </div>
        </div>

        {/* Score display */}
        {hasScore && (
          <div className="score-wrapper" style={{ marginLeft: 12 }}>
            <div className="score-bar-wrap">
              <div className="score-track">
                <div
                  className={`score-fill ${scoreClass}`}
                  style={{ width: `${barWidth}%` }}
                />
              </div>
            </div>
            <div className={`score-pill ${scoreClass}`} style={{
              padding: "4px 8px",
              borderRadius: 4,
              fontSize: "0.8em",
              fontWeight: "500",
              background: scoreColor,
              color: "white",
              display: "flex",
              gap: 4,
              alignItems: "center",
            }}>
              <span>{scoreIcon}</span>
              <span>{formatScore(score)}</span>
            </div>
          </div>
        )}
      </div>

      {/* Content */}
      <div className={`result-body ${isLong && !showFull ? "truncated" : ""}`} style={{
        marginBottom: 12,
        lineHeight: 1.6,
        fontSize: "0.95em",
        color: "var(--text-2)",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}>
        <div className="result-content">{visible}</div>
      </div>

      {/* Footer: actions + meta count */}
      <div className="result-footer" style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        borderTop: "1px solid var(--border-soft)",
        paddingTop: 12,
      }}>
        <div style={{ display: "flex", gap: 6, flex: 1 }}>
          {isLong && (
            <Button size="sm" onClick={() => setShowFull(b => !b)}>
              {showFull ? "▲ Less" : "▼ More"}
            </Button>
          )}
          {hasMeta && (
            <Button size="sm" onClick={() => setShowMeta(b => !b)}>
              {showMeta ? "Hide metadata" : "📋 Metadata"}
            </Button>
          )}
        </div>
        {hasMeta && (
          <span className="result-meta-pill" style={{
            fontSize: "0.8em",
            color: "var(--text-3)",
          }}>
            {Object.keys(item.metadata).length} fields
          </span>
        )}
      </div>

      {/* Expanded metadata */}
      {showMeta && hasMeta && (
        <div style={{
          marginTop: 12,
          padding: 12,
          borderTop: "1px solid var(--border-soft)",
          background: "var(--surface-2)",
          borderRadius: 4,
        }}>
          <CodeBlock code={JSON.stringify(item.metadata, null, 2)} />
        </div>
      )}
    </div>
  );
}
