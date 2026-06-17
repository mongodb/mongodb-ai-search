import { useState } from "react";
import { Chip } from "./UI";
import type { UnderstoodQuery, Strategy } from "../lib/types";
import { INTENT_STRATEGY_HINT } from "../lib/types";

export default function IntentPanel({
  understood, resolvedStrategy, autoMode,
}: {
  understood: UnderstoodQuery;
  resolvedStrategy: string;
  autoMode: boolean;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const intent = understood.intent || "—";
  const expected = (INTENT_STRATEGY_HINT[intent] ?? "hybrid") as Strategy;
  const matches = resolvedStrategy === expected;

  return (
    <div className="intent-strip">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          width: "100%",
          textAlign: "left",
          fontSize: "1em",
          fontWeight: "600",
        }}
      >
        <h4 style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
          <span>{isExpanded ? "▼" : "▶"}</span>
          🧠 Query Intent
        </h4>
      </button>

      {isExpanded && (
        <>
          <div className="chip-row">
            <Chip label={`intent: ${intent}`} variant="blue" />
            <Chip label={`chosen: ${resolvedStrategy}`} variant="green" />
            {autoMode && (
              <Chip
                label={`expected: ${expected}`}
                variant={matches ? "green" : "amber"}
              />
            )}
          </div>

          {autoMode ? (
            <p className="kv" style={{ marginTop: 6 }}>
              {matches
                ? <>Intent <code>{intent}</code> → <b>{expected}</b> ✓ matches planner choice.</>
                : <>Intent <code>{intent}</code> typically maps to <b>{expected}</b>, but planner picked <b>{resolvedStrategy}</b> based on full context.</>}
            </p>
          ) : (
            <p className="kv" style={{ marginTop: 6, color: "var(--text-3)", fontSize: "0.9em" }}>
              Strategy was manually specified (not auto mode).
            </p>
          )}

          <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
            <div className="intent-kv"><b>Raw:</b> {understood.raw}</div>
            {understood.rewritten && understood.rewritten !== understood.raw && (
              <div className="intent-kv"><b>Rewritten:</b> {understood.rewritten}</div>
            )}
            {understood.entities?.length > 0 && (
              <div className="intent-kv" style={{ flexWrap: "wrap", gap: 4 }}>
                <b>Entities:</b>
                {understood.entities.slice(0, 14).map((e) => (
                  <Chip key={e} label={e} variant="gray" />
                ))}
              </div>
            )}
            {Object.keys(understood.metadata_filters ?? {}).length > 0 && (
              <div className="intent-kv">
                <b>Inferred filters:</b>{" "}
                <code>{JSON.stringify(understood.metadata_filters)}</code>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
