import { Chip, Field, Segmented, TextInput } from "./UI";
import type { Backend } from "../lib/api";
import type { Strategy } from "../lib/types";
import { REQUIRED_ATLAS_FIELDS, STRATEGIES } from "../lib/types";

interface Props {
  backend: Backend;
  setBackend: (b: Backend) => void;
  fastapiUrl: string;
  setFastapiUrl: (u: string) => void;
  mcpUrl: string;
  setMcpUrl: (u: string) => void;
  strategy: Strategy;
  setStrategy: (s: Strategy) => void;
}

export default function SettingsPanel(p: Props) {
  const required = REQUIRED_ATLAS_FIELDS[p.strategy] ?? [];

  return (
    <div className="settings-panel">
      {/* Backend Selection */}
      <div className="settings-section">
        <div className="settings-title">🔌 Backend</div>
        <Segmented<Backend>
          value={p.backend}
          onChange={p.setBackend}
          options={[
            { value: "fastapi", label: "FastAPI" },
            { value: "mcp", label: "FastMCP" },
          ]}
        />
      </div>

      {/* API Configuration */}
      <div className="settings-section">
        <div className="settings-title">⚙️ API Configuration</div>
        {p.backend === "fastapi" ? (
          <Field label="FastAPI Base URL">
            <TextInput
              value={p.fastapiUrl}
              onChange={(e) => p.setFastapiUrl(e.target.value)}
              placeholder="http://localhost:8000"
            />
          </Field>
        ) : (
          <Field label="FastMCP Endpoint">
            <TextInput
              value={p.mcpUrl}
              onChange={(e) => p.setMcpUrl(e.target.value)}
              placeholder="http://localhost:8001/mcp"
            />
          </Field>
        )}
      </div>

      {/* Retrieval Strategy */}
      <div className="settings-section">
        <div className="settings-title">🎯 Strategy</div>
        <div style={{ marginBottom: 8 }}>
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
        </div>
        <div className="chip-row">
          {STRATEGIES.map((s) => (
            <Chip
              key={s}
              label={s}
              variant={s === p.strategy ? "accent" : "gray"}
            />
          ))}
        </div>
      </div>

      {/* Required Atlas Fields */}
      {required.length > 0 && (
        <div className="settings-section">
          <div className="settings-title">📋 Required Fields</div>
          <p
            className="muted"
            style={{ fontSize: "11px", marginBottom: "6px", margin: 0 }}
          >
            Atlas configuration required for <code>{p.strategy}</code>:
          </p>
          <div className="chip-row" style={{ marginTop: "6px" }}>
            {required.map((f) => (
              <Chip key={f} label={f} variant="blue" />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
