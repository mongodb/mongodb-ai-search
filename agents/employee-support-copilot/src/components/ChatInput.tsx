"use client";

import { useState, useRef, type KeyboardEvent } from "react";
import { COLLECTION_CONFIGS, type DomainKey } from "../lib/collections";

interface Props {
  onSend: (query: string, forceDomain?: DomainKey) => void;
  disabled?: boolean;
}

const SUGGESTIONS: { label: string; query: string }[] = [
  { label: "VPN issue", query: "My VPN is not connecting on my Mac. How do I fix it?" },
  { label: "Leave policy", query: "What is the leave policy for new joiners?" },
  { label: "Password reset", query: "How do I reset my company password?" },
  { label: "Travel reimbursement", query: "What is the travel reimbursement cap for domestic trips?" },
  { label: "Remote work", query: "How do I get remote access while travelling internationally?" },
  { label: "Payroll FAQ", query: "When is the payroll processed each month?" },
];

export default function ChatInput({ onSend, disabled }: Props) {
  const [query, setQuery] = useState("");
  const [forceDomain, setForceDomain] = useState<DomainKey | "auto">("auto");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function handleSend() {
    const q = query.trim();
    if (!q || disabled) return;
    onSend(q, forceDomain === "auto" ? undefined : forceDomain);
    setQuery("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleInput() {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  }

  return (
    <div className="border-t border-gray-200 bg-white px-4 pt-3 pb-4">
      {/* Suggestion chips */}
      <div className="flex flex-wrap gap-1.5 mb-3">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.label}
            onClick={() => { setQuery(s.query); textareaRef.current?.focus(); }}
            className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-colors"
          >
            {s.label}
          </button>
        ))}
      </div>

      <div className="flex gap-2 items-end">
        {/* Domain selector */}
        <select
          value={forceDomain}
          onChange={(e) => setForceDomain(e.target.value as DomainKey | "auto")}
          className="h-10 rounded-xl border border-gray-200 bg-gray-50 px-2 text-xs text-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-300 flex-shrink-0"
          title="Force routing to a specific domain"
        >
          <option value="auto">🤖 Auto-route</option>
          {(Object.entries(COLLECTION_CONFIGS) as [DomainKey, typeof COLLECTION_CONFIGS[DomainKey]][]).map(
            ([key, cfg]) => (
              <option key={key} value={key}>
                {cfg.icon} {cfg.label}
              </option>
            )
          )}
        </select>

        {/* Text input */}
        <textarea
          ref={textareaRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          placeholder="Ask me about IT issues, HR policies, leave, payroll…"
          rows={1}
          disabled={disabled}
          className="flex-1 resize-none rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-300 disabled:opacity-50 leading-relaxed"
          style={{ minHeight: "40px", maxHeight: "160px" }}
        />

        {/* Send button */}
        <button
          onClick={handleSend}
          disabled={disabled || !query.trim()}
          className="h-10 w-10 flex-shrink-0 rounded-xl bg-blue-600 text-white flex items-center justify-center hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          aria-label="Send"
        >
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
            <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
          </svg>
        </button>
      </div>

      <p className="mt-1.5 text-[10px] text-gray-400 text-center">
        Shift+Enter for new line · Enter to send · Powered by SearchaaS + MongoDB Atlas
      </p>
    </div>
  );
}
