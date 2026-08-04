"""
FastAPI surface — exposes every Phase 1 retrieval strategy as its own endpoint
plus a planner-driven `/retrieve` route and an end-to-end `/query` route.

All endpoints share a single bootstrap `Container`, guaranteeing parity with
the FastMCP surface.
"""
from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from searchaas.app.bootstrap import get_container, reset_container
from searchaas.config import AppConfig, load_config
from searchaas.facts import apply_post_filters
from searchaas.infrastructure import AtlasFactory
from searchaas.observability import configure_logging, get_logger
from searchaas.observability.pipeline_capture import capture, captured
from searchaas.utils import clamp_auto_strategy, filter_by_entities, serialize_docs, summarize

configure_logging()
log = get_logger("searchaas.api")

# Read config once at module load — it is lru_cached and never changes per process.
_cfg = load_config()
_EMB_KEY = _cfg.atlas.embedding_key

# When post-filters are present we fetch extra candidates so in-memory filtering
# has headroom and doesn't starve the result set, then trim back to the user's
# requested top_k. Capped to keep the Atlas request bounded.
_POST_FILTER_OVERSHOOT = 3
_MAX_RETRIEVE_K = 100

# ---------------------------------------------------------------------------
# Embedding concurrency semaphore
# ---------------------------------------------------------------------------
# Limits the number of simultaneous Atlas AutoEmbed aggregation calls so the
# aggregate request rate to Voyage AI stays under its RPM quota. Initialized
# lazily on first use so the config is fully loaded before we read the limit.
# ---------------------------------------------------------------------------
_embed_semaphore: threading.Semaphore | None = None
_embed_semaphore_lock = threading.Lock()


def _get_semaphore() -> threading.Semaphore:
    global _embed_semaphore
    if _embed_semaphore is not None:
        return _embed_semaphore
    with _embed_semaphore_lock:
        if _embed_semaphore is None:
            limit = _cfg.retrieval.concurrency_limit
            _embed_semaphore = threading.Semaphore(limit if limit > 0 else 2 ** 31)
            log.info("Embed concurrency semaphore initialised: limit=%s", limit)
    return _embed_semaphore


# Strategies that call Atlas AutoEmbed (voyage-4) — fulltext and metadata do
# not embed the query so they bypass the semaphore.
_EMBED_STRATEGIES = frozenset({"vector", "hybrid", "auto", "parent_doc"})

# ---------------------------------------------------------------------------
# Rate-limit retry helper
# ---------------------------------------------------------------------------
_RATE_LIMIT_PHRASES = (
    "rate limit exceeded",
    "rate_limit",
    "too many requests",
    "embedding provider rate limit",
)
_RETRY_MAX      = int(os.environ.get("EMBED_RETRY_MAX",      "3"))
_RETRY_BASE_S   = float(os.environ.get("EMBED_RETRY_BASE_S", "1.0"))
_RETRY_CAP_S    = float(os.environ.get("EMBED_RETRY_CAP_S",  "8.0"))


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _RATE_LIMIT_PHRASES)


# ---------------------------------------------------------------------------
# Lifespan: warm the container at startup so the first real request pays no
# cold-start penalty (embedder init, LLM client, vector store, index preflight).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Readiness state — set True once the background warmup thread completes.
# Retrieval endpoints check this and return 503 until the container is ready,
# preventing the 30-90 s cold-start blocking from appearing as tail latency.
# ---------------------------------------------------------------------------
_container_ready = False
_warmup_start_ts: float = 0.0


def _warm_in_background() -> None:
    """
    Run all slow startup work in a daemon thread so uvicorn binds immediately.

    Sets _container_ready = True when done so retrieval endpoints know they
    can serve without blocking. Until then they return 503 + Retry-After: 5.
    """
    global _container_ready, _warmup_start_ts
    try:
        log.info(
            "Warmup [bg]: embeddings=%s llm=%s default_strategy=%s",
            _cfg.embeddings.provider,
            _cfg.planner.llm_provider,
            _cfg.retrieval.default_strategy,
        )
        ping = AtlasFactory.ping()
        if not ping.get("ok"):
            log.error("Warmup [bg]: Atlas ping FAILED — %s", ping)
        else:
            log.info("Warmup [bg]: Atlas reachable (%.1f ms)", ping.get("latency_ms", -1))

        log.info("Warmup [bg]: building container (embedder + LLM + vector store)…")
        get_container()

        # Pre-warm the HNSW index cache by issuing a lightweight vector query
        # per collection registered in YAML. This populates Atlas's buffer pool
        # so the first real requests see warm-cache latency instead of 3-5× cold.
        _prewarm_hnsw()

        elapsed = round(time.perf_counter() - _warmup_start_ts, 1)
        log.info("Warmup [bg]: container + HNSW cache ready in %.1f s", elapsed)
    except Exception:
        log.exception("Warmup [bg]: container build failed — requests will retry lazily")
    finally:
        # Mark ready regardless of partial failure so the server doesn't
        # refuse all traffic forever if an optional step (HNSW prewarm) fails.
        _container_ready = True


def _prewarm_hnsw() -> None:
    """Issue one cheap vector query per configured collection to warm Atlas HNSW."""
    try:
        c = get_container()
        # Use a generic warmup query that will match something in any domain corpus.
        warmup_query = "help"
        # Default collection (from YAML atlas.collection).
        try:
            from searchaas.planning.engine import RetrievalPlan
            plan = RetrievalPlan(strategy="vector", top_k=1)
            retriever = c.retrievers.create(plan)
            retriever.invoke(warmup_query)
            log.info("HNSW prewarm: default collection OK")
        except Exception as exc:
            log.warning("HNSW prewarm: default collection failed (%s) — skipping", exc)

        # Extra collections via HNSW_PREWARM_COLLECTIONS env var.
        # Format: "col:vec_index:text_key;col2:vec_index2:text_key2"
        extra = os.environ.get("HNSW_PREWARM_COLLECTIONS", "")
        if extra:
            for entry in extra.split(";"):
                entry = entry.strip()
                parts = entry.split(":")
                if len(parts) < 2:
                    continue
                col_name  = parts[0].strip()
                vec_idx   = parts[1].strip()
                text_key  = parts[2].strip() if len(parts) > 2 else "text"
                try:
                    from searchaas.planning.engine import RetrievalPlan
                    plan = RetrievalPlan(strategy="vector", top_k=1)
                    overrides = AtlasOverrides(
                        collection=col_name,
                        vector_index=vec_idx,
                        text_key=text_key,
                    )
                    retriever = c.retrievers.create(plan, overrides=overrides)
                    retriever.invoke(warmup_query)
                    log.info("HNSW prewarm: %s/%s OK", col_name, vec_idx)
                except Exception as exc:
                    log.warning("HNSW prewarm: %s/%s failed (%s)", col_name, vec_idx, exc)
    except Exception as exc:
        log.warning("HNSW prewarm: skipped (%s)", exc)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _warmup_start_ts
    _warmup_start_ts = time.perf_counter()
    t = threading.Thread(target=_warm_in_background, daemon=True, name="warmup")
    t.start()
    log.info("Startup: port bound — warmup running in background thread")
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


# Hard per-request deadline (seconds). Requests that exceed this are cancelled
# and return 504 Gateway Timeout — preventing long-tail hangs from tying up
# uvicorn worker threads. Set REQUEST_TIMEOUT_S=0 to disable.
_REQUEST_TIMEOUT_S = float(os.environ.get("REQUEST_TIMEOUT_S", "10"))

# Non-retrieval paths that bypass the readiness gate (always served immediately).
_READINESS_EXEMPT = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


@app.middleware("http")
async def _request_logger(request: Request, call_next):
    import asyncio as _asyncio
    rid = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    origin = request.headers.get("origin")
    path   = request.url.path

    # ── Readiness gate ────────────────────────────────────────────────────
    # Retrieval endpoints return 503 until background warmup completes so
    # new Cloud Run instances don't serve cold-start latency spikes to users.
    if not _container_ready and path not in _READINESS_EXEMPT:
        elapsed = round(time.perf_counter() - _warmup_start_ts, 1)
        log.info("req %s 503 NOT READY (%.1f s elapsed)", rid, elapsed)
        return JSONResponse(
            status_code=503,
            content={"detail": "Server warming up", "elapsed_s": elapsed},
            headers={"Retry-After": "5", "x-request-id": rid},
        )

    log.info("req %s -> %s %s origin=%s", rid, request.method, path, origin)
    try:
        if _REQUEST_TIMEOUT_S > 0:
            response = await _asyncio.wait_for(
                call_next(request), timeout=_REQUEST_TIMEOUT_S
            )
        else:
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
        import asyncio as _asyncio
        ms = round((time.perf_counter() - t0) * 1000, 1)
        if isinstance(exc, _asyncio.TimeoutError):
            log.warning("req %s TIMEOUT after %.0f ms (limit=%.0f s)",
                        rid, ms, _REQUEST_TIMEOUT_S)
            return JSONResponse(
                status_code=504,
                content={"detail": f"Request timed out after {_REQUEST_TIMEOUT_S:.0f} s",
                         "request_id": rid, "path": path},
                headers={"x-request-id": rid},
            )
        log.exception("req %s !! after %sms", rid, ms)
        debug = os.environ.get("SEARCHAAS_DEBUG_ERRORS", "1") == "1"
        body: dict[str, Any] = {
            "error": type(exc).__name__,
            "message": str(exc),
            "request_id": rid,
            "path": path,
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
    # The ACTUAL Atlas aggregation captured from the executed query
    # ({collection, database, pipeline}). None if nothing was captured (the UI
    # then falls back to a client-side reconstruction).
    pipeline: dict[str, Any] | None = None


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
    enable_summarization: bool | None = None


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


def _uq_dict(uq: Any) -> dict[str, Any]:
    return {
        "raw": uq.raw,
        "corrected": uq.corrected,
        "rewritten": uq.rewritten,
        "entities": list(uq.entities or []),
        "facts": [f.to_dict() for f in (getattr(uq, "facts", None) or [])],
        # pre-filter applied in Atlas; post-filter applied in-memory after retrieval
        "metadata_filters": dict(uq.metadata_filters or {}),
        "post_filters": [f.to_dict() for f in (getattr(uq, "post_filters", None) or [])],
        "intent": uq.intent,
    }


def _execute_plan(
    req: RetrieveRequest,
    plan: Any,
    invoke_query: str,
    uq: Any,
    planning_ms: float,
    understanding_ms: float,
    t_total: float,
) -> RetrieveResponse:
    """Shared tail: retrieve → serialize → post-filter → summarize → build response."""
    c = get_container()
    emb_key = (req.atlas and req.atlas.embedding_key) or _EMB_KEY

    # Non-indexed facts are applied as an in-memory post-filter. Overshoot the
    # candidate count so post-filtering has headroom, then trim back to top_k.
    post_filters = list(getattr(uq, "post_filters", None) or [])
    desired_k = plan.top_k
    if post_filters:
        plan.top_k = min(desired_k * _POST_FILTER_OVERSHOOT, _MAX_RETRIEVE_K)

    def _invoke_with_guard(plan, overrides, retrieval_overrides) -> tuple[list, list, float]:
        """
        Invoke the retriever under the embedding concurrency semaphore and with
        exponential-backoff retries on Voyage AI rate-limit errors.

        Returns (docs, caps, mongo_ms).
        """
        uses_embed = plan.strategy in _EMBED_STRATEGIES
        sem = _get_semaphore() if uses_embed else None

        # Maximum seconds a request may wait for a semaphore slot before the
        # server returns 503. Prevents threads from blocking indefinitely when
        # the embedding concurrency limit is saturated under high load.
        # Override via EMBED_QUEUE_TIMEOUT_S env var (0 = no timeout).
        _queue_timeout = float(os.environ.get("EMBED_QUEUE_TIMEOUT_S", "4"))

        attempt = 0
        while True:
            retriever = c.retrievers.create(
                plan, overrides=overrides, retrieval_overrides=retrieval_overrides,
            )
            try:
                if sem:
                    acquired = sem.acquire(
                        timeout=_queue_timeout if _queue_timeout > 0 else None
                    )
                    if not acquired:
                        log.warning(
                            "Semaphore queue timeout after %.1f s (strategy=%s) — "
                            "returning 503. Raise CONCURRENCY_LIMIT or "
                            "EMBED_QUEUE_TIMEOUT_S to absorb more burst.",
                            _queue_timeout, plan.strategy,
                        )
                        raise HTTPException(
                            status_code=503,
                            detail=(
                                "Server at embedding capacity. "
                                "All query slots are busy — retry in a moment."
                            ),
                        )
                try:
                    t0 = time.perf_counter()
                    with capture():
                        docs = retriever.invoke(invoke_query)
                        caps = captured()
                    mongo_ms = round((time.perf_counter() - t0) * 1000, 1)
                finally:
                    if sem:
                        sem.release()
                return docs, caps, mongo_ms
            except HTTPException:
                raise
            except Exception as exc:
                if _is_rate_limit_error(exc) and attempt < _RETRY_MAX:
                    delay = min(_RETRY_BASE_S * (2 ** attempt), _RETRY_CAP_S)
                    attempt += 1
                    log.warning(
                        "Voyage AI rate limit hit (attempt %d/%d) — "
                        "backing off %.1f s before retry. strategy=%s",
                        attempt, _RETRY_MAX, delay, plan.strategy,
                    )
                    time.sleep(delay)
                    continue
                raise

    try:
        docs, caps, mongo_ms = _invoke_with_guard(
            plan, req.atlas, req.retrieval,
        )
    except Exception as exc:
        # Atlas rejects $vectorSearch pre-filters on paths not declared as
        # {type: filter} in the index. When this happens, evict the offending
        # field from the runtime allowlist and retry once without any filters
        # so the user gets results rather than a 500.
        err_str = str(exc)
        if "needs to be indexed as filter" in err_str and plan.filters:
            import re as _re
            bad_field = _re.search(r"Path '([^']+)' needs to be indexed", err_str)
            if bad_field:
                c.retrievers.evict_filter_field(bad_field.group(1))
            log.warning(
                "Pre-filter %s rejected by Atlas (field not indexed as filter) — "
                "retrying without filters. Error: %s",
                list(plan.filters.keys()), exc,
            )
            plan.filters = {}
            try:
                docs, caps, mongo_ms = _invoke_with_guard(
                    plan, req.atlas, req.retrieval,
                )
            except Exception as retry_exc:
                log.exception("Retrieval failed after filter retry (%s)", plan.strategy)
                raise HTTPException(
                    status_code=500, detail=f"retrieval failed: {retry_exc}"
                ) from retry_exc
        elif _is_rate_limit_error(exc):
            log.error(
                "Voyage AI rate limit exhausted after %d retries (strategy=%s): %s",
                _RETRY_MAX, plan.strategy, exc,
            )
            raise HTTPException(
                status_code=429,
                detail=(
                    "Embedding provider rate limit exceeded. "
                    f"Retried {_RETRY_MAX}× with backoff. "
                    "Reduce concurrency or upgrade your Voyage AI plan."
                ),
            ) from exc
        else:
            log.exception("Retrieval failed (%s)", plan.strategy)
            raise HTTPException(status_code=500, detail=f"retrieval failed: {exc}") from exc

    # Prefer the aggregate on the queried collection; else the last captured one.
    target_coll = (req.atlas and req.atlas.collection) or c.config.atlas.collection
    pipeline_doc = next((p for p in caps if p.get("collection") == target_coll), None) \
        or (caps[-1] if caps else None)

    serialized = serialize_docs(docs, emb_key, include_score=True)
    serialized = filter_by_entities(serialized, list(uq.entities or []))
    if post_filters:
        before = len(serialized)
        serialized = apply_post_filters(serialized, post_filters)[:desired_k]
        plan.top_k = desired_k  # report the user-facing top_k, not the overshoot
        log.info(
            "post-filter: %d -> %d docs via %s",
            before, len(serialized), [f.to_dict() for f in post_filters],
        )

    t_sum = time.perf_counter()
    _enable_sum = get_container().config.planner.enable_summarization
    summary = summarize(c.llm, invoke_query, serialized) if _enable_sum else None
    summarize_ms = round((time.perf_counter() - t_sum) * 1000, 1) if summary is not None else None

    total_ms = round((time.perf_counter() - t_total) * 1000, 1)
    log.info(
        "retrieve strategy=%s results=%s mongo_ms=%s understanding_ms=%s "
        "planning_ms=%s summarize_ms=%s total_ms=%s",
        plan.strategy, len(serialized), mongo_ms, understanding_ms,
        planning_ms, summarize_ms, total_ms,
    )
    return RetrieveResponse(
        strategy=plan.strategy,
        plan=plan.model_dump(),
        results=serialized,
        understood_query=_uq_dict(uq),
        summary=summary,
        timings=Timings(
            mongo_ms=mongo_ms,
            planning_ms=planning_ms,
            understanding_ms=understanding_ms,
            summarize_ms=summarize_ms,
            total_ms=total_ms,
        ),
        pipeline=pipeline_doc,
    )


def _run(strategy: str, req: RetrieveRequest) -> RetrieveResponse:
    c = get_container()
    if req.atlas:
        log.info("retrieve: UI atlas overrides=%s", req.atlas.model_dump(exclude_none=True))

    t_total = time.perf_counter()

    t_uq = time.perf_counter()
    uq = c.understanding.process(req.query)
    understanding_ms = round((time.perf_counter() - t_uq) * 1000, 1)

    # Merge NLU-extracted metadata filters with explicit request filters.
    # Request filters take precedence so users can always override NLU results.
    merged_filters = {**(uq.metadata_filters or {}), **req.filters}

    t_plan = time.perf_counter()
    plan = c.planner.plan_for(
        strategy=strategy,
        query=uq.rewritten,
        top_k=uq.limit or req.top_k,  # a count named in the query wins
        filters=merged_filters,
        sort=uq.sort,  # honoured by the metadata strategy; ignored by others
    )
    planning_ms = round((time.perf_counter() - t_plan) * 1000, 1)
    log.info(
        "retrieve strategy=%s top_k=%s filters=%s query=%r rewritten=%r",
        plan.strategy, plan.top_k, plan.filters, req.query[:80], uq.rewritten[:80],
    )
    return _execute_plan(req, plan, uq.rewritten, uq, planning_ms, understanding_ms, t_total)


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


@app.post("/retrieve/metadata", response_model=RetrieveResponse)
def retrieve_metadata(req: RetrieveRequest) -> RetrieveResponse:
    """Structured retrieval: $match/$sort/$limit for rankings & exact lookups."""
    return _run("metadata", req)


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

    # A count named in the query (uq.limit, already applied by the planner) wins;
    # otherwise honor an explicit request top_k.
    if uq.limit is None and req.top_k is not None:
        plan.top_k = req.top_k
    # Merge all filter sources: metadata_filters (NLU) < plan filters (LLM planner) < request filters (user)
    plan.filters = {**(uq.metadata_filters or {}), **plan.filters, **(req.filters or {})}

    return _execute_plan(req, plan, uq.rewritten, uq, planning_ms, understanding_ms, t_total)


@app.post("/query")
def query(req: RetrieveRequest) -> dict[str, Any]:
    """Alias for /retrieve — stable surface for Phase 2 enhancements."""
    return retrieve_auto(req).model_dump()
