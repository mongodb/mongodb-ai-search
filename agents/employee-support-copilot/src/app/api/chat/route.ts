/**
 * POST /api/chat — BFF (Backend for Frontend) route.
 *
 * Flow:
 *   1. Validate and parse the incoming ChatRequest.
 *   2. Classify the query → domain + bias.
 *   3. If ambiguous → fan-out to both collections in parallel.
 *      Else → single-collection SearchaaS call.
 *   4. Assemble the answer and return a ChatResponse.
 *
 * SearchaaS is called with explicit atlas overrides on every request so
 * the backend never needs to be restarted when collection details change.
 */

import { NextRequest, NextResponse } from "next/server";
import { classifyQuery, rankedDomains } from "../../../lib/classifier";
import {
  buildPayload,
  callBothCollections,
  callSearchaaS,
} from "../../../lib/searchaas-client";
import {
  assembleDualCollection,
  assembleSingleCollection,
} from "../../../lib/assembler";
import { COLLECTION_CONFIGS, type DomainKey } from "../../../lib/collections";
import type { ChatRequest, ChatResponse, CitationDTO } from "../../../types/chat";

export const runtime = "nodejs"; // Needs fetch + AbortSignal.timeout

export async function POST(req: NextRequest): Promise<NextResponse<ChatResponse>> {
  const t0 = Date.now();

  // ------------------------------------------------------------------
  // 1. Parse request
  // ------------------------------------------------------------------
  let body: ChatRequest;
  try {
    body = (await req.json()) as ChatRequest;
  } catch {
    return NextResponse.json(
      { error: "Invalid JSON body", answer: "", citations: [], routing: {} as ChatResponse["routing"], timings: {} as ChatResponse["timings"], hasSummary: false },
      { status: 400 }
    );
  }

  const { query, forceDomain, topK = 8, filters = {} } = body;

  if (!query || typeof query !== "string" || !query.trim()) {
    return NextResponse.json(
      { error: "query is required", answer: "", citations: [], routing: {} as ChatResponse["routing"], timings: {} as ChatResponse["timings"], hasSummary: false },
      { status: 422 }
    );
  }

  // ------------------------------------------------------------------
  // 2. Classify
  // ------------------------------------------------------------------
  const t1 = Date.now();
  const classification = classifyQuery(query.trim());
  const classificationMs = Date.now() - t1;

  // forceDomain from UI overrides classifier output.
  const effectiveDomain: DomainKey = forceDomain ?? classification.domain;
  const ambiguous = !forceDomain && classification.ambiguous;

  console.log("[api/chat]", JSON.stringify({
    query: query.slice(0, 80),
    effectiveDomain,
    confidence: classification.confidence.toFixed(2),
    ambiguous,
    bias: classification.bias,
    forceDomain,
  }));

  // ------------------------------------------------------------------
  // 3. Retrieve
  // ------------------------------------------------------------------
  let assembled: Awaited<ReturnType<typeof assembleSingleCollection | typeof assembleDualCollection>>;

  if (ambiguous) {
    // Fan-out: query both collections in parallel, pick the better answer.
    const ranked = rankedDomains(classification.scores);
    const cols = ranked.map((d) => COLLECTION_CONFIGS[d]) as [typeof COLLECTION_CONFIGS[DomainKey], typeof COLLECTION_CONFIGS[DomainKey]];

    let dualResults: Awaited<ReturnType<typeof callBothCollections>>;
    try {
      dualResults = await callBothCollections(query.trim(), cols, topK);
    } catch (err) {
      console.error("[api/chat] dual-collection error", err);
      dualResults = [];
    }

    assembled = assembleDualCollection({
      results: dualResults.map((r, i) => ({
        response: r.response,
        latencyMs: r.latencyMs,
        collection: r.collection,
        domain: ranked[i],
      })),
      totalMs: Date.now() - t0,
    });
  } else {
    // Single-collection path.
    const collection = COLLECTION_CONFIGS[effectiveDomain];
    const payload = buildPayload({
      query: query.trim(),
      collection,
      topK,
      filters,
      bias: classification.bias,
    });

    let searchaasResult: Awaited<ReturnType<typeof callSearchaaS>>;
    try {
      searchaasResult = await callSearchaaS(payload);
    } catch (err: unknown) {
      const errMsg =
        err && typeof err === "object" && "message" in err
          ? String((err as { message: unknown }).message)
          : "SearchaaS request failed";

      // Graceful degradation: if this is not a forceDomain request, try the
      // other collection before giving up.
      if (!forceDomain) {
        const otherDomains = (Object.keys(COLLECTION_CONFIGS) as DomainKey[]).filter(
          (d) => d !== effectiveDomain
        );
        if (otherDomains.length > 0) {
          console.warn("[api/chat] primary collection failed — trying fallback:", otherDomains[0]);
          const fallbackCol = COLLECTION_CONFIGS[otherDomains[0]];
          const fallbackPayload = buildPayload({ query: query.trim(), collection: fallbackCol, topK, bias: "auto" });
          try {
            searchaasResult = await callSearchaaS(fallbackPayload);
          } catch {
            return errorResponse(errMsg, t0);
          }
        } else {
          return errorResponse(errMsg, t0);
        }
      } else {
        return errorResponse(errMsg, t0);
      }
    }

    assembled = assembleSingleCollection({
      response: searchaasResult.response,
      collection,
      domain: effectiveDomain,
      latencyMs: searchaasResult.latencyMs,
      totalMs: Date.now() - t0,
    });
  }

  // ------------------------------------------------------------------
  // 4. Build response
  // ------------------------------------------------------------------
  const citationsDTO: CitationDTO[] = assembled.citations.map((c) => ({
    ...c,
    domainIcon: COLLECTION_CONFIGS[c.domain].icon,
    badgeColor: COLLECTION_CONFIGS[c.domain].badgeColor,
  }));

  const routingDomain = assembled.isDualCollection
    ? assembled.collectionsUsed
    : assembled.collectionsUsed[0];

  const domainLabel = assembled.isDualCollection
    ? assembled.collectionsUsed.map((d) => COLLECTION_CONFIGS[d].label).join(" + ")
    : COLLECTION_CONFIGS[assembled.collectionsUsed[0] ?? effectiveDomain].label;

  const response: ChatResponse = {
    answer: assembled.answer,
    citations: citationsDTO,
    routing: {
      domain: routingDomain,
      domainLabel,
      confidence: classification.confidence,
      ambiguous: assembled.isDualCollection,
      isDualCollection: assembled.isDualCollection,
      strategy: assembled.strategy,
      bias: classification.bias,
      matchedSignals: classification.matchedSignals,
    },
    timings: {
      classificationMs,
      searchaasMs: assembled.timings.searchaasMs,
      totalMs: Date.now() - t0,
    },
    hasSummary: assembled.hasSummary,
  };

  return NextResponse.json(response);
}

function errorResponse(message: string, t0: number): NextResponse<ChatResponse> {
  return NextResponse.json(
    {
      answer:
        "I'm sorry — the search service is temporarily unavailable. " +
        "Please try again in a moment or contact support.",
      citations: [],
      routing: {
        domain: "employee_support" as DomainKey,
        domainLabel: "Unknown",
        confidence: 0,
        ambiguous: false,
        isDualCollection: false,
        strategy: "error",
        bias: "auto",
        matchedSignals: [],
      },
      timings: { classificationMs: 0, searchaasMs: 0, totalMs: Date.now() - t0 },
      hasSummary: false,
      error: message,
    },
    { status: 503 }
  );
}
