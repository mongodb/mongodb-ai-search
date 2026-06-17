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

import time
from typing import Any

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
    MongoDBAtlasHybridSearchRetriever,
    MongoDBAtlasParentDocumentRetriever,
)
from langchain_mongodb.vectorstores import MongoDBAtlasVectorSearch
from langchain_mongodb.utils import make_serializable
from pymongo.errors import OperationFailure
from pymongo_search_utils.pipeline import (
    autoembedding_vector_search_stage,
    combine_pipelines,
    final_hybrid_stage,
    reciprocal_rank_stage,
    text_search_stage,
)

from searchaas.filtering import sanitize_filters
from searchaas.infrastructure import AtlasFactory
from searchaas.observability import get_logger

log = get_logger("searchaas.retrieval")


# Forward reference — AtlasOverrides is defined in api/app.py but we accept
# it here as Any to avoid a circular import.  Type checkers see it as Any.
_Overrides = Any  # AtlasOverrides | None


class RetrieverFactory:
    """Build a base retriever for the requested strategy."""

    def __init__(
        self,
        vector_store: Any,
        llm: Any,
        collection: Any,
        *,
        vector_index: str | None = None,
        search_index: str = "default",
        text_key: str = "content",
        embedding_key: str = "embedding",
        dimensions: int = -1,
        hybrid_weights: dict[str, float] | None = None,
        vector_num_candidates: int = 200,
        filter_fields: list[str] | None = None,
        fulltext_filter_fields: list[str] | None = None,
    ) -> None:
        self._vs = vector_store
        self._llm = llm
        self._col = collection
        # `MongoDBAtlasVectorSearch` stores this as `_index_name`; we accept
        # it explicitly so we don't reach into a private attribute.
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

        # Precompute hot-path flags so we don't call `isinstance` and
        # `getattr` chains on every request.
        self._is_auto = isinstance(getattr(vector_store, "embeddings", None), AutoEmbeddings)
        self._embeddings = getattr(vector_store, "embeddings", None)

        # Snapshot the active Atlas config once. Previously every override
        # request called `load_config().atlas` — even though `load_config`
        # is `lru_cache(maxsize=1)`, taking the `.atlas` attribute and using
        # it across the rest of the function still cost us a function call
        # and attribute chain per request. Container rebuilds rebuild the
        # factory, so this snapshot is always coherent with the live container.
        from searchaas.config import load_config
        self._atlas_cfg_snapshot = load_config().atlas

        # Per-instance LRU for vector stores built from UI overrides. The UI
        # tends to send the same overrides for an entire chat session, so a
        # tiny LRU eliminates redundant `MongoDBAtlasVectorSearch.from_connection_string`
        # calls (which open a new pymongo client connection pool and rerun
        # index validation).
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
            return _GraphRAGRetriever(
                collection=self._resolve_collection(overrides),
                top_k=k,
                text_key=(overrides and overrides.text_key) or self._text_key,
                embedding_key=(overrides and overrides.embedding_key) or self._embedding_key,
            )

        raise ValueError(f"Unknown retrieval strategy: {s!r}")

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

    # ----------------------------------------------------------------- helpers

    def _resolve_collection(self, overrides: _Overrides):
        """Return the collection to query, respecting a UI collection override."""
        if overrides and overrides.collection and overrides.collection != self._col.name:
            log.debug("Using UI-overridden collection: %r", overrides.collection)
            return AtlasFactory.collection(overrides.collection)
        return self._col

    def _resolve_vector_store(self, overrides: _Overrides):
        """
        Return a vector store, creating a temporary one when UI overrides
        differ from the container defaults (same embedder, different index /
        fields). Identical overrides reuse a cached store from `_vs_cache`
        so we don't open a fresh PyMongo client + rerun index validation on
        every request from the same UI session.
        """
        if not overrides:
            return self._vs

        # Fast-path: no override actually differs from the container defaults.
        # `or` short-circuits as soon as one mismatch is found.
        if not (
            (overrides.collection    and overrides.collection    != self._col.name)
            or (overrides.vector_index  and overrides.vector_index  != self._vector_index)
            or (overrides.embedding_key and overrides.embedding_key != self._embedding_key)
            or (overrides.text_key      and overrides.text_key      != self._text_key)
        ):
            return self._vs

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
                qvec = self._vs.embeddings.embed_query(query)
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
            "oversampling_factor": max(1, num_cands // max(k, 1)),
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
        """Lexical retrieval via Atlas Search ($search) — searches `text_key`."""
        col          = self._resolve_collection(overrides)
        search_index = (overrides and overrides.search_index) or self._search_index
        text_key     = (overrides and overrides.text_key)     or self._text_key

        log.info(
            "[MongoDB] $search — index=%r field=%r limit=%s filter=%s",
            search_index, text_key, k, plan.filters or None,
        )
        return MongoDBAtlasFullTextSearchRetriever(
            collection=col,
            search_index_name=search_index,
            search_field=text_key,
            k=k,
            filter=plan.filters or None,
        )

    # ---- Hybrid (RRF + weights) ----------------------------------------- #
    def _build_hybrid(self, plan, k: int, overrides: _Overrides = None, retrieval_overrides: _Overrides = None) -> BaseRetriever:
        """
        Fused vector + full-text retrieval using Reciprocal Rank Fusion (RRF).

            score = vector_weight   / (vector_penalty   + rank_vector)
                  + fulltext_weight / (fulltext_penalty + rank_fulltext)

        Two implementations are dispatched based on the embedder type:

          * Client-side embeddings  -> `MongoDBAtlasHybridSearchRetriever`
            (calls `embedder.embed_query(query)` and uses `queryVector` in
            `$vectorSearch`).

          * AutoEmbeddings (server-side)  -> `_AutoEmbedHybridRetriever`
            (uses `$vectorSearch.query.text` so Atlas embeds the query
            internally — `embed_query` is NotImplemented for AutoEmbeddings
            and would otherwise raise).
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

        # `_is_auto` is the container-level mode; a vector_store rebuilt for
        # an override would not change embedder type, so we trust the
        # precomputed flag rather than `isinstance` per request.
        is_auto = self._is_auto

        if is_auto:
            num_cands = (retrieval_overrides and retrieval_overrides.num_candidates) or self._vector_candidates
            oversampling = max(1, num_cands // max(k, 1))
            log.info(
                "[MongoDB] $vectorSearch (autoEmbed) + $search hybrid (RRF) — "
                "vector_index=%r search_index=%r field=%r model=%r "
                "vector_weight=%s fulltext_weight=%s limit=%s "
                "oversampling=%s pre_filter=%s",
                vi, search_index, text_key, self._embeddings.model,
                vw, fw, k, oversampling, plan.filters or None,
            )
            return _AutoEmbedHybridRetriever(
                collection=self._resolve_collection(overrides),
                vector_index=vi,
                search_index=search_index,
                text_key=text_key,
                model=self._embeddings.model,
                k=k,
                vector_weight=vw,
                fulltext_weight=fw,
                pre_filter=plan.filters or None,
                oversampling_factor=oversampling,
            )

        emb_key = (overrides and overrides.embedding_key) or self._embedding_key
        dims    = (overrides and overrides.dimensions)    or self._dimensions
        log.info(
            "[MongoDB] $vectorSearch+$search hybrid (RRF) — "
            "vector_index=%r search_index=%r field=%r embedding=%r "
            "dim=%s vector_weight=%s fulltext_weight=%s limit=%s pre_filter=%s",
            vi, search_index, text_key, emb_key, dims,
            vw, fw, k, plan.filters or None,
        )
        return MongoDBAtlasHybridSearchRetriever(
            vectorstore=vs,
            search_index_name=search_index,
            k=k,
            vector_weight=vw,
            fulltext_weight=fw,
            pre_filter=plan.filters or None,
        )

    # ---- Parent-document ------------------------------------------------- #
    def _build_parent_doc(self, plan, k: int, overrides: _Overrides = None) -> BaseRetriever:
        """
        Child-chunk match that returns the parent document.

        Parent/child ingestion (splitting + linking via `doc_id`) happens at
        index time; here we only construct the retriever bound to the same
        vectorstore.

        AutoEmbeddings mode uses `_AutoEmbedParentDocRetriever` because the
        official `MongoDBAtlasParentDocumentRetriever` calls
        `embedder.embed_query` directly (NotImplemented for AutoEmbeddings).
        """
        vs       = self._resolve_vector_store(overrides)
        col      = self._resolve_collection(overrides)
        vi       = (overrides and overrides.vector_index)  or self._vector_index
        text_key = (overrides and overrides.text_key)      or self._text_key

        if self._is_auto:
            oversampling = max(1, self._vector_candidates // max(k, 1))
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
        log.info(
            "[MongoDB] parent-doc $vectorSearch — index=%r embedding=%r "
            "limit=%s pre_filter=%s",
            vi, emb_key, k, plan.filters or None,
        )
        return MongoDBAtlasParentDocumentRetriever(
            vectorstore=vs,
            collection=col,
            search_kwargs={"k": k, "pre_filter": plan.filters or None},
        )


# --------------------------------------------------------------------------- #
# GraphRAG retriever — Atlas as the knowledge graph via $graphLookup
# --------------------------------------------------------------------------- #


class _GraphRAGRetriever(BaseRetriever):
    """
    Multi-hop graph retrieval using `$graphLookup` over the chunks collection.

    Looks up seed chunks matching the query, then traverses `entities` to
    pull in connected chunks (one hop by default). Falls back gracefully if
    the collection lacks an entity graph.
    """

    _col: Any = PrivateAttr()
    _top_k: int = PrivateAttr()
    _max_depth: int = PrivateAttr()
    _text_key: str = PrivateAttr()
    _embedding_key: str = PrivateAttr()
    _text_index_result: Any = PrivateAttr(default=None)   # None = not yet checked

    def __init__(
        self,
        *,
        collection: Any,
        top_k: int = 20,
        max_depth: int = 1,
        text_key: str = "content",
        embedding_key: str = "embedding",
    ) -> None:
        super().__init__()
        self._col = collection
        self._top_k = top_k
        self._max_depth = max_depth
        self._text_key = text_key
        self._embedding_key = embedding_key

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:  # type: ignore[override]
        try:
            match_stage = (
                {"$match": {"$text": {"$search": query}}}
                if self._has_text_index()
                else {"$match": {self._text_key: {"$regex": query, "$options": "i"}}}
            )
            pipeline = [
                match_stage,
                {"$limit": max(self._top_k, 10)},
                {
                    "$graphLookup": {
                        "from": self._col.name,
                        "startWith": "$entities",
                        "connectFromField": "entities",
                        "connectToField": "entities",
                        "as": "connected",
                        "maxDepth": self._max_depth,
                    }
                },
                {"$limit": self._top_k},
            ]
            log.debug(
                "[MongoDB] aggregate pipeline — collection=%r stages=%s",
                self._col.name,
                pipeline,
            )
            docs: list[Document] = []
            for row in self._col.aggregate(pipeline):
                docs.append(self._to_document(row))
                for hop in row.get("connected", [])[: self._top_k]:
                    docs.append(self._to_document(hop))
            # de-dup by content
            seen, unique = set(), []
            for d in docs:
                key = (d.metadata.get("_id"), d.page_content[:120])
                if key in seen:
                    continue
                seen.add(key)
                unique.append(d)
                if len(unique) >= self._top_k:
                    break
            return unique
        except Exception as exc:
            log.warning("GraphRAG retrieval failed: %s", exc)
            return []

    def _has_text_index(self) -> bool:
        """Check once whether the collection has a text index; cache the result."""
        if self._text_index_result is not None:
            return self._text_index_result
        result = False
        try:
            result = any(
                any(v == "text" for v in (idx.get("key") or {}).values())
                for idx in self._col.list_indexes()
            )
        except Exception:
            result = False
        self._text_index_result = result
        return result

    def _to_document(self, row: dict) -> Document:
        meta = {
            k: v for k, v in row.items()
            if k not in (self._text_key, self._embedding_key, "connected")
        }
        return Document(page_content=row.get(self._text_key, ""), metadata=meta)


# --------------------------------------------------------------------------- #
# AutoEmbed-compatible hybrid retriever
# --------------------------------------------------------------------------- #
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
        text_pipeline = text_search_stage(
            query=query,
            search_field=self._text_key,
            index_name=self._search_index,
            limit=self._k,
            filter=self._pre_filter,
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
# AutoEmbed-compatible parent-document retriever
# --------------------------------------------------------------------------- #
class _AutoEmbedParentDocRetriever(BaseRetriever):
    """Parent-doc retriever for AutoEmbeddings-mode vectorstores.

    Mirrors `MongoDBAtlasParentDocumentRetriever` but issues the server-side
    `$vectorSearch` (with `query.text` + `model`) instead of embedding the
    query client-side. After the child match, it `$lookup`s the parent doc
    via `doc_id` and de-dupes.
    """

    _col: Any = PrivateAttr()
    _vector_index: str = PrivateAttr()
    _text_key: str = PrivateAttr()
    _model: str = PrivateAttr()
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
        model: str,
        k: int,
        pre_filter: dict | None,
        oversampling_factor: int,
    ) -> None:
        super().__init__()
        self._col = collection
        self._vector_index = vector_index
        self._text_key = text_key
        self._model = model
        self._k = k
        self._pre_filter = pre_filter
        self._oversampling = max(1, oversampling_factor)

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:  # type: ignore[override]
        # `autoembedding_vector_search_stage` is module-level.
        pipeline = [
            autoembedding_vector_search_stage(
                query=query,
                search_field=self._text_key,
                index_name=self._vector_index,
                model=self._model,
                top_k=self._k,
                filter=self._pre_filter,
                oversampling_factor=self._oversampling,
            ),
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
