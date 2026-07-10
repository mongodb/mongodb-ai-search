"""
Retriever Factory — produces a `BaseRetriever` for the plan's strategy.

Strategies (Phase 1):
  vector     : MongoDBAtlasVectorSearch.as_retriever
  fulltext   : MongoDBAtlasFullTextSearchRetriever
  hybrid     : MongoDBAtlasHybridSearchRetriever
  parent_doc : MongoDBAtlasParentDocumentRetriever
  graph      : custom $graphLookup-backed retriever (GraphRAG)

Self-query is Phase 2 (depends on Metadata Intelligence).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import PrivateAttr

# --- Hot-path imports hoisted to module level -------------------------------
# These were previously re-imported inside every retrieval call. `sys.modules`
# caches them after the first hit, but the per-call dict lookup is still
# avoidable — and module-level imports make dependency cost visible during
# `python -c "import searchaas.retrieval.factory"` profiling. None of these
# are conditional or optional; they're always required to query Atlas.
from langchain_mongodb.embeddings import AutoEmbeddings
from langchain_mongodb.retrievers import (
    MongoDBAtlasFullTextSearchRetriever,
    # Retained for the pre-8.0 portable-RRF fallback (client-side embed path);
    # not wired into `_build_hybrid`, which now emits native `$rankFusion`.
    MongoDBAtlasHybridSearchRetriever,  # noqa: F401
)
from langchain_mongodb.vectorstores import MongoDBAtlasVectorSearch
from langchain_mongodb.graphrag.graph import MongoDBGraphStore
from langchain_mongodb.utils import make_serializable
from pymongo.errors import OperationFailure
from pymongo_search_utils.pipeline import (
    autoembedding_vector_search_stage,
    combine_pipelines,
    final_hybrid_stage,
    reciprocal_rank_stage,
    text_search_stage,
    vector_search_stage,
)

from searchaas.filtering import sanitize_filters
from searchaas.infrastructure import AtlasFactory
from searchaas.observability import get_logger

log = get_logger("searchaas.retrieval")


# Forward reference — AtlasOverrides is defined in api/app.py but we accept
# it here as Any to avoid a circular import.  Type checkers see it as Any.
_Overrides = Any  # AtlasOverrides | None

# A pre_filter is applied *during* ANN search, so it thins the approximate
# candidate pool — recall degrades when the filter is selective. Widen the pool
# when a filter is present (Atlas guidance). Capped at the $vectorSearch max.
_FILTER_NUMCANDIDATES_BOOST = 3
_MAX_NUM_CANDIDATES = 10000

# Max recursion depth for the GraphRAG `$graphLookup` traversal inside
# `MongoDBGraphStore`. The store default is 3; 2 keeps hop fan-out bounded for
# a first cut. Plumb to config/RetrievalOverrides later if per-request control
# is needed.
_GRAPH_MAX_DEPTH = 2


def _oversampling_factor(k: int, num_cands: int, has_filter: bool) -> int:
    """candidates-per-result for $vectorSearch, boosted when a filter is set."""
    over = max(1, num_cands // max(k, 1))
    if has_filter:
        over = min(over * _FILTER_NUMCANDIDATES_BOOST, max(1, _MAX_NUM_CANDIDATES // max(k, 1)))
    return over


class RetrieverFactory:
    """Build a base retriever for the requested strategy."""

    def __init__(
        self,
        vector_store: Any = None,
        llm: Any = None,
        collection: Any = None,
        *,
        vector_store_provider: Callable[[], Any] | None = None,
        embeddings: Any = None,
        is_auto: bool | None = None,
        vector_index: str | None = None,
        search_index: str = "default",
        text_key: str = "content",
        embedding_key: str = "embedding",
        dimensions: int = -1,
        hybrid_weights: dict[str, float] | None = None,
        vector_num_candidates: int = 200,
        filter_fields: list[str] | None = None,
        fulltext_filter_fields: list[str] | None = None,
        max_time_ms: int | None = None,
    ) -> None:
        # Vector-store construction is DEFERRED. The base store is built on first
        # use by a vector-needing strategy (vector / non-auto hybrid / non-auto
        # parent_doc / the vector diagnostic) via `_ensure_base_vector_store`,
        # never for graph/fulltext/metadata. This is what keeps a graph-only
        # deployment from auto-creating a spurious vector index on its
        # collection. A pre-built `vector_store` (tests, warm starts) is honored
        # directly; otherwise `vector_store_provider` builds it lazily once.
        self._vs_built = vector_store            # None until built (or provided)
        self._vs_provider = vector_store_provider
        self._vs_lock = threading.Lock()
        self._llm = llm
        self._col = collection
        # `MongoDBAtlasVectorSearch` stores this as `_index_name`; we accept
        # it explicitly so we don't reach into a private attribute (and so we
        # don't need a built store to know the index name).
        self._vector_index = vector_index or getattr(vector_store, "_index_name", "vector_index")
        self._search_index = search_index
        self._text_key = text_key
        self._embedding_key = embedding_key
        self._dimensions = dimensions
        self._hybrid_weights = hybrid_weights or {"vector_weight": 0.6, "fulltext_weight": 0.4}
        self._vector_candidates = vector_num_candidates
        # Filter allowlists derived from the user-supplied index definitions.
        # `filter_fields`: paths indexed as {type: filter} in the vector index
        # ($vectorSearch rejects any other path). `fulltext_filter_fields`:
        # paths mapped in the Lucene index; None means the mapping is dynamic
        # and every field is filterable.
        self._filter_fields = list(filter_fields or [])
        self._fulltext_filter_fields = fulltext_filter_fields
        self._max_time_ms = max_time_ms  # applied to custom aggregate() calls

        # Embedder mode. Prefer the explicit args (so we don't need a built
        # vector store to know the mode); fall back to inspecting a pre-built
        # store for backward compatibility (e.g. unit tests passing a fake).
        if embeddings is not None or is_auto is not None:
            self._embeddings = embeddings
            self._is_auto = bool(is_auto)
        else:
            self._embeddings = getattr(vector_store, "embeddings", None)
            self._is_auto = isinstance(self._embeddings, AutoEmbeddings)

        # Snapshot the active Atlas config once.
        from searchaas.config import load_config
        self._atlas_cfg_snapshot = load_config().atlas

        # Per-collection cache of `MongoDBGraphStore` instances for the graph
        # strategy. Building one attaches prompts + deep-copies the entity
        # schema, so we do it once per collection (keyed by name), not per
        # request. Mirrors the `_vs_cache` pattern.
        self._graph_stores: dict[str, MongoDBGraphStore] = {}

        # Per-instance LRU for vector stores built from UI overrides.
        self._vs_cache: dict[tuple, Any] = {}
        self._vs_cache_max = 16

        log.info(
            "RetrieverFactory init: vector_index=%r search_index=%r text_key=%r "
            "embedding_key=%r dimensions=%s hybrid_weights=%s filter_fields=%s "
            "fulltext_filter_fields=%s auto_embed=%s",
            self._vector_index, search_index, text_key, embedding_key, dimensions,
            self._hybrid_weights, self._filter_fields,
            "dynamic" if fulltext_filter_fields is None else fulltext_filter_fields,
            self._is_auto,
        )

    # ----------------------------------------------------------------- API

    def create(
        self,
        plan,
        overrides: _Overrides = None,
        retrieval_overrides: _Overrides = None,
    ) -> BaseRetriever:
        """
        Build a retriever for *plan*, applying optional per-request overrides.

        *overrides*           — AtlasOverrides  : collection, index, field names
        *retrieval_overrides* — RetrievalOverrides: hybrid weights, num_candidates
        """
        s = plan.strategy
        k = plan.top_k
        # Clamp filters to what the target index can actually filter on. The
        # plan is mutated so API responses report the filters that really ran.
        plan.filters = self._sanitize_for(s, plan.filters)
        # The per-request "RetrieverFactory: strategy=… overrides=…" line is
        # at DEBUG: model_dump() builds a Pydantic dict copy and the formatted
        # log line was 200+ chars even for trivial overrides. The downstream
        # `_build_*` methods log strategy-specific structured info anyway.
        if log.isEnabledFor(10):  # logging.DEBUG
            log.debug(
                "RetrieverFactory: strategy=%s k=%s filters=%s atlas_overrides=%s retrieval_overrides=%s",
                s, k, plan.filters,
                overrides.model_dump(exclude_none=True) if overrides else None,
                retrieval_overrides.model_dump(exclude_none=True) if retrieval_overrides else None,
            )

        if s == "vector":
            return self._build_vector(plan, k, overrides, retrieval_overrides)
        if s == "fulltext":
            return self._build_fulltext(plan, k, overrides)
        if s == "hybrid":
            return self._build_hybrid(plan, k, overrides, retrieval_overrides)
        if s == "parent_doc":
            return self._build_parent_doc(plan, k, overrides)
        if s == "graph":
            col = self._resolve_collection(overrides)
            return _GraphRAGRetriever(
                graph_store=self._get_graph_store(col),
                top_k=k,
            )
        if s == "metadata":
            return self._build_metadata(plan, k, overrides)

        raise ValueError(f"Unknown retrieval strategy: {s!r}")

    # ---- Metadata (structured find/$sort) -------------------------------- #
    def _build_metadata(self, plan, k: int, overrides: _Overrides = None) -> BaseRetriever:
        """Structured retrieval via a $match/$sort/$limit aggregation."""
        col = self._resolve_collection(overrides)
        text_key = (overrides and overrides.text_key) or self._text_key
        emb_key  = (overrides and overrides.embedding_key) or self._embedding_key
        log.info(
            "[MongoDB] metadata find/$sort — collection=%r filters=%s sort=%s limit=%s",
            col.name, plan.filters or None, getattr(plan, "sort", None) or None, k,
        )
        return _MetadataRetriever(
            collection=col,
            top_k=k,
            text_key=text_key,
            embedding_key=emb_key,
            filters=plan.filters or None,
            sort=getattr(plan, "sort", None) or None,
            max_time_ms=self._max_time_ms,
        )

    def _sanitize_for(self, strategy: str, filters: dict[str, Any] | None) -> dict[str, Any]:
        """Reduce filters to the paths the target index can filter on."""
        if not filters:
            return {}
        if strategy == "fulltext":
            if self._fulltext_filter_fields is None:  # dynamic mapping
                return dict(filters)
            return sanitize_filters(
                filters, self._fulltext_filter_fields,
                log=log, source=f"{strategy} plan",
            )
        # vector / hybrid / parent_doc all feed $vectorSearch pre_filter;
        # graph ignores filters, so dropping unknown paths is harmless there.
        return sanitize_filters(
            filters, self._filter_fields,
            log=log, source=f"{strategy} plan",
        )

    def evict_filter_field(self, field: str) -> None:
        """Remove *field* from the runtime vector-index filter allowlist.

        Called when Atlas rejects a pre-filter with "needs to be indexed as
        filter" — meaning the YAML config and the live Atlas index are out of
        sync. Evicting the field prevents every subsequent request from failing
        with the same error until the index is updated or the service restarts.
        """
        if field in self._filter_fields:
            self._filter_fields.remove(field)
            log.warning(
                "evicted %r from vector filter allowlist — Atlas index does not "
                "have this path indexed as {type: filter}. Update the Atlas index "
                "or remove it from atlas.vector_index_definition in searchaas.yaml.",
                field,
            )

    # ----------------------------------------------------------------- helpers

    def _resolve_collection(self, overrides: _Overrides):
        """Return the collection to query, respecting a UI collection override."""
        if overrides and overrides.collection and overrides.collection != self._col.name:
            log.debug("Using UI-overridden collection: %r", overrides.collection)
            return AtlasFactory.collection(overrides.collection)
        return self._col

    def _get_graph_store(self, collection: Any) -> MongoDBGraphStore:
        """
        Return a `MongoDBGraphStore` attached to *collection*, cached by name.

        We attach to an **existing** knowledge-graph collection (entity nodes
        with `_id`=name, `type`, `relationships.target_ids`) — we do NOT call
        `add_documents`, so no ingestion/LLM extraction runs here. `self._llm`
        (a `BaseChatModel`) is the `entity_extraction_model`, used server-side
        by `similarity_search` to pull entity names out of the query before the
        `$graphLookup` traversal.
        """
        cached = self._graph_stores.get(collection.name)
        if cached is not None:
            return cached
        log.info(
            "Building MongoDBGraphStore: collection=%r max_depth=%s",
            collection.name, _GRAPH_MAX_DEPTH,
        )
        store = MongoDBGraphStore(
            collection=collection,
            entity_extraction_model=self._llm,
            max_depth=_GRAPH_MAX_DEPTH,
        )
        self._graph_stores[collection.name] = store
        return store

    def _ensure_base_vector_store(self):
        """
        Build (once) and return the base `MongoDBAtlasVectorSearch`.

        Construction is deferred to first use so a non-vector deployment
        (graph/fulltext/metadata) never builds a vector store — and therefore
        never auto-creates a vector index on its collection. Thread-safe:
        concurrent uvicorn worker requests share one build.
        """
        if self._vs_built is not None:
            return self._vs_built
        with self._vs_lock:
            if self._vs_built is not None:  # double-checked under lock
                return self._vs_built
            if self._vs_provider is None:
                raise RuntimeError(
                    "This deployment was started without a vector store "
                    "(default_strategy is non-vector, e.g. graph/fulltext/metadata), "
                    "but a vector-based strategy (vector/hybrid/parent_doc) was "
                    "requested. Configure a vector-capable collection and set "
                    "default_strategy to vector/hybrid/parent_doc/auto."
                )
            self._vs_built = self._vs_provider()
            return self._vs_built

    def warm_vector_store(self) -> Any:
        """Eagerly build and return the base vector store (called at startup
        only when the configured default_strategy needs vectors, to preserve
        warm starts)."""
        return self._ensure_base_vector_store()

    def _resolve_vector_store(self, overrides: _Overrides):
        """
        Return a vector store, creating a temporary one when UI overrides
        differ from the container defaults (same embedder, different index /
        fields). Identical overrides reuse a cached store from `_vs_cache`
        so we don't open a fresh PyMongo client + rerun index validation on
        every request from the same UI session.
        """
        if not overrides:
            return self._ensure_base_vector_store()

        # Fast-path: no override actually differs from the container defaults.
        # `or` short-circuits as soon as one mismatch is found.
        if not (
            (overrides.collection    and overrides.collection    != self._col.name)
            or (overrides.vector_index  and overrides.vector_index  != self._vector_index)
            or (overrides.embedding_key and overrides.embedding_key != self._embedding_key)
            or (overrides.text_key      and overrides.text_key      != self._text_key)
        ):
            return self._ensure_base_vector_store()

        cfg = self._atlas_cfg_snapshot
        col_name  = overrides.collection    or self._col.name
        vi        = overrides.vector_index  or self._vector_index
        emb_key   = overrides.embedding_key or self._embedding_key
        text_key  = overrides.text_key      or self._text_key
        dims      = overrides.dimensions    or self._dimensions

        # AutoEmbeddings has strict constraints enforced by langchain-mongodb:
        # embedding_key MUST be None, dimensions MUST be -1, relevance_score_fn
        # MUST be None (the index owns similarity). The UI may still send legacy
        # values (e.g. embedding_key="embedding" from DEFAULT_CONFIG before
        # hydration completes) — coerce them here so we never pass illegal
        # values to MongoDBAtlasVectorSearch.from_connection_string.
        if self._is_auto:
            if emb_key is not None:
                log.debug("AutoEmbed: coercing embedding_key=%r -> None", emb_key)
            emb_key = None
            dims = -1
            relevance_score_fn_arg: str | None = None
        else:
            relevance_score_fn_arg = cfg.relevance_score_fn

        # Cache key is the full set of inputs that affect the constructed
        # store. Hits return immediately; misses build + evict oldest.
        cache_key = (col_name, vi, emb_key, text_key, dims, relevance_score_fn_arg)
        cached = self._vs_cache.get(cache_key)
        if cached is not None:
            return cached

        namespace = f"{cfg.database}.{col_name}"
        log.info(
            "Building temporary vector store: namespace=%r index=%r "
            "embedding_key=%r text_key=%r dims=%s auto=%s",
            namespace, vi, emb_key, text_key, dims, self._is_auto,
        )
        vs = MongoDBAtlasVectorSearch.from_connection_string(
            connection_string=cfg.uri,
            namespace=namespace,
            embedding=self._embeddings,
            index_name=vi,
            text_key=text_key,
            embedding_key=emb_key,
            relevance_score_fn=relevance_score_fn_arg,
            dimensions=dims,
        )

        # Evict oldest entry (dict preserves insertion order in CPython 3.7+).
        if len(self._vs_cache) >= self._vs_cache_max:
            oldest_key = next(iter(self._vs_cache))
            self._vs_cache.pop(oldest_key, None)
        self._vs_cache[cache_key] = vs
        return vs

    # ---- diagnostic helper used by `searchaas.diagnose` ------------------ #
    def run_vector(self, query: str, k: int = 5,
                   filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Run a vector search with full instrumentation and return a structured
        report. Catches the common failure modes so the caller knows whether
        the embedder, the index, or the query stage failed.
        """
        report: dict[str, Any] = {"stage": "init", "query": query, "k": k, "filters": filters or {}}
        # Detect AutoEmbeddings mode (precomputed at init).
        is_auto = self._is_auto
        report["auto_embed"] = is_auto

        # 1. embed the query (skipped for AutoEmbeddings — Atlas embeds server-side)
        if not is_auto:
            try:
                t0 = time.perf_counter()
                qvec = self._ensure_base_vector_store().embeddings.embed_query(query)
                report["embed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                report["query_dimensions"] = len(qvec) if hasattr(qvec, "__len__") else None
                report["stage"] = "embedded"
                log.info("vector-probe: embed dim=%s ms=%s", report["query_dimensions"], report["embed_ms"])
            except Exception as exc:
                log.exception("vector-probe: embedding failed")
                report["ok"] = False
                report["stage"] = "embedding"
                report["error_kind"] = exc.__class__.__name__
                report["error"] = str(exc)
                return report
        else:
            report["embed_ms"] = 0
            report["query_dimensions"] = None
            report["stage"] = "embedded_server_side"
            log.info("vector-probe: AutoEmbeddings — server-side embedding (no client embed)")

        # 2. compare against stored embedding dimensions (using configured key)
        emb_field = self._embedding_key
        report["embedding_key"] = emb_field
        if is_auto:
            # No client-side embedding field exists in docs; Atlas stores vectors
            # internally. Skip the stored-dimension check entirely.
            report["stored_dimensions"] = None
        else:
            try:
                _dim_filter = {emb_field: {"$exists": True}}
                log.info(
                    "[MongoDB] find_one — collection=%r filter=%s projection={%r: 1}",
                    self._col.name, _dim_filter, emb_field,
                )
                sample = self._col.find_one(
                    _dim_filter,
                    {emb_field: 1},
                )
                stored_dim = (
                    len(sample[emb_field])
                    if sample and isinstance(sample.get(emb_field), list) else None
                )
                report["stored_dimensions"] = stored_dim
                # Configured dimension check (also fail fast)
                if self._dimensions and self._dimensions != -1 and report["query_dimensions"] \
                        and self._dimensions != report["query_dimensions"]:
                    log.error("vector-probe: query=%s != configured atlas.dimensions=%s",
                              report["query_dimensions"], self._dimensions)
                    report["ok"] = False
                    report["stage"] = "dimension_mismatch"
                    report["error"] = (
                        f"Query embedder produced {report['query_dimensions']}-dim vectors "
                        f"but atlas.dimensions in YAML is {self._dimensions}. "
                        f"Fix either the embedder or atlas.dimensions."
                    )
                    return report
                if stored_dim is not None and report["query_dimensions"] is not None:
                    if stored_dim != report["query_dimensions"]:
                        log.error("vector-probe: DIMENSION MISMATCH query=%s stored=%s",
                                  report["query_dimensions"], stored_dim)
                        report["ok"] = False
                        report["stage"] = "dimension_mismatch"
                        report["error"] = (
                            f"Query embedder produced {report['query_dimensions']}-dim vectors "
                            f"but documents at field {emb_field!r} are stored with "
                            f"{stored_dim}-dim vectors. Switch embedding provider/model or re-index."
                        )
                        return report
            except Exception as exc:
                log.warning("vector-probe: could not sample stored embedding (%s)", exc)
                report["sample_warning"] = str(exc)

        # 3. invoke the retriever
        try:
            t0 = time.perf_counter()
            from searchaas.planning import RetrievalPlan
            plan = RetrievalPlan(strategy="vector", top_k=k, filters=filters or {})
            retriever = self._build_vector(plan, k)
            docs = retriever.invoke(query)
            report["search_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            report["result_count"] = len(docs)
            report["results"] = [
                {
                    "content": d.page_content[:200],
                    "metadata": {
                        mk: mv for mk, mv in d.metadata.items()
                        if mk != self._embedding_key
                    },
                }
                for d in docs[:5]
            ]
            report["ok"] = True
            report["stage"] = "done"
            log.info("vector-probe: %s results in %s ms", len(docs), report["search_ms"])
        except OperationFailure as exc:
            log.exception("vector-probe: $vectorSearch operation failed")
            report["ok"] = False
            report["stage"] = "search"
            report["error_kind"] = "OperationFailure"
            report["error"] = str(exc)
            report["hint"] = (
                "Atlas rejected the $vectorSearch stage. Common causes: "
                f"(a) vector index name mismatch (configured: {self._vector_index!r}), "
                "(b) index still building, "
                f"(c) numDimensions in the index != query vector dimensions "
                f"({report.get('query_dimensions')}), "
                f"(d) field path is not {self._embedding_key!r}."
            )
        except Exception as exc:
            log.exception("vector-probe: search failed")
            report["ok"] = False
            report["stage"] = "search"
            report["error_kind"] = exc.__class__.__name__
            report["error"] = str(exc)

        return report

    # ------------------------------------------------------------- builders

    # ---- Vector ---------------------------------------------------------- #
    def _build_vector(self, plan, k: int, overrides: _Overrides = None, retrieval_overrides: _Overrides = None) -> BaseRetriever:
        """
        Semantic retrieval via Atlas Vector Search.

        The bound `MongoDBAtlasVectorSearch` already carries:
          - index_name      : config.atlas.vector_index
          - text_key        : config.atlas.text_key
          - embedding_key   : config.atlas.embedding_key
          - dimensions      : config.atlas.dimensions

        Per-call `search_kwargs` only accepts:
          - k                    : number of results to return
          - pre_filter           : metadata filter applied before ANN
          - oversampling_factor  : candidates fetched per result (HNSW)
        """
        vs           = self._resolve_vector_store(overrides)
        vi           = (overrides and overrides.vector_index)  or self._vector_index
        emb_key      = (overrides and overrides.embedding_key) or self._embedding_key
        dims         = (overrides and overrides.dimensions)    or self._dimensions
        num_cands    = (retrieval_overrides and retrieval_overrides.num_candidates) or self._vector_candidates

        search_kwargs: dict[str, Any] = {
            "k": k,
            "oversampling_factor": _oversampling_factor(k, num_cands, bool(plan.filters)),
        }
        if plan.filters:
            search_kwargs["pre_filter"] = plan.filters

        log.info(
            "[MongoDB] $vectorSearch — index=%r path=%r numDimensions=%s "
            "numCandidates=%s limit=%s pre_filter=%s",
            vi, emb_key, dims, num_cands, k,
            search_kwargs.get("pre_filter"),
        )
        return vs.as_retriever(
            search_type="similarity",
            search_kwargs=search_kwargs,
        )

    # ---- Full-text ------------------------------------------------------- #
    def _build_fulltext(self, plan, k: int, overrides: _Overrides = None) -> BaseRetriever:
        """Lexical retrieval via Atlas Search ($search) — searches `text_key`.

        Uses `_FulltextRetriever` (a thin custom retriever) instead of
        `MongoDBAtlasFullTextSearchRetriever` so we can add a server-side
        `$project` that strips the embedding vector before it crosses the wire.
        """
        col          = self._resolve_collection(overrides)
        search_index = (overrides and overrides.search_index) or self._search_index
        text_key     = (overrides and overrides.text_key)     or self._text_key
        emb_key      = (overrides and overrides.embedding_key) or self._embedding_key

        log.info(
            "[MongoDB] $search — index=%r field=%r limit=%s filter=%s",
            search_index, text_key, k, plan.filters or None,
        )
        return _FulltextRetriever(
            collection=col,
            search_index=search_index,
            text_key=text_key,
            embedding_key=emb_key,
            k=k,
            filters=plan.filters or None,
            max_time_ms=self._max_time_ms,
        )

    # ---- Hybrid (native $rankFusion) ------------------------------------ #
    def _build_hybrid(self, plan, k: int, overrides: _Overrides = None, retrieval_overrides: _Overrides = None) -> BaseRetriever:
        """
        Fused vector + full-text retrieval using the native **`$rankFusion`**
        stage (MongoDB 8.0+). One stage does reciprocal rank fusion of the two
        ranked input pipelines server-side, replacing the ~15-stage portable
        RRF idiom (`$group $push $$ROOT` → `$unwind` → … → `$unionWith` → final
        `$group`/`$sort`).

            combination.weights = { vector: <vw>, text: <fw> }

        NOTE: no version gating — this always emits `$rankFusion`, which is
        supported on MongoDB 8.0+ (verified on the live 8.0.26 cluster). Only a
        pre-8.0 (7.x and older) cluster would reject the stage; the portable
        path (`_AutoEmbedHybridRetriever` / `MongoDBAtlasHybridSearchRetriever`)
        is retained for that case should a version-gated fallback ever be
        needed (see RankFusionPlan.md).

        Both embedder modes route through `_RankFusionHybridRetriever`; the only
        difference is the *vector* input pipeline:

          * AutoEmbeddings (server-side)  -> `$vectorSearch` with
            `query.text` + `model` (Atlas embeds the query internally).

          * Client-side embeddings        -> `embedder.embed_query(query)` then
            `$vectorSearch` with `queryVector` on the embedding field.
        """
        vs           = self._resolve_vector_store(overrides)
        vi           = (overrides and overrides.vector_index)  or self._vector_index
        search_index = (overrides and overrides.search_index)  or self._search_index
        text_key     = (overrides and overrides.text_key)      or self._text_key
        vw = float(
            (retrieval_overrides and retrieval_overrides.vector_weight) or
            self._hybrid_weights.get("vector_weight", 0.6)
        )
        fw = float(
            (retrieval_overrides and retrieval_overrides.fulltext_weight) or
            self._hybrid_weights.get("fulltext_weight", 0.4)
        )
        num_cands    = (retrieval_overrides and retrieval_overrides.num_candidates) or self._vector_candidates
        oversampling = _oversampling_factor(k, num_cands, bool(plan.filters))

        # `_is_auto` is the container-level mode; a vector_store rebuilt for
        # an override would not change embedder type, so we trust the
        # precomputed flag rather than `isinstance` per request.
        is_auto = self._is_auto

        if is_auto:
            log.info(
                "[MongoDB] $rankFusion hybrid (autoEmbed) — "
                "vector_index=%r search_index=%r field=%r model=%r "
                "vector_weight=%s fulltext_weight=%s limit=%s "
                "oversampling=%s pre_filter=%s",
                vi, search_index, text_key, self._embeddings.model,
                vw, fw, k, oversampling, plan.filters or None,
            )
            return _RankFusionHybridRetriever(
                collection=self._resolve_collection(overrides),
                vector_index=vi,
                search_index=search_index,
                text_key=text_key,
                model=self._embeddings.model,
                embeddings=None,
                embedding_key=None,
                k=k,
                vector_weight=vw,
                fulltext_weight=fw,
                pre_filter=plan.filters or None,
                oversampling_factor=oversampling,
                max_time_ms=self._max_time_ms,
            )

        emb_key = (overrides and overrides.embedding_key) or self._embedding_key
        dims    = (overrides and overrides.dimensions)    or self._dimensions
        log.info(
            "[MongoDB] $rankFusion hybrid (client-side embed) — "
            "vector_index=%r search_index=%r field=%r embedding=%r "
            "dim=%s vector_weight=%s fulltext_weight=%s limit=%s "
            "oversampling=%s pre_filter=%s",
            vi, search_index, text_key, emb_key, dims,
            vw, fw, k, oversampling, plan.filters or None,
        )
        return _RankFusionHybridRetriever(
            collection=self._resolve_collection(overrides),
            vector_index=vi,
            search_index=search_index,
            text_key=text_key,
            model=None,
            embeddings=getattr(vs, "embeddings", None) or self._embeddings,
            embedding_key=emb_key,
            k=k,
            vector_weight=vw,
            fulltext_weight=fw,
            pre_filter=plan.filters or None,
            oversampling_factor=oversampling,
            max_time_ms=self._max_time_ms,
        )

    # ---- Parent-document ------------------------------------------------- #
    def _build_parent_doc(self, plan, k: int, overrides: _Overrides = None) -> BaseRetriever:
        """
        Child-chunk match that returns the parent document.

        Parent/child ingestion (splitting + linking via `doc_id`) happens at
        index time; here we only construct the retriever bound to the same
        vectorstore.

        Both embedder modes use `_AutoEmbedParentDocRetriever` (a $vectorSearch
        child match + $lookup(doc_id -> _id) to the parent, in the same
        collection). This replaces langchain's `MongoDBAtlasParentDocumentRetriever`,
        which requires a separate `docstore`/`byte_store` (not how this data is
        laid out — parents live in the same collection) and calls `embed_query`
        (NotImplemented for AutoEmbeddings). The only difference is the vector
        stage: server-side `query.text`+`model` (auto) vs client `queryVector`.
        """
        col      = self._resolve_collection(overrides)
        vi       = (overrides and overrides.vector_index)  or self._vector_index
        text_key = (overrides and overrides.text_key)      or self._text_key
        oversampling = _oversampling_factor(k, self._vector_candidates, bool(plan.filters))

        if self._is_auto:
            log.info(
                "[MongoDB] parent-doc $vectorSearch (autoEmbed) — index=%r model=%r "
                "limit=%s oversampling=%s pre_filter=%s",
                vi, self._embeddings.model, k, oversampling, plan.filters or None,
            )
            return _AutoEmbedParentDocRetriever(
                collection=col,
                vector_index=vi,
                text_key=text_key,
                model=self._embeddings.model,
                k=k,
                pre_filter=plan.filters or None,
                oversampling_factor=oversampling,
            )

        emb_key = (overrides and overrides.embedding_key) or self._embedding_key
        # Ensure the base store exists (embedder lives on it) for the client path.
        vs = self._resolve_vector_store(overrides)
        log.info(
            "[MongoDB] parent-doc $vectorSearch (client embed) — index=%r embedding=%r "
            "limit=%s oversampling=%s pre_filter=%s",
            vi, emb_key, k, oversampling, plan.filters or None,
        )
        return _AutoEmbedParentDocRetriever(
            collection=col,
            vector_index=vi,
            text_key=text_key,
            model=None,
            embeddings=getattr(vs, "embeddings", None) or self._embeddings,
            embedding_key=emb_key,
            k=k,
            pre_filter=plan.filters or None,
            oversampling_factor=oversampling,
        )


# --------------------------------------------------------------------------- #
# GraphRAG retriever — langchain MongoDBGraphStore knowledge-graph traversal
# --------------------------------------------------------------------------- #
#
# Backed by langchain's `MongoDBGraphStore`, which expects an **entity-node**
# collection built per the MongoDB GraphRAG tutorial (each doc is an entity:
# `_id`=name, `type`, `attributes`, `relationships.target_ids`). We attach to a
# PRE-BUILT graph (no `add_documents` / ingestion here) and query it with
# `similarity_search(query)`:
#
#   1. LLM extracts entity NAMES from the query text.
#   2. `$graphLookup` traverses `relationships.target_ids -> _id`, up to
#      `max_depth` hops, collecting the connected entity nodes.
#
# It returns `Entity` dicts (no text, no score, no depth). We adapt each into a
# readable `Document` so the existing `serialize_docs -> summarize` path can
# synthesize an answer from the graph neighborhood.
class _GraphRAGRetriever(BaseRetriever):
    """GraphRAG retriever over a `MongoDBGraphStore` entity-node collection."""

    _store: Any = PrivateAttr()
    _top_k: int = PrivateAttr()

    def __init__(
        self,
        *,
        graph_store: Any,
        top_k: int = 20,
    ) -> None:
        super().__init__()
        self._store = graph_store
        self._top_k = top_k

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:  # type: ignore[override]
        try:
            # LLM query-entity extraction + $graphLookup traversal, server-side.
            entities = self._store.similarity_search(query) or []
        except Exception as exc:
            # Pointing at a non-graph collection (no matching entity nodes),
            # an LLM/extraction hiccup, or an aggregation error all land here —
            # degrade gracefully to an empty result rather than 500.
            log.warning("GraphRAG retrieval failed: %s", exc)
            return []

        docs: list[Document] = []
        for ent in entities[: self._top_k]:
            try:
                docs.append(self._entity_to_document(ent))
            except Exception:
                continue
        log.debug(
            "[MongoDB] GraphRAG similarity_search — entities=%d returned=%d",
            len(entities), len(docs),
        )
        return docs

    @staticmethod
    def _entity_to_document(entity: dict) -> Document:
        """Render a `MongoDBGraphStore` Entity dict into a readable Document.

        page_content is human/LLM-readable prose (name, type, attributes, and
        outgoing relationships) so `summarize` has real text to work with;
        the raw entity fields are preserved in metadata.
        """
        # BSON types -> JSON-serialisable for the API layer.
        try:
            make_serializable(entity)
        except Exception:
            pass

        name = entity.get("_id", "")
        etype = entity.get("type") or ""
        lines = [f"{name} ({etype})" if etype else str(name)]

        attributes = entity.get("attributes") or {}
        if isinstance(attributes, dict):
            for key, vals in attributes.items():
                rendered = ", ".join(map(str, vals)) if isinstance(vals, list) else str(vals)
                lines.append(f"{key}: {rendered}")

        rel = entity.get("relationships") or {}
        target_ids = rel.get("target_ids") or []
        rel_types = rel.get("types") or []
        for i, target in enumerate(target_ids):
            label = rel_types[i] if i < len(rel_types) else "related_to"
            lines.append(f"{label} -> {target}")

        return Document(
            page_content="\n".join(lines),
            metadata={
                "_id": name,
                "entity_type": etype,
                "attributes": attributes,
                "relationships": rel,
            },
            id=str(name) if name else None,
        )


# --------------------------------------------------------------------------- #
# Fulltext retriever — custom $search pipeline with server-side $project
# --------------------------------------------------------------------------- #
class _FulltextRetriever(BaseRetriever):
    """
    Atlas Search (`$search`) retriever that projects out the embedding vector
    **server-side** before the cursor sends bytes over the wire.

    `MongoDBAtlasFullTextSearchRetriever` from langchain-mongodb has no
    projection support — it returns full documents including the embedding
    array (4–24 KB per doc × top_k docs). This class replaces it with a
    direct aggregation that adds `{$project: {<embedding_key>: 0}}` after
    `$search`, keeping all other document fields intact.
    """

    _col: Any = PrivateAttr()
    _search_index: str = PrivateAttr()
    _text_key: str = PrivateAttr()
    _embedding_key: str = PrivateAttr()
    _k: int = PrivateAttr()
    _filters: dict | None = PrivateAttr()
    _max_time_ms: int | None = PrivateAttr(default=None)

    def __init__(
        self,
        *,
        collection: Any,
        search_index: str,
        text_key: str,
        embedding_key: str,
        k: int,
        filters: dict | None = None,
        max_time_ms: int | None = None,
    ) -> None:
        super().__init__()
        self._col = collection
        self._search_index = search_index
        self._text_key = text_key
        self._embedding_key = embedding_key
        self._k = k
        self._filters = filters or None
        self._max_time_ms = max_time_ms

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:  # type: ignore[override]
        search_stage: dict = {
            "$search": {
                "index": self._search_index,
                "text": {"query": query, "path": self._text_key},
            }
        }
        if self._filters:
            search_stage["$search"]["filter"] = self._filters  # type: ignore[index]

        # Project out embedding vectors server-side — avoids sending 4–24 KB of
        # floats per document over the wire only to discard them in Python.
        exclude: dict[str, int] = {self._embedding_key: 0}
        if self._embedding_key != "embedding":
            exclude["embedding"] = 0   # belt-and-suspenders for legacy field name

        pipeline: list[dict] = [
            search_stage,
            {"$project": exclude},
            {"$limit": self._k},
        ]

        log.debug(
            "[MongoDB] aggregate pipeline (fulltext) — collection=%r stages=%d",
            self._col.name, len(pipeline),
        )
        agg_kwargs: dict = {}
        if self._max_time_ms:
            agg_kwargs["maxTimeMS"] = self._max_time_ms

        try:
            docs: list[Document] = []
            for row in self._col.aggregate(pipeline, **agg_kwargs):
                try:
                    make_serializable(row)
                except Exception:
                    pass
                text = row.pop(self._text_key, "") or ""
                doc_id = row.get("_id")
                docs.append(Document(
                    page_content=text,
                    metadata=row,
                    id=str(doc_id) if doc_id is not None else None,
                ))
            return docs
        except Exception as exc:
            log.warning("Fulltext retrieval failed: %s", exc)
            return []


# --------------------------------------------------------------------------- #
# Metadata retriever — structured find/$sort (no vector/text search)
# --------------------------------------------------------------------------- #
class _MetadataRetriever(BaseRetriever):
    """Answer *structured* questions with a plain `$match`/`$sort`/`$limit`.

    For rankings, superlatives ("lowest rated"), and exact lookups where
    semantic similarity is the wrong tool. Executed as an aggregation (not
    `find`) so (a) the pipeline-capture listener records the real query and
    (b) a numeric-type guard can drop docs whose sort field is missing/empty
    (e.g. `imdb.rating == ""` in sample_mflix) before an ascending `$sort`.
    """

    _col: Any = PrivateAttr()
    _top_k: int = PrivateAttr()
    _text_key: str = PrivateAttr()
    _embedding_key: str = PrivateAttr()
    _filters: dict | None = PrivateAttr()
    _sort: dict | None = PrivateAttr()
    _max_time_ms: int | None = PrivateAttr(default=None)

    def __init__(
        self,
        *,
        collection: Any,
        top_k: int,
        text_key: str = "content",
        embedding_key: str = "embedding",
        filters: dict | None = None,
        sort: dict | None = None,
        max_time_ms: int | None = None,
    ) -> None:
        super().__init__()
        self._col = collection
        self._top_k = top_k
        self._text_key = text_key
        self._embedding_key = embedding_key
        self._filters = filters or None
        self._sort = sort or None
        self._max_time_ms = max_time_ms

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:  # type: ignore[override]
        pipeline: list[dict] = []
        if self._filters:
            pipeline.append({"$match": self._filters})
        if self._sort:
            sort_field = next(iter(self._sort))
            # Exclude missing / non-numeric sort values so empty strings ("") and
            # absent fields don't sort ahead of real numbers on ascending order.
            pipeline.append({"$match": {sort_field: {"$type": "number"}}})
            pipeline.append({"$sort": dict(self._sort)})
        pipeline.append({"$limit": self._top_k})
        # Project out the embedding vector server-side — it is stripped in
        # _to_document() anyway, but doing it here avoids sending 4–24 KB per
        # document over the wire before Python discards them.
        exclude: dict[str, int] = {self._embedding_key: 0}
        if self._embedding_key != "embedding":
            exclude["embedding"] = 0
        pipeline.append({"$project": exclude})

        log.debug(
            "[MongoDB] aggregate pipeline (metadata) — collection=%r stages=%d",
            self._col.name, len(pipeline),
        )
        agg_kwargs: dict = {}
        if self._max_time_ms:
            agg_kwargs["maxTimeMS"] = self._max_time_ms
        try:
            docs: list[Document] = []
            for row in self._col.aggregate(pipeline, **agg_kwargs):
                # Convert BSON types (ObjectId, Decimal128, Binary, …) to
                # JSON-serialisable values so the API response can be encoded.
                try:
                    make_serializable(row)
                except Exception:
                    pass
                docs.append(self._to_document(row))
            return docs
        except Exception as exc:
            log.warning("Metadata retrieval failed: %s", exc)
            return []

    def _to_document(self, row: dict) -> Document:
        meta = {k: v for k, v in row.items() if k not in (self._text_key, self._embedding_key)}
        return Document(page_content=row.get(self._text_key, "") or "", metadata=meta)


# --------------------------------------------------------------------------- #
# Native $rankFusion hybrid retriever (MongoDB 8.0+)
# --------------------------------------------------------------------------- #
#
# Emits a single `$rankFusion` stage that fuses two ranked input pipelines
# (vector + full-text) server-side via reciprocal rank fusion, replacing the
# ~15-stage portable RRF idiom. Handles BOTH embedder modes:
#
#   * AutoEmbeddings (server-side): the vector input pipeline is a
#     `$vectorSearch` with `query.text` + `model` (Atlas embeds the query),
#     pathing on `text_key`.
#
#   * Client-side embeddings: `embedder.embed_query(query)` is called once and
#     the vector input pipeline is a `$vectorSearch` with `queryVector`, pathing
#     on `embedding_key`.
#
# Pipeline shape:
#
#   { $rankFusion: {
#       input: { pipelines: {
#         vector: [ { $vectorSearch: {...} } ],
#         text:   [ { $search: {...} }, { $match: <pre_filter> }?, { $limit: k } ]
#       } },
#       combination: { weights: { vector: <vw>, text: <fw> } },
#       scoreDetails: true
#   } }
#   { $addFields: { score: {$meta: "score"}, score_details: {$meta: "scoreDetails"} } }
#   { $limit: k }
#
# The pre_filter is kept on BOTH channels (as `$vectorSearch.filter` and as a
# `$match` in the text pipeline) for parity with the portable RRF path.
class _RankFusionHybridRetriever(BaseRetriever):
    """Native `$rankFusion` hybrid retriever (MongoDB 8.0+), both embedder modes."""

    _col: Any = PrivateAttr()
    _vector_index: str = PrivateAttr()
    _search_index: str = PrivateAttr()
    _text_key: str = PrivateAttr()
    _model: str | None = PrivateAttr()
    _embeddings: Any = PrivateAttr()
    _embedding_key: str | None = PrivateAttr()
    _k: int = PrivateAttr()
    _vw: float = PrivateAttr()
    _fw: float = PrivateAttr()
    _pre_filter: dict | None = PrivateAttr()
    _oversampling: int = PrivateAttr()
    _max_time_ms: int | None = PrivateAttr(default=None)

    def __init__(
        self,
        *,
        collection: Any,
        vector_index: str,
        search_index: str,
        text_key: str,
        model: str | None,
        embeddings: Any,
        embedding_key: str | None,
        k: int,
        vector_weight: float,
        fulltext_weight: float,
        pre_filter: dict | None,
        oversampling_factor: int,
        max_time_ms: int | None = None,
    ) -> None:
        super().__init__()
        self._col = collection
        self._vector_index = vector_index
        self._search_index = search_index
        self._text_key = text_key
        self._model = model
        self._embeddings = embeddings
        self._embedding_key = embedding_key
        self._k = k
        self._vw = vector_weight
        self._fw = fulltext_weight
        self._pre_filter = pre_filter
        self._oversampling = max(1, oversampling_factor)
        self._max_time_ms = max_time_ms

    def _vector_pipeline(self, query: str) -> list[dict]:
        """The ranked vector input pipeline for `$rankFusion` (mode-dependent)."""
        if self._model is not None:
            # AutoEmbeddings: Atlas embeds `query.text` with `model` server-side;
            # the vector path is the *text* field.
            return [
                autoembedding_vector_search_stage(
                    query=query,
                    search_field=self._text_key,
                    index_name=self._vector_index,
                    model=self._model,
                    top_k=self._k,
                    filter=self._pre_filter,
                    oversampling_factor=self._oversampling,
                )
            ]
        # Client-side: embed once, then $vectorSearch on the embedding field.
        query_vector = self._embeddings.embed_query(query)
        return [
            vector_search_stage(
                query_vector=query_vector,
                search_field=self._embedding_key or "embedding",
                index_name=self._vector_index,
                top_k=self._k,
                filter=self._pre_filter,
                oversampling_factor=self._oversampling,
            )
        ]

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:  # type: ignore[override]
        # ---- Vector input pipeline (ranked by $vectorSearch) --------------
        vector_pipeline = self._vector_pipeline(query)

        # ---- Text input pipeline (ranked by $search) ----------------------
        # include_scores=False: $rankFusion ranks by the pipeline's own output
        # order, not a raw Lucene searchScore, so the dead `$set score` stage is
        # omitted. text_search_stage appends `$match <filter>` when a pre_filter
        # is present, keeping the filter on the text channel too.
        text_pipeline = text_search_stage(
            query=query,
            search_field=self._text_key,
            index_name=self._search_index,
            limit=self._k,
            filter=self._pre_filter,
            include_scores=False,
        )

        pipeline: list[dict] = [
            {
                "$rankFusion": {
                    "input": {
                        "pipelines": {
                            "vector": vector_pipeline,
                            "text": text_pipeline,
                        }
                    },
                    "combination": {"weights": {"vector": self._vw, "text": self._fw}},
                    # scoreDetails intentionally omitted — it adds Atlas-side CPU
                    # overhead computing per-channel breakdowns for every result.
                    # The fused score is still surfaced via {$meta: "score"}.
                }
            },
            {
                "$addFields": {
                    "score": {"$meta": "score"},
                }
            },
            {"$limit": self._k},
        ]

        log.debug(
            "[MongoDB] aggregate pipeline ($rankFusion hybrid) — collection=%r stages=%s",
            self._col.name, len(pipeline),
        )

        # ---- Execute + format ---------------------------------------------
        # `$rankFusion` returns each document with the fused relevance available
        # via `{$meta: "score"}` (projected into `score` above), replacing the
        # summed `vector_score + fulltext_score` of the portable path.
        _make_ser = make_serializable
        _Document = Document
        _text_key = self._text_key
        agg_kwargs: dict = {}
        if self._max_time_ms:
            agg_kwargs["maxTimeMS"] = self._max_time_ms
        docs: list[Document] = []
        for res in self._col.aggregate(pipeline, **agg_kwargs):
            if _text_key not in res:
                continue
            text = res.pop(_text_key)
            doc_id = res.get("_id")
            score = res.pop("score", 0.0)
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 0.0
            # Make ObjectId / Decimal / etc serialisable for the API layer.
            try:
                _make_ser(res)
            except Exception:
                pass
            res["score"] = score
            docs.append(_Document(
                page_content=text,
                metadata=res,
                id=str(doc_id) if doc_id is not None else None,
            ))
        return docs


# --------------------------------------------------------------------------- #
# AutoEmbed-compatible hybrid retriever  —  PORTABLE RRF FALLBACK (pre-8.0)
# --------------------------------------------------------------------------- #
#
# NOT wired into `_build_hybrid` anymore: `$rankFusion` (see
# `_RankFusionHybridRetriever`) supersedes this on MongoDB 8.0+. This class is
# retained intentionally as the portable fallback for **pre-8.0 clusters (7.x
# and older)**, which do not support `$rankFusion`. To re-enable it, version-gate
# `_build_hybrid` (server_version < (8, 0) → this retriever for the autoEmbed
# path, `MongoDBAtlasHybridSearchRetriever` for the client-side path). Kept in
# sync intentionally; do not delete without dropping pre-8.0 support.
#
# `MongoDBAtlasHybridSearchRetriever` from langchain-mongodb 0.11 hard-codes
# `vectorstore._embedding.embed_query(query)` and asserts on
# `vectorstore._embedding_key`, both of which are invalid when the vectorstore
# is backed by `AutoEmbeddings` (server-side embedding). This retriever
# replicates the same RRF algorithm but uses the server-side
# `autoembedding_vector_search_stage` so the embedding happens inside Atlas
# using the model declared in the autoEmbed index field.
#
# Pipeline shape (one stream per channel, fused via $unionWith + RRF):
#
#   vector channel:
#     $vectorSearch (autoEmbed, query.text)         -> top_k * oversampling docs
#     $group / $unwind / $addFields (RRF)           -> add vector_score
#
#   fulltext channel (via $unionWith):
#     $search (lucene index, text_key)              -> top_k docs
#     $group / $unwind / $addFields (RRF)           -> add fulltext_score
#
#   final stage:
#     $group on _id, $sum the per-channel scores, $sort desc, $limit
class _AutoEmbedHybridRetriever(BaseRetriever):
    """RRF hybrid retriever for AutoEmbeddings-mode vectorstores."""

    _col: Any = PrivateAttr()
    _vector_index: str = PrivateAttr()
    _search_index: str = PrivateAttr()
    _text_key: str = PrivateAttr()
    _model: str = PrivateAttr()
    _k: int = PrivateAttr()
    _vw: float = PrivateAttr()
    _fw: float = PrivateAttr()
    _pre_filter: dict | None = PrivateAttr()
    _oversampling: int = PrivateAttr()
    _vector_penalty: float = PrivateAttr(default=60.0)
    _fulltext_penalty: float = PrivateAttr(default=60.0)

    def __init__(
        self,
        *,
        collection: Any,
        vector_index: str,
        search_index: str,
        text_key: str,
        model: str,
        k: int,
        vector_weight: float,
        fulltext_weight: float,
        pre_filter: dict | None,
        oversampling_factor: int,
    ) -> None:
        super().__init__()
        self._col = collection
        self._vector_index = vector_index
        self._search_index = search_index
        self._text_key = text_key
        self._model = model
        self._k = k
        self._vw = vector_weight
        self._fw = fulltext_weight
        self._pre_filter = pre_filter
        self._oversampling = max(1, oversampling_factor)

    # Static across instances; constructed once at class-body time.
    _SCORES_FIELDS = ("vector_score", "fulltext_score")

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:  # type: ignore[override]
        # `autoembedding_vector_search_stage` and friends are now imported at
        # module top (see header), so no per-call import overhead.
        scores_fields = self._SCORES_FIELDS
        pipeline: list[dict] = []

        # ---- Vector channel (autoEmbed) -----------------------------------
        vector_pipeline = [
            autoembedding_vector_search_stage(
                query=query,
                search_field=self._text_key,
                index_name=self._vector_index,
                model=self._model,
                top_k=self._k,
                filter=self._pre_filter,
                oversampling_factor=self._oversampling,
            )
        ]
        vector_pipeline += reciprocal_rank_stage(
            score_field="vector_score",
            penalty=self._vector_penalty,
            weight=self._vw,
        )
        combine_pipelines(pipeline, vector_pipeline, self._col.name)

        # ---- Full-text channel ($search) ----------------------------------
        # include_scores=False: RRF ranks by array-index position, not the raw
        # Lucene searchScore, so the `$set score=$meta:searchScore` stage would
        # only inject a dead field that rides through the union/merge unused.
        text_pipeline = text_search_stage(
            query=query,
            search_field=self._text_key,
            index_name=self._search_index,
            limit=self._k,
            filter=self._pre_filter,
            include_scores=False,
        )
        text_pipeline.extend(
            reciprocal_rank_stage(
                score_field="fulltext_score",
                penalty=self._fulltext_penalty,
                weight=self._fw,
            )
        )
        combine_pipelines(pipeline, text_pipeline, self._col.name)

        # ---- Fuse + limit -------------------------------------------------
        pipeline.extend(final_hybrid_stage(scores_fields=scores_fields, limit=self._k))

        log.debug(
            "[MongoDB] aggregate pipeline (autoEmbed hybrid) — collection=%r stages=%s",
            self._col.name, len(pipeline),
        )

        # ---- Execute + format ---------------------------------------------
        # `make_serializable`, `Document`, `scores_fields` are all in the
        # enclosing scope — bind them to locals so the inner loop avoids
        # repeated `LOAD_GLOBAL` / attribute lookups (~10-15 % faster on
        # large result sets in our micro-bench).
        _make_ser = make_serializable
        _Document = Document
        _text_key = self._text_key
        _fields = scores_fields
        docs: list[Document] = []
        for res in self._col.aggregate(pipeline):
            if _text_key not in res:
                continue
            text = res.pop(_text_key)
            doc_id = res.get("_id")
            score = 0.0
            for s in _fields:
                v = res.pop(s, 0.0)
                if v:
                    score += float(v)
            # Make ObjectId / Decimal / etc serialisable for the API layer.
            try:
                _make_ser(res)
            except Exception:
                pass
            res["score"] = score
            docs.append(_Document(
                page_content=text,
                metadata=res,
                id=str(doc_id) if doc_id is not None else None,
            ))
        return docs


# --------------------------------------------------------------------------- #
# Parent-document retriever ($vectorSearch child match -> $lookup parent)
# --------------------------------------------------------------------------- #
class _AutoEmbedParentDocRetriever(BaseRetriever):
    """Parent-doc retriever for BOTH embedder modes.

    Replaces langchain's `MongoDBAtlasParentDocumentRetriever` (which requires a
    separate `docstore`/`byte_store` and calls `embed_query`, NotImplemented for
    AutoEmbeddings). It runs the `$vectorSearch` on child chunks, then `$lookup`s
    the parent via `doc_id -> _id` in the same collection and de-dupes.

    The only per-mode difference is the vector stage:

      * AutoEmbeddings (server-side): `$vectorSearch` with `query.text` + `model`,
        path = `text_key`.
      * Client-side embeddings: `embeddings.embed_query(query)` once, then
        `$vectorSearch` with `queryVector`, path = `embedding_key`.
    """

    _col: Any = PrivateAttr()
    _vector_index: str = PrivateAttr()
    _text_key: str = PrivateAttr()
    _model: str | None = PrivateAttr()
    _embeddings: Any = PrivateAttr()
    _embedding_key: str | None = PrivateAttr()
    _k: int = PrivateAttr()
    _pre_filter: dict | None = PrivateAttr()
    _oversampling: int = PrivateAttr()
    _id_key: str = PrivateAttr(default="doc_id")

    def __init__(
        self,
        *,
        collection: Any,
        vector_index: str,
        text_key: str,
        model: str | None,
        k: int,
        pre_filter: dict | None,
        oversampling_factor: int,
        embeddings: Any = None,
        embedding_key: str | None = None,
    ) -> None:
        super().__init__()
        self._col = collection
        self._vector_index = vector_index
        self._text_key = text_key
        self._model = model
        self._embeddings = embeddings
        self._embedding_key = embedding_key
        self._k = k
        self._pre_filter = pre_filter
        self._oversampling = max(1, oversampling_factor)

    def _child_vector_stage(self, query: str) -> dict:
        """The ranked $vectorSearch stage over child chunks (mode-dependent)."""
        if self._model is not None:
            # AutoEmbeddings: Atlas embeds `query.text` with `model`, path=text_key.
            return autoembedding_vector_search_stage(
                query=query,
                search_field=self._text_key,
                index_name=self._vector_index,
                model=self._model,
                top_k=self._k,
                filter=self._pre_filter,
                oversampling_factor=self._oversampling,
            )
        # Client-side: embed once, then queryVector on the embedding field.
        query_vector = self._embeddings.embed_query(query)
        return vector_search_stage(
            query_vector=query_vector,
            search_field=self._embedding_key or "embedding",
            index_name=self._vector_index,
            top_k=self._k,
            filter=self._pre_filter,
            oversampling_factor=self._oversampling,
        )

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:  # type: ignore[override]
        pipeline = [
            self._child_vector_stage(query),
            {"$set": {"score": {"$meta": "vectorSearchScore"}}},
            {
                "$lookup": {
                    "from": self._col.name,
                    "localField": self._id_key,
                    "foreignField": "_id",
                    "as": "parent_context",
                    "pipeline": [
                        {"$match": {f"metadata.{self._id_key}": {"$exists": False}}},
                    ],
                }
            },
            {"$unwind": {"path": "$parent_context"}},
            {
                "$group": {
                    "_id": "$parent_context._id",
                    "uniqueDocument": {"$first": "$parent_context"},
                }
            },
            {"$replaceRoot": {"newRoot": "$uniqueDocument"}},
        ]
        log.debug(
            "[MongoDB] aggregate pipeline (autoEmbed parent-doc) — collection=%r stages=%s",
            self._col.name, len(pipeline),
        )

        # Bind hot-loop locals (module-level names) to avoid LOAD_GLOBAL per row.
        _make_ser = make_serializable
        _Document = Document
        _text_key = self._text_key
        docs: list[Document] = []
        for res in self._col.aggregate(pipeline):
            if _text_key not in res:
                continue
            text = res.pop(_text_key)
            try:
                _make_ser(res)
            except Exception:
                pass
            docs.append(_Document(page_content=text, metadata=res))
        return docs
