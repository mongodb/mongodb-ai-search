import type { RetrieveResult } from "../lib/types";

export default function SummaryPanel({
  summary,
  results,
}: {
  summary?: string | null;
  results: RetrieveResult[];
}) {
  const hasSummary = !!summary;
  const hasResults = results?.length > 0;

  if (!hasSummary && !hasResults) return null;

  /* ── Fallback when no LLM summary ── */
  if (!hasSummary) {
    const preview = (results[0]?.content ?? "").trim().slice(0, 320);
    return (
      <div className="summary-strip">
        <h4>📝 Top Result Preview</h4>
        <p>{preview}{(results[0]?.content ?? "").length > 320 ? "…" : ""}</p>
        <p style={{ fontSize: 11.5, color: "var(--text-3)", marginTop: 6 }}>
          LLM summary unavailable — showing first result snippet.
        </p>
      </div>
    );
  }

  /* ── Summary of returned documents ── */
  return (
    <div className="summary-strip">
      <div className="summary-section">
        <p className="summary-paragraph" style={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}>
          {summary}
        </p>
      </div>
    </div>
  );
}
