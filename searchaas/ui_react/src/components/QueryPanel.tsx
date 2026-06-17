import { useState } from "react";
import {
  Banner, Button, Disclosure, Field, NumberInput, TextArea,
} from "./UI";
import type { Backend } from "../lib/api";
import type { Strategy } from "../lib/types";
import { STRATEGIES } from "../lib/types";

interface Props {
  backend: Backend;                  setBackend: (b: Backend) => void;
  fastapiUrl: string;                setFastapiUrl: (u: string) => void;
  mcpUrl: string;                    setMcpUrl: (u: string) => void;
  strategy: Strategy;                setStrategy: (s: Strategy) => void;
  topK: number;                      setTopK: (n: number) => void;
  query: string;                     setQuery: (q: string) => void;
  filtersText: string;               setFiltersText: (s: string) => void;
  loading: boolean;
  error: string | null;
  onRun: () => void;
}

export default function QueryPanel(p: Props) {
  const [filtersError, setFiltersError] = useState<string | null>(null);

  const onFilters = (v: string) => {
    p.setFiltersText(v);
    if (!v.trim()) { setFiltersError(null); return; }
    try {
      const parsed = JSON.parse(v);
      if (typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("filters must be a JSON object");
      }
      setFiltersError(null);
    } catch (e) {
      setFiltersError((e as Error).message);
    }
  };

  return (
    <div className="card">
      <h3>💬 Ask</h3>
      <p className="sub">
        Enter your question below. Configure retrieval settings on the left.
      </p>

      {/* Query Input */}
      <div className="query-input-wrapper">
        <TextArea
          rows={5}
          placeholder="Ask the knowledge base…"
          value={p.query}
          onChange={(e) => p.setQuery(e.target.value)}
        />
      </div>

      {/* Query Controls */}
      <div className="row">
        <Field label="Results Count">
          <NumberInput 
            min={1} 
            max={50}
            value={p.topK}
            onChange={(e) => p.setTopK(Number(e.target.value) || 20)} 
          />
        </Field>
        <Field label="Retrieval">
          <select
            className="select"
            value={p.strategy}
            onChange={(e) => p.setStrategy(e.target.value as Strategy)}
          >
            {STRATEGIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {/* Filters - Collapsible */}
      <Disclosure summary="🔍 Advanced Filters (JSON)">
        <TextArea
          rows={4}
          className={`textarea mono${filtersError ? " error" : ""}`}
          value={p.filtersText}
          onChange={(e) => onFilters(e.target.value)}
          placeholder='{"doc_type": "policy"}'
        />
        {filtersError && (
          <div className="field-hint" style={{ color: "var(--red)" }}>
            {filtersError}
          </div>
        )}
      </Disclosure>

      {p.error && (
        <Banner variant="danger">{p.error}</Banner>
      )}

      {/* Run Button */}
      <Button
        variant="primary"
        loading={p.loading}
        disabled={!p.query.trim() || !!filtersError}
        onClick={p.onRun}
        style={{ width: "100%" }}
      >
        {p.loading ? "🔄 Searching…" : "🔎 Search"}
      </Button>
    </div>
  );
}
