"""
FastMCP surface — publishes Phase 1 retrieval strategies as MCP tools.

Each tool maps one-to-one to a strategy and reuses the identical bootstrap
container as the FastAPI layer, guaranteeing parity between REST and MCP.
"""
from __future__ import annotations

import os
import time
from typing import Any

from fastmcp import FastMCP

from searchaas.app.bootstrap import get_container
from searchaas.config import load_config
from searchaas.observability import configure_logging, get_logger
from searchaas.utils import clamp_auto_strategy, serialize_docs, summarize

configure_logging()
log = get_logger("searchaas.mcp")

# Read embedding key once — load_config() is lru_cached but reading the key
# here avoids the attribute lookup inside every serialize_docs() call.
_EMB_KEY = load_config().atlas.embedding_key

mcp = FastMCP("searchaas")


def _retrieve(
    strategy: str,
    query: str,
    top_k: int = 20,
    filters: dict | None = None,
    atlas: dict | None = None,
    retrieval: dict | None = None,
) -> dict[str, Any]:
    """Run a single strategy and return a full response dict (matching auto_search shape).

    Includes a best-effort AI summary so MCP clients (and the React UI) get
    parity with the FastAPI `/retrieve/*` endpoints.
    """
    from searchaas.api.app import AtlasOverrides, RetrievalOverrides
    c = get_container()
    overrides = AtlasOverrides(**{k: v for k, v in atlas.items() if v is not None}) if atlas else None
    ret_overrides = RetrievalOverrides(**{k: v for k, v in retrieval.items() if v is not None}) if retrieval else None
    if overrides:
        log.info("MCP retrieve: UI atlas overrides=%s", overrides.model_dump(exclude_none=True))
    if ret_overrides:
        log.info("MCP retrieve: retrieval overrides=%s", ret_overrides.model_dump(exclude_none=True))

    t_total = time.perf_counter()

    t_plan = time.perf_counter()
    plan = c.planner.plan_for(strategy=strategy, query=query, top_k=top_k, filters=filters or {})
    planning_ms = round((time.perf_counter() - t_plan) * 1000, 1)

    t_uq = time.perf_counter()
    uq = c.understanding.process(query)
    understanding_ms = round((time.perf_counter() - t_uq) * 1000, 1)

    t_mongo = time.perf_counter()
    docs = c.retrievers.create(plan, overrides=overrides, retrieval_overrides=ret_overrides).invoke(query)
    mongo_ms = round((time.perf_counter() - t_mongo) * 1000, 1)

    emb_key = (overrides and overrides.embedding_key) or _EMB_KEY
    results = serialize_docs(docs, emb_key, include_score=True)

    t_sum = time.perf_counter()
    summary = summarize(c.llm, query, results)
    summarize_ms = round((time.perf_counter() - t_sum) * 1000, 1) if summary is not None else None

    total_ms = round((time.perf_counter() - t_total) * 1000, 1)

    if summary is not None:
        log.info("MCP %s_search: summary_chars=%s", strategy, len(summary))
    log.info(
        "MCP %s_search: results=%s mongo_ms=%s understanding_ms=%s planning_ms=%s summarize_ms=%s total_ms=%s",
        strategy, len(results), mongo_ms, understanding_ms, planning_ms, summarize_ms, total_ms,
    )

    return {
        "strategy": plan.strategy,
        "plan": plan.model_dump(),
        "results": results,
        "understood_query": {
            "raw": uq.raw,
            "corrected": uq.corrected,
            "rewritten": uq.rewritten,
            "entities": list(uq.entities or []),
            "metadata_filters": dict(uq.metadata_filters or {}),
            "intent": uq.intent,
        },
        "summary": summary,
        "timings": {
            "mongo_ms": mongo_ms,
            "planning_ms": planning_ms,
            "understanding_ms": understanding_ms,
            "summarize_ms": summarize_ms,
            "total_ms": total_ms,
        },
    }


@mcp.tool
def vector_search(query: str, top_k: int = 20, filters: dict | None = None, atlas: dict | None = None, retrieval: dict | None = None) -> dict[str, Any]:
    """Semantic retrieval via Atlas Vector Search (with AI summary)."""
    return _retrieve("vector", query, top_k, filters, atlas, retrieval)


@mcp.tool
def fulltext_search(query: str, top_k: int = 20, filters: dict | None = None, atlas: dict | None = None, retrieval: dict | None = None) -> dict[str, Any]:
    """Keyword / lexical retrieval via Atlas Search (with AI summary)."""
    return _retrieve("fulltext", query, top_k, filters, atlas, retrieval)


@mcp.tool
def hybrid_search(query: str, top_k: int = 20, filters: dict | None = None, atlas: dict | None = None, retrieval: dict | None = None) -> dict[str, Any]:
    """Fused vector + full-text retrieval (with AI summary)."""
    return _retrieve("hybrid", query, top_k, filters, atlas, retrieval)


@mcp.tool
def graph_search(query: str, top_k: int = 20, filters: dict | None = None, atlas: dict | None = None, retrieval: dict | None = None) -> dict[str, Any]:
    """Multi-hop graph retrieval via $graphLookup / GraphRAG (with AI summary)."""
    return _retrieve("graph", query, top_k, filters, atlas, retrieval)


@mcp.tool
def parent_doc_search(query: str, top_k: int = 20, filters: dict | None = None, atlas: dict | None = None, retrieval: dict | None = None) -> dict[str, Any]:
    """Child-chunk match that returns the parent document (with AI summary)."""
    return _retrieve("parent_doc", query, top_k, filters, atlas, retrieval)


@mcp.tool
def auto_search(query: str, top_k: int = 20, filters: dict | None = None, atlas: dict | None = None, retrieval: dict | None = None) -> dict[str, Any]:
    """
    Auto mode: understand the query, plan a strategy, retrieve, and summarize.
    Strategy is clamped to {hybrid, fulltext, vector} for end-user execution.
    """
    from searchaas.api.app import AtlasOverrides, RetrievalOverrides
    c = get_container()
    overrides     = AtlasOverrides(**{k: v for k, v in atlas.items() if v is not None}) if atlas else None
    ret_overrides = RetrievalOverrides(**{k: v for k, v in retrieval.items() if v is not None}) if retrieval else None
    if overrides:
        log.info("auto_search: UI atlas overrides=%s", overrides.model_dump(exclude_none=True))
    if ret_overrides:
        log.info("auto_search: retrieval overrides=%s", ret_overrides.model_dump(exclude_none=True))

    t_total = time.perf_counter()

    t_uq = time.perf_counter()
    uq = c.understanding.process(query)
    understanding_ms = round((time.perf_counter() - t_uq) * 1000, 1)

    t_plan = time.perf_counter()
    plan = c.planner.plan(uq)
    planning_ms = round((time.perf_counter() - t_plan) * 1000, 1)

    original = plan.strategy
    plan.strategy = clamp_auto_strategy(plan.strategy, uq.intent)
    if plan.strategy != original:
        log.info("auto_search: clamped %r -> %r (intent=%s)", original, plan.strategy, uq.intent)

    if top_k:
        plan.top_k = top_k
    if filters:
        plan.filters = {**plan.filters, **filters}

    t_mongo = time.perf_counter()
    docs = c.retrievers.create(plan, overrides=overrides, retrieval_overrides=ret_overrides).invoke(uq.rewritten)
    mongo_ms = round((time.perf_counter() - t_mongo) * 1000, 1)

    emb_key = (overrides and overrides.embedding_key) or _EMB_KEY
    results = serialize_docs(docs, emb_key, include_score=False)

    t_sum = time.perf_counter()
    summary = summarize(c.llm, uq.rewritten, results)
    summarize_ms = round((time.perf_counter() - t_sum) * 1000, 1)

    total_ms = round((time.perf_counter() - t_total) * 1000, 1)

    log.info(
        "MCP auto_search: results=%s mongo_ms=%s understanding_ms=%s planning_ms=%s summarize_ms=%s total_ms=%s",
        len(results), mongo_ms, understanding_ms, planning_ms, summarize_ms, total_ms,
    )

    return {
        "strategy": plan.strategy,
        "plan": plan.model_dump(),
        "results": results,
        "understood_query": {
            "raw": uq.raw,
            "corrected": uq.corrected,
            "rewritten": uq.rewritten,
            "entities": list(uq.entities or []),
            "metadata_filters": dict(uq.metadata_filters or {}),
            "intent": uq.intent,
        },
        "summary": summary,
        "timings": {
            "mongo_ms": mongo_ms,
            "planning_ms": planning_ms,
            "understanding_ms": understanding_ms,
            "summarize_ms": summarize_ms,
            "total_ms": total_ms,
        },
    }


def main() -> None:
    cfg = load_config().server

    # Warm the container before accepting connections so the first MCP call
    # pays no cold-start penalty.
    log.info("MCP: warming container…")
    get_container()
    log.info("MCP: container ready")

    if cfg.mcp_transport in ("streamable-http", "http", "sse"):
        import uvicorn
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware as StarlettesCORS

        import os as _os
        _extra = [o.strip() for o in _os.environ.get("SEARCHAAS_CORS_ORIGINS", "").split(",") if o.strip()]
        if "*" in _extra:
            log.warning("MCP CORS: allowing ALL origins (SEARCHAAS_CORS_ORIGINS=*)")
            cors = Middleware(
                StarlettesCORS,
                allow_origins=["*"],
                allow_credentials=False,
                allow_methods=["*"],
                allow_headers=["*"],
                expose_headers=["mcp-session-id"],
            )
        else:
            log.info("MCP CORS: localhost + extra=%s", _extra)
            cors = Middleware(
                StarlettesCORS,
                allow_origins=_extra,
                allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
                expose_headers=["mcp-session-id"],
            )
        app = mcp.http_app(transport=cfg.mcp_transport, middleware=[cors])

        # Plain HTTP healthcheck for load balancers (ECS Express Mode ALB,
        # ALB target groups, etc.). The MCP endpoint itself requires an SSE
        # handshake and won't return 200 on a plain GET.
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route

        async def _healthz(_request):
            return PlainTextResponse("ok")

        app.router.routes.append(Route("/healthz", _healthz, methods=["GET"]))

        uvicorn.run(app, host=cfg.mcp_host, port=cfg.mcp_port,
                    log_level=cfg.log_level)
    else:
        mcp.run(transport=cfg.mcp_transport, host=cfg.mcp_host, port=cfg.mcp_port)


if __name__ == "__main__":
    main()
