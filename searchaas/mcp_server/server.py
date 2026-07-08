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
from searchaas.utils import clamp_auto_strategy, filter_by_entities, serialize_docs, summarize

configure_logging()
log = get_logger("searchaas.mcp")

# Read embedding key once — load_config() is lru_cached but reading the key
# here avoids the attribute lookup inside every serialize_docs() call.
_EMB_KEY = load_config().atlas.embedding_key

mcp = FastMCP("searchaas")


def _build_response(
    plan: Any,
    invoke_query: str,
    uq: Any,
    overrides: Any,
    ret_overrides: Any,
    planning_ms: float,
    understanding_ms: float,
    t_total: float,
    label: str,
) -> dict[str, Any]:
    """Shared tail: retrieve → serialize → summarize → build MCP response dict."""
    import re as _re
    c = get_container()
    emb_key = (overrides and overrides.embedding_key) or _EMB_KEY

    t_mongo = time.perf_counter()
    try:
        docs = c.retrievers.create(
            plan, overrides=overrides, retrieval_overrides=ret_overrides
        ).invoke(invoke_query)
    except Exception as exc:
        # Atlas rejects $vectorSearch pre-filters on paths not declared as
        # {type: filter} in the index. Evict the bad field and retry once
        # without filters so the MCP tool returns results rather than raising.
        err_str = str(exc)
        if "needs to be indexed as filter" in err_str and plan.filters:
            bad = _re.search(r"Path '([^']+)' needs to be indexed", err_str)
            if bad:
                c.retrievers.evict_filter_field(bad.group(1))
            log.warning(
                "MCP %s: pre-filter %s rejected by Atlas (not indexed as filter) — "
                "retrying without filters. Error: %s",
                label, list(plan.filters.keys()), exc,
            )
            plan.filters = {}
            docs = c.retrievers.create(
                plan, overrides=overrides, retrieval_overrides=ret_overrides
            ).invoke(invoke_query)
        else:
            raise
    mongo_ms = round((time.perf_counter() - t_mongo) * 1000, 1)

    results = serialize_docs(docs, emb_key, include_score=True)
    results = filter_by_entities(results, list(uq.entities or []))

    t_sum = time.perf_counter()
    _enable_sum = get_container().config.planner.enable_summarization
    summary = summarize(c.llm, invoke_query, results) if _enable_sum else None
    summarize_ms = round((time.perf_counter() - t_sum) * 1000, 1) if summary is not None else None

    total_ms = round((time.perf_counter() - t_total) * 1000, 1)
    log.info(
        "MCP %s: results=%s mongo_ms=%s understanding_ms=%s planning_ms=%s summarize_ms=%s total_ms=%s",
        label, len(results), mongo_ms, understanding_ms, planning_ms, summarize_ms, total_ms,
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


def _retrieve(
    strategy: str,
    query: str,
    top_k: int = 20,
    filters: dict | None = None,
    atlas: dict | None = None,
    retrieval: dict | None = None,
) -> dict[str, Any]:
    """Run a fixed strategy and return a full MCP response dict."""
    from searchaas.api.app import AtlasOverrides, RetrievalOverrides
    c = get_container()
    overrides = AtlasOverrides(**{k: v for k, v in atlas.items() if v is not None}) if atlas else None
    ret_overrides = RetrievalOverrides(**{k: v for k, v in retrieval.items() if v is not None}) if retrieval else None
    if overrides:
        log.info("MCP retrieve: UI atlas overrides=%s", overrides.model_dump(exclude_none=True))
    if ret_overrides:
        log.info("MCP retrieve: retrieval overrides=%s", ret_overrides.model_dump(exclude_none=True))

    t_total = time.perf_counter()

    t_uq = time.perf_counter()
    uq = c.understanding.process(query)
    understanding_ms = round((time.perf_counter() - t_uq) * 1000, 1)

    # Merge NLU-extracted metadata filters with explicit request filters.
    merged_filters = {**(uq.metadata_filters or {}), **(filters or {})}

    t_plan = time.perf_counter()
    plan = c.planner.plan_for(strategy=strategy, query=uq.rewritten, top_k=top_k, filters=merged_filters)
    planning_ms = round((time.perf_counter() - t_plan) * 1000, 1)

    return _build_response(plan, uq.rewritten, uq, overrides, ret_overrides, planning_ms, understanding_ms, t_total, strategy)


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
def metadata_search(query: str, top_k: int = 20, filters: dict | None = None, atlas: dict | None = None, retrieval: dict | None = None) -> dict[str, Any]:
    """Structured retrieval ($match/$sort/$limit) for rankings, superlatives, and exact lookups."""
    return _retrieve("metadata", query, top_k, filters, atlas, retrieval)


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

    if top_k is not None:
        plan.top_k = top_k
    if filters:
        plan.filters = {**plan.filters, **filters}

    return _build_response(plan, uq.rewritten, uq, overrides, ret_overrides, planning_ms, understanding_ms, t_total, "auto_search")


def main() -> None:
    cfg = load_config().server

    if cfg.mcp_transport in ("streamable-http", "http", "sse"):
        import threading
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

        # Plain HTTP healthcheck for load balancers (Cloud Run, ALB, etc.).
        from starlette.responses import JSONResponse, PlainTextResponse
        from starlette.routing import Route

        async def _healthz(_request):
            return PlainTextResponse("ok")

        async def _settings(_request):
            """Mirror of FastAPI GET /settings — lets the UI load live config
            when only the MCP server is running (FastAPI not started)."""
            from searchaas.utils import redact_cfg
            c = get_container()
            cfg_c = c.config
            return JSONResponse({
                "atlas": {
                    "uri":                "***",
                    "database":           cfg_c.atlas.database,
                    "collection":         cfg_c.atlas.collection,
                    "vector_index":       cfg_c.atlas.vector_index,
                    "search_index":       cfg_c.atlas.search_index,
                    "text_key":           cfg_c.atlas.text_key,
                    "embedding_key":      cfg_c.atlas.embedding_key,
                    "relevance_score_fn": cfg_c.atlas.relevance_score_fn,
                    "dimensions":         cfg_c.atlas.dimensions,
                },
                "embeddings": {
                    "provider": cfg_c.embeddings.provider,
                    "config":   redact_cfg(cfg_c.embeddings.config),
                },
                "planner": {
                    "llm_provider":        cfg_c.planner.llm_provider,
                    "config":              redact_cfg(cfg_c.planner.config),
                    "default_top_k":       cfg_c.planner.default_top_k,
                    "enable_summarization": cfg_c.planner.enable_summarization,
                },
                "retrieval": {
                    "default_strategy": cfg_c.retrieval.default_strategy,
                    "hybrid":           cfg_c.retrieval.hybrid,
                    "vector":           cfg_c.retrieval.vector,
                },
                "server": {
                    "host":          cfg_c.server.host,
                    "port":          cfg_c.server.port,
                    "mcp_host":      cfg_c.server.mcp_host,
                    "mcp_port":      cfg_c.server.mcp_port,
                    "mcp_transport": cfg_c.server.mcp_transport,
                    "log_level":     cfg_c.server.log_level,
                },
            })

        app.router.routes.append(Route("/healthz",  _healthz,  methods=["GET"]))
        app.router.routes.append(Route("/settings", _settings, methods=["GET"]))

        # Warm the container in a background thread so uvicorn binds the port
        # immediately. Cloud Run (and any other load-balancer) health-checks the
        # port as soon as the process starts; blocking on get_container() before
        # uvicorn.run() causes the "container failed to start" timeout.
        # get_container() is thread-safe (uses a lock + global singleton), so
        # concurrent requests that arrive before warmup completes will block
        # inside get_container() rather than trigger a double-init.
        def _warmup():
            log.info("MCP: warming container in background…")
            try:
                get_container()
                log.info("MCP: container ready")
            except Exception:
                log.exception("MCP: container warmup failed")

        threading.Thread(target=_warmup, daemon=True).start()

        uvicorn.run(app, host=cfg.mcp_host, port=cfg.mcp_port,
                    log_level=cfg.log_level)
    else:
        # Stdio / non-HTTP transports: warm synchronously then hand off to
        # FastMCP's own transport loop (no port binding involved).
        log.info("MCP: warming container…")
        get_container()
        log.info("MCP: container ready")
        mcp.run(transport=cfg.mcp_transport, host=cfg.mcp_host, port=cfg.mcp_port)


if __name__ == "__main__":
    main()
