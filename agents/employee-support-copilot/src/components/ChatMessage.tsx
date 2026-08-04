"use client";

import type { ChatMessage as ChatMessageType } from "../types/chat";
import { COLLECTION_CONFIGS, type DomainKey } from "../lib/collections";
import CitationCard from "./CitationCard";

interface Props {
  message: ChatMessageType;
}

function DomainBadge({ domain, label }: { domain: DomainKey | DomainKey[]; label: string }) {
  const domains = Array.isArray(domain) ? domain : [domain];
  const firstCfg = COLLECTION_CONFIGS[domains[0]];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${firstCfg.badgeColor}`}
    >
      {firstCfg.icon} {label}
    </span>
  );
}

function StrategyBadge({ strategy }: { strategy: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-600">
      🔍 {strategy}
    </span>
  );
}

function MarkdownAnswer({ text }: { text: string }) {
  // Minimal markdown rendering — headings, bold, blockquote, horizontal rule.
  const lines = text.split("\n");
  const elements: React.ReactNode[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (line.startsWith("### ")) {
      elements.push(<h3 key={i} className="mt-4 mb-1 text-sm font-semibold text-gray-900">{line.slice(4)}</h3>);
    } else if (line.startsWith("## ")) {
      elements.push(<h2 key={i} className="mt-4 mb-1 text-base font-semibold text-gray-900">{line.slice(3)}</h2>);
    } else if (line.startsWith("> ")) {
      elements.push(
        <blockquote key={i} className="border-l-4 border-blue-300 pl-3 text-gray-600 italic my-1">
          {applyInline(line.slice(2))}
        </blockquote>
      );
    } else if (line === "---") {
      elements.push(<hr key={i} className="my-3 border-gray-200" />);
    } else if (line.trim() === "") {
      elements.push(<div key={i} className="h-2" />);
    } else {
      elements.push(<p key={i} className="text-sm text-gray-800 leading-relaxed">{applyInline(line)}</p>);
    }
  }
  return <>{elements}</>;
}

function applyInline(text: string): React.ReactNode {
  // Bold: **text**
  const parts = text.split(/(\*\*[^*]+\*\*|_[^_]+_)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("_") && part.endsWith("_")) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    return part;
  });
}

export default function ChatMessage({ message }: Props) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-xl rounded-2xl rounded-tr-sm bg-blue-600 px-4 py-3 shadow-sm">
          <p className="text-sm text-white">{message.content}</p>
        </div>
      </div>
    );
  }

  // Assistant message
  const r = message.response;
  if (!r) {
    return (
      <div className="flex justify-start mb-4">
        <div className="max-w-2xl rounded-2xl rounded-tl-sm bg-white border border-gray-200 px-4 py-3 shadow-sm">
          <p className="text-sm text-gray-800">{message.content}</p>
        </div>
      </div>
    );
  }

  const isError = !!r.error;

  return (
    <div className="flex justify-start mb-6">
      <div className="w-full max-w-2xl rounded-2xl rounded-tl-sm bg-white border border-gray-200 shadow-sm overflow-hidden">
        {/* Routing header */}
        {!isError && (
          <div className="flex flex-wrap items-center gap-2 px-4 pt-3 pb-2 border-b border-gray-100 bg-gray-50">
            <DomainBadge domain={r.routing.domain} label={r.routing.domainLabel} />
            <StrategyBadge strategy={r.routing.strategy} />
            {r.routing.isDualCollection && (
              <span className="text-xs text-gray-500 italic">· dual-collection</span>
            )}
            <span className="ml-auto text-xs text-gray-400">
              {r.timings.totalMs}ms
            </span>
          </div>
        )}

        {/* Answer */}
        <div className={`px-4 py-3 ${isError ? "bg-red-50" : ""}`}>
          {isError ? (
            <p className="text-sm text-red-700">{r.answer}</p>
          ) : (
            <MarkdownAnswer text={r.answer} />
          )}
        </div>

        {/* Citations */}
        {r.citations.length > 0 && (
          <div className="border-t border-gray-100 px-4 pb-4 pt-2">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              Sources
            </p>
            <div className="space-y-2">
              {r.citations.map((c, i) => (
                <CitationCard key={i} citation={c} index={i + 1} />
              ))}
            </div>
          </div>
        )}

        {/* Debug: confidence + signals (collapsible) */}
        {!isError && (
          <details className="border-t border-gray-100">
            <summary className="px-4 py-1.5 text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none">
              Debug info
            </summary>
            <div className="px-4 pb-3 text-xs text-gray-500 space-y-1">
              <p>Confidence: <span className="font-mono">{(r.routing.confidence * 100).toFixed(0)}%</span></p>
              <p>Bias: <span className="font-mono">{r.routing.bias}</span></p>
              <p>Classification: <span className="font-mono">{r.routing.matchedSignals.slice(0, 5).join(", ") || "none"}</span></p>
              <p>Timings: <span className="font-mono">classify={r.timings.classificationMs}ms searchaas={r.timings.searchaasMs}ms total={r.timings.totalMs}ms</span></p>
            </div>
          </details>
        )}
      </div>
    </div>
  );
}
