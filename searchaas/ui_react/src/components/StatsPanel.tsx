import type { RetrieveResponse } from "../lib/types";

interface Props {
  response: RetrieveResponse | null;
  backend: string;
  autoMode: boolean;
}

export default function StatsPanel({ response, backend, autoMode }: Props) {
  if (!response) return null;

  const latencyMs = response.latencyMs ?? 0;
  let latencyClass = "fast";
  if (latencyMs > 2000) latencyClass = "slow";
  else if (latencyMs > 1000) latencyClass = "moderate";

  const resultCount = response.results.length;
  const strategy = response.strategy.replace(/_/g, "-");

  return (
    <div className="stats-panel">
      <div className="stat-item">
        <span className="stat-label">⏱️ Latency</span>
        <span className={`latency-badge-modern ${latencyClass}`}>
          {latencyMs}ms
        </span>
      </div>

      <div className="stat-item">
        <span className="stat-label">📊 Results</span>
        <span className="stat-value">{resultCount}</span>
      </div>

      <div className="stat-item">
        <span className="stat-label">🎯 Strategy</span>
        <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--accent)" }}>
          {autoMode && <span style={{ marginRight: "4px" }}>AUTO →</span>}
          {strategy}
        </span>
      </div>

      <div className="stat-item">
        <span className="stat-label">🔌 Backend</span>
        <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--accent)" }}>
          {backend}
        </span>
      </div>
    </div>
  );
}
