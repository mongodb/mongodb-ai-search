import { useState } from "react";
import type { ButtonHTMLAttributes, InputHTMLAttributes,
  SelectHTMLAttributes, TextareaHTMLAttributes, ReactNode } from "react";

/* ── Button ─────────────────────────────────────────────────────────── */
type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost";
  size?: "default" | "sm";
  loading?: boolean;
};
export function Button({ variant="default", size, loading, children, className, disabled, ...r }: BtnProps) {
  return (
    <button
      className={["btn", variant!=="default"?variant:"", size==="sm"?"sm":"", className??""  ].filter(Boolean).join(" ")}
      disabled={disabled||loading} {...r}
    >
      {loading ? <span className="spin" /> : null}
      {children}
    </button>
  );
}

/* ── Field ───────────────────────────────────────────────────────────── */
export function Field({ label, required, hint, children }: {
  label: ReactNode; required?: boolean; hint?: ReactNode; children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label}{required && <span className="required-star">*</span>}
      </span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

/* ── Inputs ──────────────────────────────────────────────────────────── */
export const TextInput = (p: InputHTMLAttributes<HTMLInputElement>) =>
  <input className="input" type="text" {...p} />;

export const TextArea = (p: TextareaHTMLAttributes<HTMLTextAreaElement>) =>
  <textarea className="textarea" {...p} />;

export const NumberInput = (p: InputHTMLAttributes<HTMLInputElement>) =>
  <input className="input" type="number" {...p} />;

/* ── Select ──────────────────────────────────────────────────────────── */
export function Select({ options, ...r }: SelectHTMLAttributes<HTMLSelectElement> & {
  options: ReadonlyArray<string | { value: string; label: string }>;
}) {
  return (
    <select className="select" {...r}>
      {options.map((o) => {
        const v = typeof o === "string" ? o : o.value;
        const l = typeof o === "string" ? o : o.label;
        return <option key={v} value={v}>{l}</option>;
      })}
    </select>
  );
}

/* ── Slider ──────────────────────────────────────────────────────────── */
export function Slider({ value, onChange, min=0, max=1, step=0.05 }: {
  value: number; onChange: (n: number) => void; min?: number; max?: number; step?: number;
}) {
  return (
    <input type="range" className="slider" min={min} max={max} step={step} value={value}
      onChange={(e) => onChange(Number(e.target.value))} />
  );
}

/* ── Chip ────────────────────────────────────────────────────────────── */
export function Chip({ label, variant="gray" }: {
  label: ReactNode; variant?: "gray"|"accent"|"green"|"blue"|"amber"|"red"|"purple";
}) {
  return <span className={`chip ${variant}`}>{label}</span>;
}

/* ── Banner ──────────────────────────────────────────────────────────── */
export function Banner({ variant="info", children }: {
  variant?: "info"|"success"|"warn"|"danger"; children: ReactNode;
}) {
  return <div className={`banner ${variant}`}>{children}</div>;
}

/* ── Segmented ───────────────────────────────────────────────────────── */
export function Segmented<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void;
  options: ReadonlyArray<{ value: T; label: ReactNode }>;
}) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o.value} type="button"
          className={o.value === value ? "active" : ""}
          onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ── Tabs ────────────────────────────────────────────────────────────── */
export function Tabs<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void;
  options: ReadonlyArray<{ value: T; label: ReactNode }>;
}) {
  return (
    <div className="tabs">
      {options.map((o) => (
        <button key={o.value} type="button"
          className={o.value === value ? "active" : ""}
          onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ── Disclosure ──────────────────────────────────────────────────────── */
export function Disclosure({ summary, children, open: defaultOpen }: {
  summary: ReactNode; children: ReactNode; open?: boolean;
}) {
  return (
    <details className="disc" open={defaultOpen}>
      <summary>{summary}</summary>
      <div className="disc-body">{children}</div>
    </details>
  );
}

/* ── CodeBlock ───────────────────────────────────────────────────────── */
export function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="code-block">
      <button className="code-copy" onClick={async () => {
        await navigator.clipboard.writeText(code).catch(() => {});
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      }}>{copied ? "✓" : "Copy"}</button>
      <pre><code>{code}</code></pre>
    </div>
  );
}

/* ── Latency badge ───────────────────────────────────────────────────── */
export function Latency({ ms }: { ms: number }) {
  const cls = ms < 800 ? "fast" : ms < 2500 ? "mid" : "slow";
  const display = ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
  return <span className={`latency ${cls}`}>⏱ {display}</span>;
}
