"""
FastAPI surface — exposes every Phase 1 retrieval strategy as its own endpoint
plus a planner-driven `/retrieve` route and an end-to-end `/query` route.

All endpoints share a single bootstrap `Container`, guaranteeing parity with
the FastMCP surface.
"""
from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import traceback

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from searchaas.app.bootstrap import get_container, reset_container
from searchaas.config import AppConfig, load_config
from searchaas.infrastructure import AtlasFactory
from searchaas.observability import configure_logging, get_logger
from searchaas.utils import clamp_auto_strategy, serialize_docs, summarize

configure_logging()
log = get_logger("searchaas.api")

# Read config once at module load — it is lru_cached and never changes per process.
_cfg = load_config()
_EMB_KEY = _cfg.atlas.embedding_key


# ---------------------------------------------------------------------------
# Lifespan: warm the container at startup so the first real request pays no
# cold-start penalty (embedder init, LLM client, vector store, index preflight).
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(app: FastAPI):
    log.info(
        "Startup: embeddings=%s llm=%s default_strategy=%s",
        _cfg.embeddings.provider,
        _cfg.planner.llm_provider,
        _cfg.retrieval.default_strategy,
    )
    ping = AtlasFactory.ping()
    if not ping.get("ok"):
        log.error("Startup: Atlas ping FAILED — %s", ping)
    else:
        log.info("Startup: Atlas reachable (%.1f ms)", ping.get("latency_ms", -1))

    log.info("Startup: warming container (embedder + LLM + vector store)…")
    get_container()
    log.info("Startup: container ready — serving requests")
    yield


app = FastAPI(
    title="SearchaaS Retrieval API",
    version="0.1.0",
    description="Phase 1 — MongoDB Atlas retrieval (vector / fulltext / hybrid / graph / parent_doc).",
    lifespan=_lifespan,
)

# CORS — local dev origins by default. Add production origins (S3 website,
# ECS Express URL, custom domain) via the SEARCHAAS_CORS_ORIGINS env var,
# comma-separated, e.g.:
#   SEARCHAAS_CORS_ORIGINS="http://searchaas-ui-123.s3-website-us-east-1.amazonaws.com,https://app.example.com"
# Or set SEARCHAAS_CORS_ORIGINS="*" to allow all origins (dev only).
_default_origin_regex = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
_extra_origins = [o.strip() for o in os.environ.get("SEARCHAAS_CORS_ORIGINS", "").split(",") if o.strip()]
_allow_all = "*" in _extra_origins

if _allow_all:
    log.warning("CORS: allowing ALL origins (SEARCHAAS_CORS_ORIGINS=*) — do NOT use in production with credentials")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,  # cannot combine '*' with credentials per spec
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    log.info("CORS: regex=%r extra_origins=%s", _default_origin_regex, _extra_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_extra_origins,
        allow_origin_regex=_default_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def _request_logger(request: Request, call_next):
    rid = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    origin = request.headers.get("origin")
    log.info("req %s -> %s %s origin=%s", rid, request.method, request.url.path, origin)
    try:
        response = await call_next(request)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        # Surface CORS preflight rejections clearly. Symptoms: browser sends
        # OPTIONS, server returns 400 with no body and no Access-Control-* headers.
        if (
            request.method == "OPTIONS"
            and response.status_code >= 400
            and "access-control-allow-origin" not in (k.lower() for k in response.headers.keys())
        ):
            log.warning(
                "CORS PREFLIGHT REJECTED: origin=%r method=%s path=%s status=%s. "
                "Add this origin to SEARCHAAS_CORS_ORIGINS env var (comma-separated) "
                "or set SEARCHAAS_CORS_ORIGINS=* for dev.",
                origin, request.headers.get("access-control-request-method"),
                request.url.path, response.status_code,
            )
        log.info("req %s <- %s %s %sms", rid, response.status_code, request.url.path, ms)
        response.headers["x-request-id"] = rid
        return response
    except Exception as exc:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        log.exception("req %s !! after %sms", rid, ms)
        # Surface the real error to the client instead of a bare 500 with no
        # body. Controlled by SEARCHAAS_DEBUG_ERRORS=1 (default on in non-prod).
        debug = os.environ.get("SEARCHAAS_DEBUG_ERRORS", "1") == "1"
        body: dict[str, Any] = {
            "error": type(exc).__name__,
            "message": str(exc),
            "request_id": rid,
            "path": request.url.path,
        }
        if debug:
            body["traceback"] = traceback.format_exception(exc)
        return JSONResponse(status_code=500, content=body,
                            headers={"x-request-id": rid})


# --------------------------------------------------------------------- schemas


# ── Per-request lightweight overrides (no container rebuild) ──────────────────


class AtlasOverrides(BaseModel):
    """
    Per-request Atlas config overrides sent from the UI settings panel.
    Only the values provided will override the server-side defaults for that
    request. URI and database require a full settings update (POST /settings).
    """
    collection:    str | None = Field(default=None, description="Collection to query")
    vector_index:  str | None = Field(default=None, description="Atlas Vector Search index name")
    search_index:  str | None = Field(default=None, description="Atlas Search (Lucene) index name")
    text_key:      str | None = Field(default=None, description="Field containing the document text")
    embedding_key: str | None = Field(default=None, description="Field containing the embedding vector")
    dimensions:    int | None = Field(default=None, description="Vector dimensionality")


class RetrievalOverrides(BaseModel):
    """Per-request retrieval tuning — applied on top of the active container config."""
    vector_weight:   float | None = Field(default=None, ge=0.0, le=1.0, description="Hybrid vector channel weight")
    fulltext_weight: float | None = Field(default=None, ge=0.0, le=1.0, description="Hybrid full-text channel weight")
    num_candidates:  int   | None = Field(default=None, ge=10,  le=2000, description="$vectorSearch numCandidates")


class RetrieveRequest(BaseModel):
    query:     str = Field(..., description="User query")
    top_k:     int | None = Field(default=None, ge=1, le=100)
    filters:   dict[str, Any] = Field(default_factory=dict)
    atlas:     AtlasOverrides     | None = Field(default=None, description="Per-request Atlas config overrides")
    retrieval: RetrievalOverrides | None = Field(default=None, description="Per-request retrieval tuning")


class Timings(BaseModel):
    """Server-side timing breakdown for a single retrieval request.

    All values are wall-clock milliseconds measured with `time.perf_counter`.

    * `mongo_ms` — time spent in `retriever.invoke()`, which is dominated by
      the Atlas `$vectorSearch` / `$search` aggregation. This is the
      "MongoDB query latency" surfaced in the UI.
    * `planning_ms` — time spent in the planner/policy resolution.
    * `understanding_ms` — time spent in the Query Understanding layer
      (only populated on the `/retrieve` auto endpoint).
    * `summarize_ms` — time spent in the LLM summarizer (auto endpoint).
    * `total_ms` — total time observed inside the request handler. The
      gap between this and the UI-side wall clock is network + FastAPI
      overhead.
    """
    mongo_ms:         float | None = None
    planning_ms:      float | None = None
    understanding_ms: float | None = None
    summarize_ms:     float | None = None
    total_ms:         float | None = None


class RetrieveResponse(BaseModel):
    strategy: str
    plan: dict[str, Any]
    results: list[dict[str, Any]]
    understood_query: dict[str, Any] | None = None
    summary: str | None = None
    timings: Timings | None = None


# ── Persistent settings update (triggers container rebuild) ───────────────────


class EmbeddingsUpdate(BaseModel):
    """Subset of embeddings config the UI may update."""
    provider: str | None = None
    config:   dict[str, Any] | None = None


class PlannerUpdate(BaseModel):
    """Subset of planner config the UI may update."""
    llm_provider: str | None = None
    config:       dict[str, Any] | None = None
    default_top_k: int | None = Field(default=None, ge=1, le=200)


class RetrievalSettingsUpdate(BaseModel):
    """Subset of retrieval config the UI may update."""
    default_strategy: str | None = None
    hybrid:  dict[str, Any] | None = None
    vector:  dict[str, Any] | None = None


class AtlasSettingsUpdate(BaseModel):
    """Full atlas config the UI may update (including uri / database)."""
    uri:                str | None = None
    database:           str | None = None
    collection:         str | None = None
    vector_index:       str | None = None
    search_index:       str | None = None
    text_key:           str | None = None
    embedding_key:      str | None = None
    relevance_score_fn: str | None = None
    dimensions:         int | None = None


class SettingsUpdate(BaseModel):
    """
    Full settings update payload — sent from the UI 'Apply to Backend' button.
    All sections are optional; only provided (non-null, non-template) values are
    applied.  Triggers a full container rebuild when embeddings or LLM sections
    differ from the current active config.
    """
    atlas:     AtlasSettingsUpdate     | None = None
    embeddings: EmbeddingsUpdate       | None = None
    planner:   PlannerUpdate           | None = None
    retrieval: RetrievalSettingsUpdate | None = None


# ── Settings helpers ──────────────────────────────────────────────────────────


def _is_template(v: Any) -> bool:
    """Return True if the value is an unexpanded env-var template like ${VAR}."""
    return isinstance(v, str) and "${" in v


def _merge_config(current: AppConfig, update: SettingsUpdate) -> AppConfig:
    """
    Merge non-null, non-template values from *update* into *current* and
    return a new validated AppConfig.  The caller is responsible for
    rebuilding the container if provider-level fields changed.
    """
    data = current.model_dump()

    def _apply(target: dict, patch: dict) -> None:
        for k, v in patch.items():
            if v is None or _is_template(v):
                continue
            if isinstance(v, dict) and isinstance(target.get(k), dict):
                _apply(target[k], v)
            else:
                target[k] = v

    if update.atlas:
        _apply(data["atlas"], update.atlas.model_dump())
    if update.embeddings:
        _apply(data["embeddings"], update.embeddings.model_dump())
    if update.planner:
        _apply(data["planner"], update.planner.model_dump())
    if update.retrieval:
        _apply(data["retrieval"], update.retrieval.model_dump())

    return AppConfig(**data)


# --------------------------------------------------------------------- helpers


def _run(strategy: str, req: RetrieveRequest) -> RetrieveResponse:
    c = get_container()
    if req.atlas:
        log.info("retrieve: UI atlas overrides=%s", req.atlas.model_dump(exclude_none=True))

    t_total = time.perf_counter()

    t_uq = time.perf_counter()
    uq = c.understanding.process(req.query)
    understanding_ms = round((time.perf_counter() - t_uq) * 1000, 1)

    t_plan = time.perf_counter()
    plan = c.planner.plan_for(
        strategy=strategy,
        query=req.query,
        top_k=req.top_k,
        filters=req.filters,
    )
    planning_ms = round((time.perf_counter() - t_plan) * 1000, 1)
    log.info(
        "retrieve strategy=%s top_k=%s filters=%s query=%r",
        plan.strategy, plan.top_k, plan.filters, req.query[:120],
    )
    emb_key = (req.atlas and req.atlas.embedding_key) or _EMB_KEY
    try:
        t0 = time.perf_counter()
        docs = c.retrievers.create(
            plan, overrides=req.atlas, retrieval_overrides=req.retrieval,
        ).invoke(req.query)
        mongo_ms = round((time.perf_counter() - t0) * 1000, 1)
        log.info(
            "retrieve strategy=%s results=%s mongo_ms=%s planning_ms=%s understanding_ms=%s",
            plan.strategy, len(docs), mongo_ms, planning_ms, understanding_ms,
        )
    except Exception as exc:
        log.exception("Retrieval failed (%s)", strategy)
        raise HTTPException(status_code=500, detail=f"retrieval failed: {exc}") from exc

    serialized = serialize_docs(docs, emb_key, include_score=True)

    # AI summary for every strategy — best-effort, never raises. Skipped when
    # there are no results, no LLM is configured, or the client opts out via
    # `summarize=false` on the request (room for a flag if you add one later).
    t_sum = time.perf_counter()
    summary = summarize(c.llm, req.query, serialized)
    summarize_ms = round((time.perf_counter() - t_sum) * 1000, 1) if summary is not None else None
    if summary is not None:
        log.info(
            "retrieve strategy=%s summarize_ms=%s summary_chars=%s",
            plan.strategy, summarize_ms, len(summary),
        )

    total_ms = round((time.perf_counter() - t_total) * 1000, 1)
    return RetrieveResponse(
        strategy=plan.strategy,
        plan=plan.model_dump(),
        results=serialized,
        understood_query={
            "raw": uq.raw,
            "corrected": uq.corrected,
            "rewritten": uq.rewritten,
            "entities": list(uq.entities or []),
            "metadata_filters": dict(uq.metadata_filters or {}),
            "intent": uq.intent,
        },
        summary=summary,
        timings=Timings(
            mongo_ms=mongo_ms,
            planning_ms=planning_ms,
            understanding_ms=understanding_ms,
            summarize_ms=summarize_ms,
            total_ms=total_ms,
        ),
    )


# --------------------------------------------------------------------- routes


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "embeddings_provider": _cfg.embeddings.provider,
        "llm_provider": _cfg.planner.llm_provider,
        "default_strategy": _cfg.retrieval.default_strategy,
    }


@app.get("/settings")
def get_settings() -> dict[str, Any]:
    """Return the full active config (secrets redacted) so the UI can reflect the live state."""
    from searchaas.utils import redact_cfg
    c = get_container()
    cfg = c.config
    return {
        "atlas": {
            "uri":                "***",        # never expose credentials
            "database":           cfg.atlas.database,
            "collection":         cfg.atlas.collection,
            "vector_index":       cfg.atlas.vector_index,
            "search_index":       cfg.atlas.search_index,
            "text_key":           cfg.atlas.text_key,
            "embedding_key":      cfg.atlas.embedding_key,
            "relevance_score_fn": cfg.atlas.relevance_score_fn,
            "dimensions":         cfg.atlas.dimensions,
        },
        "embeddings": {
            "provider": cfg.embeddings.provider,
            "config":   redact_cfg(cfg.embeddings.config),
        },
        "planner": {
            "llm_provider":  cfg.planner.llm_provider,
            "config":        redact_cfg(cfg.planner.config),
            "default_top_k": cfg.planner.default_top_k,
        },
        "retrieval": {
            "default_strategy": cfg.retrieval.default_strategy,
            "hybrid":           cfg.retrieval.hybrid,
            "vector":           cfg.retrieval.vector,
        },
        "server": {
            "host":          cfg.server.host,
            "port":          cfg.server.port,
            "mcp_host":      cfg.server.mcp_host,
            "mcp_port":      cfg.server.mcp_port,
            "mcp_transport": cfg.server.mcp_transport,
            "log_level":     cfg.server.log_level,
        },
    }


@app.post("/settings")
def update_settings(update: SettingsUpdate) -> dict[str, Any]:
    """
    Apply config changes from the UI and rebuild the container.

    Only non-null, non-template (no \${…}) values are applied.
    Changing embeddings or LLM provider triggers a full container rebuild
    (embedder + LLM + vector store are re-initialised).  Atlas-only or
    retrieval-only changes are cheaper but still trigger a rebuild so the
    RetrieverFactory picks up the new defaults.
    """
    current = get_container().config
    log.info("POST /settings: applying update=%s", update.model_dump(exclude_none=True))

    try:
        new_cfg = _merge_config(current, update)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid config: {exc}") from exc

    try:
        reset_container(new_cfg)
    except Exception as exc:
        log.exception("Container rebuild failed after settings update")
        raise HTTPException(status_code=500, detail=f"Container rebuild failed: {exc}") from exc

    log.info(
        "Settings applied — embeddings=%s llm=%s collection=%s",
        new_cfg.embeddings.provider,
        new_cfg.planner.llm_provider,
        new_cfg.atlas.collection,
    )
    return {"status": "ok", "message": "Settings applied and container rebuilt."}


@app.get("/diagnose")
def diagnose() -> dict[str, Any]:
    """Full self-check: Atlas ping, collection stats, embedder probe."""
    from searchaas.embeddings import EmbeddingFactory
    c = get_container()
    return {
        "config": {
            "embeddings_provider": c.config.embeddings.provider,
            "llm_provider": c.config.planner.llm_provider,
            "atlas_db": c.config.atlas.database,
            "atlas_collection": c.config.atlas.collection,
            "vector_index": c.config.atlas.vector_index,
            "search_index": c.config.atlas.search_index,
        },
        "atlas_ping": AtlasFactory.ping(),
        "collection_stats": AtlasFactory.collection_stats(),
        "embedder_probe": EmbeddingFactory.probe(c.embeddings),
    }


class VectorProbeRequest(BaseModel):
    query: str = Field(..., description="Probe query")
    k: int = Field(5, ge=1, le=50)
    filters: dict[str, Any] = Field(default_factory=dict)


@app.post("/diagnose/vector")
def diagnose_vector(req: VectorProbeRequest) -> dict[str, Any]:
    """Instrumented vector search — reports exactly where failure occurs."""
    return get_container().retrievers.run_vector(req.query, k=req.k, filters=req.filters)


@app.post("/retrieve/vector", response_model=RetrieveResponse)
def retrieve_vector(req: RetrieveRequest) -> RetrieveResponse:
    return _run("vector", req)


@app.post("/retrieve/fulltext", response_model=RetrieveResponse)
def retrieve_fulltext(req: RetrieveRequest) -> RetrieveResponse:
    return _run("fulltext", req)


@app.post("/retrieve/hybrid", response_model=RetrieveResponse)
def retrieve_hybrid(req: RetrieveRequest) -> RetrieveResponse:
    return _run("hybrid", req)


@app.post("/retrieve/graph", response_model=RetrieveResponse)
def retrieve_graph(req: RetrieveRequest) -> RetrieveResponse:
    return _run("graph", req)


@app.post("/retrieve/parent-doc", response_model=RetrieveResponse)
def retrieve_parent_doc(req: RetrieveRequest) -> RetrieveResponse:
    return _run("parent_doc", req)


@app.post("/retrieve", response_model=RetrieveResponse)
def retrieve_auto(req: RetrieveRequest) -> RetrieveResponse:
    """
    Auto mode: understand the query, plan a strategy, retrieve, and summarize.
    Strategy is clamped to {hybrid, fulltext, vector} for end-user execution.
    """
    c = get_container()
    if req.atlas:
        log.info("retrieve auto: UI atlas overrides=%s", req.atlas.model_dump(exclude_none=True))

    t_total = time.perf_counter()

    t_uq = time.perf_counter()
    uq = c.understanding.process(req.query)
    understanding_ms = round((time.perf_counter() - t_uq) * 1000, 1)

    t_plan = time.perf_counter()
    plan = c.planner.plan(uq)
    planning_ms = round((time.perf_counter() - t_plan) * 1000, 1)

    original = plan.strategy
    plan.strategy = clamp_auto_strategy(plan.strategy, uq.intent)
    if plan.strategy != original:
        log.info(
            "Auto: clamped planner strategy %r -> %r (intent=%s)",
            original, plan.strategy, uq.intent,
        )

    if req.top_k:
        plan.top_k = req.top_k
    if req.filters:
        plan.filters = {**plan.filters, **req.filters}

    emb_key = (req.atlas and req.atlas.embedding_key) or _EMB_KEY
    try:
        t_mongo = time.perf_counter()
        docs = c.retrievers.create(
            plan, overrides=req.atlas, retrieval_overrides=req.retrieval,
        ).invoke(uq.rewritten)
        mongo_ms = round((time.perf_counter() - t_mongo) * 1000, 1)
    except Exception as exc:
        log.exception("Auto retrieval failed")
        raise HTTPException(status_code=500, detail=f"retrieval failed: {exc}") from exc

    serialized = serialize_docs(docs, emb_key, include_score=True)

    t_sum = time.perf_counter()
    summary = summarize(c.llm, uq.rewritten, serialized)
    summarize_ms = round((time.perf_counter() - t_sum) * 1000, 1)

    total_ms = round((time.perf_counter() - t_total) * 1000, 1)
    log.info(
        "retrieve auto: strategy=%s results=%s mongo_ms=%s understanding_ms=%s "
        "planning_ms=%s summarize_ms=%s total_ms=%s",
        plan.strategy, len(serialized), mongo_ms, understanding_ms,
        planning_ms, summarize_ms, total_ms,
    )
    return RetrieveResponse(
        strategy=plan.strategy,
        plan=plan.model_dump(),
        results=serialized,
        understood_query={
            "raw": uq.raw,
            "corrected": uq.corrected,
            "rewritten": uq.rewritten,
            "entities": list(uq.entities or []),
            "metadata_filters": dict(uq.metadata_filters or {}),
            "intent": uq.intent,
        },
        summary=summary,
        timings=Timings(
            mongo_ms=mongo_ms,
            planning_ms=planning_ms,
            understanding_ms=understanding_ms,
            summarize_ms=summarize_ms,
            total_ms=total_ms,
        ),
    )


@app.post("/query")
def query(req: RetrieveRequest) -> dict[str, Any]:
    """Alias for /retrieve — stable surface for Phase 2 enhancements."""
    return retrieve_auto(req).model_dump()
