/**
 * classifier.ts — Query intent classifier and collection router.
 *
 * Responsibilities:
 *   - Score each query against DOMAIN_SIGNALS keyword/pattern lists.
 *   - Emit a confidence-ranked list of DomainKeys.
 *   - Decide the retrieval bias (vector-heavy / fulltext-heavy / auto).
 *   - Flag ambiguous queries that should fan-out to both collections.
 *
 * This is a lightweight rule-based classifier. It runs in < 1 ms and
 * requires no external API call. Replace the scoring logic with an LLM
 * call (e.g. via the SearchaaS MCP auto_search tool) if higher accuracy
 * is needed in production.
 */

import {
  ALL_DOMAINS,
  DOMAIN_SIGNALS,
  FULLTEXT_HEAVY_PATTERNS,
  VECTOR_HEAVY_PATTERNS,
  type DomainKey,
} from "./collections";
import type { RetrievalBias } from "./searchaas-client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ClassificationResult {
  /** Best-matching domain. */
  domain: DomainKey;
  /** Confidence 0–1 for the winning domain. */
  confidence: number;
  /** Ranked scores for all domains (for diagnostics / dual-collection path). */
  scores: Record<DomainKey, number>;
  /** Suggested retrieval strategy bias. */
  bias: RetrievalBias;
  /** True when confidence is too low — trigger dual-collection fan-out. */
  ambiguous: boolean;
  /** Matched signal terms (for logging / debug). */
  matchedSignals: string[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Below this confidence we fan-out to both collections. */
const AMBIGUITY_THRESHOLD = 0.55;

/** Weight applied to a full pattern match (regex). */
const PATTERN_WEIGHT = 3;

/** Weight applied to a bare keyword hit. */
const KEYWORD_WEIGHT = 1;

// ---------------------------------------------------------------------------
// Core scoring
// ---------------------------------------------------------------------------

function scoreQuery(query: string, domain: DomainKey): { score: number; matched: string[] } {
  const q = query.toLowerCase();
  const signals = DOMAIN_SIGNALS[domain];
  let score = 0;
  const matched: string[] = [];

  for (const pattern of signals.patterns) {
    if (pattern.test(q)) {
      score += PATTERN_WEIGHT;
      matched.push(pattern.source);
    }
  }

  for (const kw of signals.keywords) {
    // Whole-word match to avoid partial hits ("policy" ≠ "employee").
    if (new RegExp(`\\b${kw}\\b`).test(q)) {
      score += KEYWORD_WEIGHT;
      matched.push(kw);
    }
  }

  return { score, matched };
}

function detectBias(query: string): RetrievalBias {
  if (VECTOR_HEAVY_PATTERNS.some((p) => p.test(query))) return "vector-heavy";
  if (FULLTEXT_HEAVY_PATTERNS.some((p) => p.test(query))) return "fulltext-heavy";
  return "auto";
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Classify a user query into a domain and decide retrieval parameters.
 *
 * @example
 * classifyQuery("VPN is not connecting on my Mac")
 * // { domain: "IT_helpdesk", confidence: 0.9, ambiguous: false, bias: "auto" }
 *
 * classifyQuery("How do I get remote access while travelling internationally?")
 * // { domain: "IT_helpdesk", confidence: 0.61, ambiguous: false, bias: "vector-heavy" }
 *
 * classifyQuery("I need help today")
 * // { domain: "IT_helpdesk", confidence: 0.5, ambiguous: true, bias: "auto" }
 */
export function classifyQuery(query: string): ClassificationResult {
  const allScores = {} as Record<DomainKey, number>;
  const allMatched: string[] = [];

  for (const domain of ALL_DOMAINS) {
    const { score, matched } = scoreQuery(query, domain);
    allScores[domain] = score;
    allMatched.push(...matched.map((m) => `${domain}:${m}`));
  }

  // Normalise scores to [0,1] confidence.
  const total = Object.values(allScores).reduce((a, b) => a + b, 0);

  let domain: DomainKey;
  let confidence: number;

  if (total === 0) {
    // No signals at all — default to employee_support (most general domain)
    // with low confidence to trigger fan-out.
    domain = "employee_support";
    confidence = 0.5;
  } else {
    // Pick the highest-scoring domain.
    domain = (Object.entries(allScores) as [DomainKey, number][])
      .sort((a, b) => b[1] - a[1])[0][0];
    confidence = allScores[domain] / total;
  }

  const bias = detectBias(query);
  const ambiguous = confidence < AMBIGUITY_THRESHOLD;

  console.log("[classifier]", JSON.stringify({
    query: query.slice(0, 80),
    domain,
    confidence: confidence.toFixed(2),
    scores: allScores,
    bias,
    ambiguous,
  }));

  return {
    domain,
    confidence,
    scores: allScores,
    bias,
    ambiguous,
    matchedSignals: allMatched,
  };
}

/**
 * Return the two domains ranked by score — used when fanning out.
 */
export function rankedDomains(scores: Record<DomainKey, number>): DomainKey[] {
  return (Object.entries(scores) as [DomainKey, number][])
    .sort((a, b) => b[1] - a[1])
    .map(([d]) => d);
}
