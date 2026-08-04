/**
 * assembler.ts — Response formatter and answer assembler.
 *
 * Responsibilities:
 *   - Turn raw SearchaaS chunks into employee-friendly answers.
 *   - Merge dual-collection results when both were queried.
 *   - Format citations/sources.
 *   - Generate fallback messages when no useful results are found.
 *
 * This file deliberately has no retrieval logic — it only shapes output.
 */

import type { SearchaaSChunk, SearchaaSResponse } from "./searchaas-client";
import type { CollectionConfig, DomainKey } from "./collections";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Citation {
  title: string;
  snippet: string;
  source?: string;
  page?: number | null;
  score?: number | null;
  domain: DomainKey;
  domainLabel: string;
}

export interface AssembledAnswer {
  /** Markdown-formatted answer text. */
  answer: string;
  /** Structured citations shown in the UI. */
  citations: Citation[];
  /** True when SearchaaS returned a LLM-generated summary. */
  hasSummary: boolean;
  /** True when the answer was assembled from two collections. */
  isDualCollection: boolean;
  /** Collection(s) that were queried. */
  collectionsUsed: DomainKey[];
  /** Strategy reported by SearchaaS. */
  strategy: string;
  /** Timing breakdown. */
  timings: {
    searchaasMs: number;
    totalMs: number;
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MIN_SCORE_THRESHOLD = 0.1; // chunks below this score are considered noise
const MAX_CITATIONS = 5;

function extractSource(metadata: Record<string, unknown>): string | undefined {
  return (
    (metadata.source as string) ||
    (metadata.url as string) ||
    (metadata.file_name as string) ||
    (metadata.document_id as string) ||
    (metadata.filename as string) ||
    undefined
  );
}

function extractTitle(metadata: Record<string, unknown>): string {
  return (
    (metadata.title as string) ||
    (metadata.document_title as string) ||
    (metadata.heading as string) ||
    "Document"
  );
}

function extractPage(metadata: Record<string, unknown>): number | null {
  const p = metadata.page ?? metadata.page_number ?? null;
  return typeof p === "number" ? p : null;
}

function chunkToCitation(
  chunk: SearchaaSChunk,
  domain: DomainKey,
  domainLabel: string
): Citation {
  return {
    title: extractTitle(chunk.metadata),
    snippet: chunk.content.slice(0, 300).trim() + (chunk.content.length > 300 ? "…" : ""),
    source: extractSource(chunk.metadata),
    page: extractPage(chunk.metadata),
    score: chunk.score ?? null,
    domain,
    domainLabel,
  };
}

function qualityScore(results: SearchaaSChunk[]): number {
  if (!results.length) return 0;
  const topScore = results[0].score ?? 0;
  return topScore;
}

// ---------------------------------------------------------------------------
// Single-collection assembly
// ---------------------------------------------------------------------------

export function assembleSingleCollection(opts: {
  response: SearchaaSResponse;
  collection: CollectionConfig;
  domain: DomainKey;
  latencyMs: number;
  totalMs: number;
}): AssembledAnswer {
  const { response, collection, domain, latencyMs, totalMs } = opts;
  const results = response.results ?? [];

  // Filter out very-low-score chunks.
  const usable = results.filter(
    (r) => r.score === null || r.score === undefined || r.score >= MIN_SCORE_THRESHOLD
  );

  const citations = usable
    .slice(0, MAX_CITATIONS)
    .map((r) => chunkToCitation(r, domain, collection.label));

  let answer: string;
  const hasSummary = !!(response.summary && response.summary.trim());

  if (hasSummary) {
    // SearchaaS returned an LLM summary — use it directly.
    answer = response.summary!.trim();
  } else if (usable.length === 0) {
    answer = buildFallback(domain);
  } else {
    // Synthesise a minimal answer from the top chunk.
    const top = usable[0];
    answer =
      `Based on the **${collection.label}** knowledge base:\n\n` +
      `> ${top.content.slice(0, 500).trim()}` +
      (top.content.length > 500 ? "…" : "") +
      (usable.length > 1
        ? `\n\n_${usable.length - 1} additional source(s) found — see citations below._`
        : "");
  }

  return {
    answer,
    citations,
    hasSummary,
    isDualCollection: false,
    collectionsUsed: [domain],
    strategy: response.strategy,
    timings: { searchaasMs: latencyMs, totalMs },
  };
}

// ---------------------------------------------------------------------------
// Dual-collection merge
// ---------------------------------------------------------------------------

export function assembleDualCollection(opts: {
  results: Array<{
    response: SearchaaSResponse;
    latencyMs: number;
    collection: CollectionConfig;
    domain: DomainKey;
  }>;
  totalMs: number;
}): AssembledAnswer {
  const { results, totalMs } = opts;

  if (results.length === 0) {
    return buildEmptyAnswer(totalMs);
  }

  // Pick the collection with the higher top-result score.
  const ranked = results
    .filter((r) => (r.response.results?.length ?? 0) > 0)
    .sort((a, b) => qualityScore(b.response.results) - qualityScore(a.response.results));

  const best = ranked[0];
  const rest = ranked.slice(1);

  const allCitations: Citation[] = [];

  if (best) {
    const usable = (best.response.results ?? []).filter(
      (r) => r.score === null || r.score === undefined || r.score >= MIN_SCORE_THRESHOLD
    );
    allCitations.push(
      ...usable.slice(0, 3).map((r) => chunkToCitation(r, best.domain, best.collection.label))
    );
  }

  for (const r of rest) {
    const usable = (r.response.results ?? []).filter(
      (chunk) => chunk.score === null || chunk.score === undefined || chunk.score >= MIN_SCORE_THRESHOLD
    );
    allCitations.push(
      ...usable.slice(0, 2).map((chunk) => chunkToCitation(chunk, r.domain, r.collection.label))
    );
  }

  const sectionParts: string[] = [];

  for (const r of results) {
    const usable = (r.response.results ?? []).filter(
      (chunk) => chunk.score === null || chunk.score === undefined || chunk.score >= MIN_SCORE_THRESHOLD
    );
    if (usable.length === 0) continue;

    const hasSummary = !!(r.response.summary?.trim());
    const text = hasSummary
      ? r.response.summary!.trim()
      : `> ${usable[0].content.slice(0, 400).trim()}` + (usable[0].content.length > 400 ? "…" : "");

    sectionParts.push(`### ${r.collection.icon} ${r.collection.label}\n\n${text}`);
  }

  const answer =
    sectionParts.length > 0
      ? `Your question touches multiple domains. Here's what I found:\n\n` +
        sectionParts.join("\n\n---\n\n")
      : buildFallback();

  const maxLatency = Math.max(...results.map((r) => r.latencyMs));

  return {
    answer,
    citations: allCitations.slice(0, MAX_CITATIONS),
    hasSummary: results.some((r) => !!r.response.summary?.trim()),
    isDualCollection: true,
    collectionsUsed: results.map((r) => r.domain),
    strategy: results.map((r) => r.response.strategy).join(" + "),
    timings: { searchaasMs: maxLatency, totalMs },
  };
}

// ---------------------------------------------------------------------------
// Fallback helpers
// ---------------------------------------------------------------------------

function buildFallback(domain?: DomainKey): string {
  if (domain === "IT_helpdesk") {
    return (
      "I couldn't find a specific answer in the IT Helpdesk knowledge base for your question. " +
      "Please try rephrasing, or contact IT Support directly via the helpdesk portal."
    );
  }
  if (domain === "employee_support") {
    return (
      "I couldn't find a specific HR or Employee Support answer for your question. " +
      "Please try rephrasing, or reach out to the People team at hr@company.com."
    );
  }
  return (
    "I couldn't find a relevant answer in either knowledge base. " +
    "Please try rephrasing your question, or contact the appropriate support team."
  );
}

function buildEmptyAnswer(totalMs: number): AssembledAnswer {
  return {
    answer: buildFallback(),
    citations: [],
    hasSummary: false,
    isDualCollection: true,
    collectionsUsed: [],
    strategy: "none",
    timings: { searchaasMs: 0, totalMs },
  };
}
