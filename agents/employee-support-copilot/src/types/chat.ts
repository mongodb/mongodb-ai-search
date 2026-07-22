/**
 * Shared TypeScript types for the chat API contract between BFF and frontend.
 */

import type { DomainKey } from "../lib/collections";

export interface ChatRequest {
  query: string;
  /** Optional: force a specific domain (used by the UI domain selector). */
  forceDomain?: DomainKey;
  topK?: number;
  filters?: Record<string, unknown>;
}

export interface CitationDTO {
  title: string;
  snippet: string;
  source?: string;
  page?: number | null;
  score?: number | null;
  domain: DomainKey;
  domainLabel: string;
  domainIcon: string;
  badgeColor: string;
}

export interface ChatResponse {
  answer: string;
  citations: CitationDTO[];
  routing: {
    domain: DomainKey | DomainKey[];
    domainLabel: string;
    confidence: number;
    ambiguous: boolean;
    isDualCollection: boolean;
    strategy: string;
    bias: string;
    matchedSignals: string[];
  };
  timings: {
    classificationMs: number;
    searchaasMs: number;
    totalMs: number;
  };
  hasSummary: boolean;
  error?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: ChatResponse;
  timestamp: Date;
}
