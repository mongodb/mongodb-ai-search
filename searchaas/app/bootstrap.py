"""
Bootstrap container — builds every factory and service from `AppConfig`.

This is the single entry point used by the FastAPI app and the FastMCP server,
guaranteeing parity across both surfaces.

Flow:
    YAML  ->  AppConfig
              |
              +-- AtlasFactory (client / db / collection)
              |
              +-- EmbeddingFactory.create(...)  --+
              |                                   |
              +--- MongoDBAtlasVectorSearch <-----+
              |
              +-- LLMFactory.create(...)
              |
              +-- QueryUnderstandingLayer(llm)
              +-- PolicyStore + RetrievalPlanner(llm)
              +-- RetrieverFactory(vector_store, llm, collection, ...)
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from langchain_mongodb.embeddings import AutoEmbeddings
from langchain_mongodb.vectorstores import MongoDBAtlasVectorSearch

from searchaas.config import AppConfig, load_config
from searchaas.embeddings import EmbeddingFactory
from searchaas.infrastructure import AtlasFactory
from searchaas.llm import LLMFactory
from searchaas.observability import configure_logging, get_logger
# Side-effect import: register the pipeline-capture listener before the vector
# store's MongoClient is built in `_build_vector_store`.
from searchaas.observability import pipeline_capture as _pipeline_capture  # noqa: F401
from searchaas.planning import PolicyStore, RetrievalPlanner
from searchaas.query_understanding import FactStore, QueryUnderstandingLayer
from searchaas.retrieval import RetrieverFactory

configure_logging()
log = get_logger("searchaas.bootstrap")


@dataclass
class Container:
    config: AppConfig
    embeddings: Any
    llm: Any
    vector_store: Any
    collection: Any
    understanding: QueryUnderstandingLayer
    fact_store: FactStore
    policies: PolicyStore
    planner: RetrievalPlanner
    retrievers: RetrieverFactory


# Retrieval strategies that require an Atlas Vector Search index (and therefore
# a built vector store). Only these trigger vector-store construction — which,
# on a missing index, auto-creates it. Non-vector strategies (graph/fulltext/
# metadata) must never build the store, so a graph-only deployment never creates
# a spurious vector index on its collection.
_VECTOR_STRATEGIES = {"auto", "vector", "hybrid", "parent_doc"}


class ProviderIndexMismatch(RuntimeError):
    """Raised when `embeddings.provider` is incompatible with the live
    Atlas Vector Search index `atlas.vector_index`.

    Set SEARCHAAS_SKIP_PROVIDER_INDEX_CHECK=1 to bypass (dev only)."""


def _validate_provider_index_compat(
    config: AppConfig, collection: Any, is_auto: bool
) -> None:
    """Fail fast when provider <-> index type don't match.

    Two failure modes are caught here so the user sees a clear error at
    startup instead of a cryptic `queryVector must be present` from Atlas:

      1. `embeddings.provider: auto` (AutoEmbeddings) + the configured
         `vector_index` has only `type: vector` fields. AutoEmbeddings sends
         `query.text` + `model` in `$vectorSearch` and the server rejects
         that against a regular vector index because it expects `queryVector`.

      2. `embeddings.provider` is anything else (client-side) + the configured
         `vector_index` has only `type: autoEmbed` fields. Client-side
         embedding computes a `queryVector` but the autoEmbed index path
         doesn't store vectors at that path, so `$vectorSearch` returns nothing
         (or rejects).

    Additionally, for AutoEmbeddings we cross-check that the index's
    declared `model` matches `embeddings.config.model` (mismatch yields
    silently empty results because the query gets re-embedded with the
    wrong model).
    """
    if os.environ.get("SEARCHAAS_SKIP_PROVIDER_INDEX_CHECK") == "1":
        log.warning(
            "Skipping provider<->index compatibility check "
            "(SEARCHAAS_SKIP_PROVIDER_INDEX_CHECK=1). Use only in dev."
        )
        return

    name = config.atlas.vector_index
    try:
        indexes = list(collection.list_search_indexes())
    except Exception as exc:
        log.warning(
            "Provider<->index check: could not list search indexes (%s) — skipping",
            exc,
        )
        return

    idx = next((i for i in indexes if i.get("name") == name), None)
    if idx is None:
        # Index missing — handled by the soft preflight; don't double-error here.
        return
    if idx.get("type") != "vectorSearch":
        return  # also caught by soft preflight

    fields = (idx.get("latestDefinition") or {}).get("fields") or []
    field_types = {f.get("type") for f in fields if f.get("type") in ("vector", "autoEmbed")}
    has_vector    = "vector"    in field_types
    has_autoembed = "autoEmbed" in field_types

    if is_auto and has_vector and not has_autoembed:
        raise ProviderIndexMismatch(
            f"Configuration mismatch: embeddings.provider='auto' (AutoEmbeddings) "
            f"but the index {name!r} on {config.atlas.database}.{collection.name} "
            f"is a client-side `vector` index (no `autoEmbed` field). "
            f"This will cause Atlas to reject queries with "
            f"'queryVector must be present'. Fix by either: "
            f"(A) switching `embeddings.provider` to a client-side provider "
            f"(voyageai/openai/gemini/...) that matches the index's path "
            f"and dimensions; or "
            f"(B) pointing `atlas.vector_index` at an autoEmbed index "
            f"(e.g. 'autoembed_index') and setting `atlas.embedding_key: null`, "
            f"`atlas.dimensions: -1`, `atlas.relevance_score_fn: null`."
        )

    if not is_auto and has_autoembed and not has_vector:
        raise ProviderIndexMismatch(
            f"Configuration mismatch: embeddings.provider={config.embeddings.provider!r} "
            f"(client-side) but the index {name!r} on "
            f"{config.atlas.database}.{collection.name} is an `autoEmbed` "
            f"(server-side) index. Client-side embeddings won't query this index "
            f"correctly. Fix by either: "
            f"(A) switching `embeddings.provider` to `auto` and setting "
            f"`atlas.embedding_key: null`, `atlas.dimensions: -1`, "
            f"`atlas.relevance_score_fn: null`; or "
            f"(B) pointing `atlas.vector_index` at a client-side `vector` "
            f"index whose `path` matches `atlas.embedding_key`."
        )

    # AutoEmbed extra check: the model on the index must match what we send.
    if is_auto and has_autoembed:
        idx_model = next(
            (f.get("model") for f in fields if f.get("type") == "autoEmbed"), None,
        )
        cfg_model = config.embeddings.config.get("model")
        if idx_model and cfg_model and idx_model != cfg_model:
            raise ProviderIndexMismatch(
                f"Configuration mismatch: autoEmbed index {name!r} is built with "
                f"model={idx_model!r} but `embeddings.config.model`={cfg_model!r}. "
                f"Either change `embeddings.config.model` to {idx_model!r} or "
                f"rebuild the index with model={cfg_model!r}."
            )


def _preflight_vector_index(config: AppConfig, collection: Any, is_auto: bool = False) -> None:
    """
    Validate that the configured vector index actually indexes the configured
    `embedding_key` as a `vector` field — and that `atlas.dimensions` matches
    the index's `numDimensions`. Logs errors (not exceptions) so the app still
    starts in dev even when indexes are misconfigured.

    When `is_auto` is True (AutoEmbeddings), the index field type must be
    `autoEmbed` and its `path` must equal `atlas.text_key` (not `embedding_key`),
    because Atlas reads the source text from `text_key` and stores embeddings
    internally — there is no client-side `embedding_key`.
    """
    want_name = config.atlas.vector_index
    want_path = config.atlas.text_key if is_auto else config.atlas.embedding_key
    want_dim = config.atlas.dimensions
    try:
        indexes = list(collection.list_search_indexes())
    except Exception as exc:
        log.warning("VectorStore preflight: could not list search indexes (%s)", exc)
        return

    by_name = {i.get("name"): i for i in indexes}
    if want_name not in by_name:
        log.error(
            "VectorStore preflight: vector index %r NOT FOUND on %s.%s. Available: %s",
            want_name, config.atlas.database, collection.name,
            [i.get("name") for i in indexes],
        )
        return

    idx = by_name[want_name]
    if idx.get("type") != "vectorSearch":
        log.error("VectorStore preflight: index %r exists but type=%r (expected vectorSearch)",
                  want_name, idx.get("type"))
        return

    fields = (idx.get("latestDefinition") or {}).get("fields") or []
    # For AutoEmbeddings we require the `autoEmbed` field type; for client-side
    # embeddings we require the `vector` field type. The codebase historically
    # accepted both for either mode, but the underlying Atlas semantics differ.
    expected_types = ("autoEmbed",) if is_auto else ("vector", "autoEmbed")
    vector_fields = [f for f in fields if f.get("type") in expected_types]
    paths = [f.get("path") for f in vector_fields]
    if want_path not in paths:
        if is_auto:
            log.error(
                "VectorStore preflight: AutoEmbeddings mode requires a field of "
                "type='autoEmbed' with path=%r (== atlas.text_key) in index %r, "
                "but found path(s)=%s. Recreate the index with type='autoEmbed', "
                "path=%r, and the `model` you configured in embeddings.config.",
                want_path, want_name, paths, want_path,
            )
        else:
            log.error(
                "VectorStore preflight: index %r has vector path(s)=%s but config "
                "embedding_key=%r. Either change `atlas.embedding_key` in YAML to "
                "match the index, or recreate the index with path=%r.",
                want_name, paths, want_path, want_path,
            )
        return

    match = next(f for f in vector_fields if f.get("path") == want_path)
    idx_dim = match.get("numDimensions")
    log.info(
        "VectorStore preflight OK: index=%r path=%r type=%s numDimensions=%s similarity=%s model=%s",
        want_name, want_path, match.get("type"), idx_dim, match.get("similarity"),
        match.get("model"),
    )

    # Drift check: every filter field declared in the configured
    # vector_index_definition must actually be indexed as a filter in Atlas,
    # otherwise $vectorSearch will reject queries that use it.
    live_filter_paths = {f.get("path") for f in fields if f.get("type") == "filter"}
    missing = [p for p in config.atlas.filter_fields if p not in live_filter_paths]
    if missing:
        log.error(
            "VectorStore preflight: atlas.vector_index_definition declares filter "
            "field(s) %s but the live index %r only has filter path(s) %s. "
            "Update the index in Atlas or fix the definition in YAML.",
            missing, want_name, sorted(live_filter_paths) or "[]",
        )
    # For AutoEmbeddings, numDimensions is not present on the index field and
    # `atlas.dimensions` must be -1 (validated below) — skip the drift check.
    if not is_auto and want_dim and want_dim != -1 and idx_dim and want_dim != idx_dim:
        log.error(
            "VectorStore preflight: atlas.dimensions=%s but index `%s` has numDimensions=%s. "
            "Update `atlas.dimensions` in YAML to match the index, or rebuild the index.",
            want_dim, want_name, idx_dim,
        )


def _build_vector_store(config: AppConfig, embeddings: Any, collection: Any) -> Any:
    """
    Wire up MongoDBAtlasVectorSearch from the config.

    Uses the official `from_connection_string(...)` constructor so credentials,
    namespace, and embedding model are bound in one place, matching the
    pattern documented for `langchain-mongodb`.

    AutoEmbeddings mode (server-side embedding):
        When `embeddings` is an instance of langchain_mongodb.AutoEmbeddings,
        Atlas computes embeddings internally inside the vector index using the
        model declared in the `autoEmbed` field of the index definition.
        In this mode, langchain-mongodb REQUIRES:
            - embedding_key      == None
            - dimensions         == -1
            - relevance_score_fn == None
        These are coerced here regardless of YAML values (with a warning log) so
        a misconfigured YAML cannot break startup.
    """
    namespace = f"{config.atlas.database}.{config.atlas.collection}"
    is_auto = isinstance(embeddings, AutoEmbeddings)
    log.info(
        "VectorStore: building namespace=%s index=%s text_key=%s embedding_key=%s "
        "dimensions=%s auto_embed=%s",
        namespace,
        config.atlas.vector_index,
        config.atlas.text_key,
        config.atlas.embedding_key,
        config.atlas.dimensions,
        is_auto,
    )

    # Hard guard: catch provider <-> index type mismatches at startup with a
    # clear error instead of a cryptic "queryVector must be present" later.
    _validate_provider_index_compat(config, collection, is_auto)

    _preflight_vector_index(config, collection, is_auto=is_auto)

    # `auto_index_timeout` (default 15s) governs how long langchain-mongodb waits
    # for an auto-created Atlas Vector Search index to become queryable.
    # Real-world creation can take 1-5 minutes on a freshly-built collection,
    # so we bump it to 5 minutes. Override via SEARCHAAS_INDEX_TIMEOUT_S env var.
    auto_index_timeout = int(os.environ.get("SEARCHAAS_INDEX_TIMEOUT_S", "300"))

    if is_auto:
        # Coerce all AutoEmbeddings-incompatible YAML values to safe defaults and
        # warn the operator if the YAML disagrees with these constraints.
        if config.atlas.embedding_key is not None:
            log.warning(
                "AutoEmbeddings: ignoring atlas.embedding_key=%r — must be null for "
                "server-side embedding. Set `atlas.embedding_key: null` in YAML.",
                config.atlas.embedding_key,
            )
        if config.atlas.dimensions not in (-1, None):
            log.warning(
                "AutoEmbeddings: ignoring atlas.dimensions=%s — must be -1 for "
                "server-side embedding. Set `atlas.dimensions: -1` in YAML.",
                config.atlas.dimensions,
            )
        if config.atlas.relevance_score_fn is not None:
            log.warning(
                "AutoEmbeddings: ignoring atlas.relevance_score_fn=%r — must be null "
                "for server-side embedding (similarity is read from the index). "
                "Set `atlas.relevance_score_fn: null` in YAML.",
                config.atlas.relevance_score_fn,
            )
        embedding_key_arg: str | None = None
        dimensions_arg: int = -1
        relevance_score_fn_arg: str | None = None
    else:
        embedding_key_arg = config.atlas.embedding_key
        dimensions_arg = config.atlas.dimensions
        relevance_score_fn_arg = config.atlas.relevance_score_fn

    # Reuse the AtlasFactory-managed MongoClient (which already has
    # connection-pool tuning, appname, and serverSelectionTimeoutMS set)
    # instead of letting MongoDBAtlasVectorSearch open a second bare
    # MongoClient to the same cluster via from_connection_string().
    # This halves the number of connection pools held against Atlas.
    existing_collection = AtlasFactory.chunks_collection()
    vs = MongoDBAtlasVectorSearch(
        collection=existing_collection,
        embedding=embeddings,
        index_name=config.atlas.vector_index,
        text_key=config.atlas.text_key,
        embedding_key=embedding_key_arg,
        relevance_score_fn=relevance_score_fn_arg,
        dimensions=dimensions_arg,
        auto_index_timeout=auto_index_timeout,
    )
    return vs


def build_container(config: AppConfig | None = None) -> Container:
    cfg = config or load_config()
    log.info("Container: building with embeddings=%s llm=%s default_strategy=%s",
             cfg.embeddings.provider, cfg.planner.llm_provider, cfg.retrieval.default_strategy)

    # --- Atlas ---
    collection = AtlasFactory.chunks_collection()

    # --- Embeddings (query-time) ---
    embeddings = EmbeddingFactory.create(
        provider=cfg.embeddings.provider,
        config=cfg.embeddings.config,
    )

    # --- LLM (planner + query understanding) ---
    llm = LLMFactory.create(
        provider=cfg.planner.llm_provider,
        config=cfg.planner.config,
    )

    # --- Vector store (DEFERRED) ---
    # Do NOT build the vector store here — its construction auto-creates the
    # Atlas vector index on the collection, which must not happen for a
    # non-vector deployment (graph/fulltext/metadata). Hand the factory a
    # provider; it builds lazily on first use by a vector-needing strategy.
    # We warm it below only when the configured default_strategy needs vectors.
    vector_store_provider = lambda: _build_vector_store(cfg, embeddings, collection)

    # Detect AutoEmbeddings mode for downstream retrievers (raw $vectorSearch
    # aggregations must use `text_key` as the index path, and a `null` filter on
    # the embedding field is meaningless since the field doesn't exist in docs).
    # Derived from `embeddings` directly so it holds even when the vector store
    # is never built.
    is_auto = isinstance(embeddings, AutoEmbeddings)

    # --- Query Understanding + Planning ---
    # Namespace the cache by the config that drives extraction (filter_fields +
    # field_aliases) so changing either invalidates stale extractions.
    field_aliases = cfg.query_understanding.field_aliases
    ns_parts = [",".join(sorted(cfg.atlas.filter_fields))]
    ns_parts += [f"{k}={'|'.join(v)}" for k, v in sorted(field_aliases.items())]
    fact_store = FactStore(namespace=";".join(ns_parts))
    understanding = QueryUnderstandingLayer(
        llm=llm,
        allowed_filter_fields=cfg.atlas.filter_fields,
        field_aliases=field_aliases,
        fact_store=fact_store,
    )
    policies = PolicyStore(default_strategy=cfg.retrieval.default_strategy)
    planner = RetrievalPlanner(
        llm=llm,
        policy_store=policies,
        default_top_k=cfg.planner.default_top_k,
    )

    # --- Retrievers ---
    # In AutoEmbeddings mode the index `path` is the text field itself (Atlas
    # embeds it server-side), so any raw `$vectorSearch` stage emitted by the
    # retrievers must use `text_key` as its `path`. We surface that to the
    # factory via `embedding_key`.
    retriever_embedding_key = cfg.atlas.text_key if is_auto else (cfg.atlas.embedding_key or "embedding")
    retrievers = RetrieverFactory(
        vector_store_provider=vector_store_provider,
        embeddings=embeddings,
        is_auto=is_auto,
        llm=llm,
        collection=collection,
        vector_index=cfg.atlas.vector_index,
        search_index=cfg.atlas.search_index,
        text_key=cfg.atlas.text_key,
        embedding_key=retriever_embedding_key,
        dimensions=-1 if is_auto else cfg.atlas.dimensions,
        hybrid_weights=cfg.retrieval.hybrid or {},
        vector_num_candidates=int((cfg.retrieval.vector or {}).get("num_candidates", 200)),
        filter_fields=cfg.atlas.filter_fields,
        fulltext_filter_fields=cfg.atlas.search_filter_fields,
        max_time_ms=cfg.retrieval.effective_max_time_ms,
    )

    # Warm the vector store at startup ONLY when the configured strategy needs
    # it — preserving today's warm-start latency for vector deployments while
    # ensuring graph/fulltext/metadata deployments never build it (and never
    # auto-create a vector index on their collection). A vector endpoint hit on
    # a non-vector deployment still builds it lazily on that first request.
    if cfg.retrieval.default_strategy in _VECTOR_STRATEGIES:
        vector_store = retrievers.warm_vector_store()
    else:
        vector_store = None
        log.info(
            "Vector store: construction deferred — default_strategy=%r does not "
            "require vectors (no vector index will be created on %s.%s unless a "
            "vector/hybrid/parent_doc request is made).",
            cfg.retrieval.default_strategy, cfg.atlas.database, collection.name,
        )

    return Container(
        config=cfg,
        embeddings=embeddings,
        llm=llm,
        vector_store=vector_store,
        collection=collection,
        understanding=understanding,
        fact_store=fact_store,
        policies=policies,
        planner=planner,
        retrievers=retrievers,
    )


_container: Container | None = None
_container_lock = threading.Lock()


def get_container() -> Container:
    """Return the process-wide singleton container, building it on first call."""
    global _container
    if _container is not None:
        return _container
    with _container_lock:
        if _container is None:
            _container = build_container()
    return _container


def reset_container(cfg: AppConfig | None = None) -> Container:
    """
    Rebuild and replace the process-wide container.

    Used by `POST /settings` to apply new embeddings provider, LLM, or atlas
    settings without restarting the process.  Thread-safe: in-flight requests
    keep the old container reference until they complete.
    """
    global _container
    log.info("reset_container: rebuilding with cfg=%s", cfg.embeddings.provider if cfg else "default")
    new = build_container(cfg)
    with _container_lock:
        _container = new
    return new
