"""
Atlas Factory — single source of truth for MongoDB Atlas clients,
databases, and collections used across the SearchaaS platform.

Adds structured logging + an explicit `ping()` so the diagnostic CLI and
the API can pinpoint connection / auth / DNS failures without waiting
for the first query to fail.
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import (
    ConfigurationError,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from searchaas.config import load_config
from searchaas.observability import get_logger
# Side-effect import: registers the global pipeline-capture CommandListener
# BEFORE any MongoClient is constructed here, so it observes every client
# (including the langchain vector store's separate client).
from searchaas.observability import pipeline_capture as _pipeline_capture  # noqa: F401

log = get_logger("searchaas.infrastructure.atlas")


# Named collections used at runtime.
COLLECTIONS: dict[str, str] = {
    "chunks":             "knowledge_chunks",
    "retrieval_policies": "retrieval_policies",
    "query_facts":        "query_facts",
}


def _redact(uri: str) -> str:
    """Hide credentials in URIs for log output."""
    try:
        if "@" in uri and "://" in uri:
            scheme, rest = uri.split("://", 1)
            creds, host = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host}"
    except Exception:
        pass
    return uri


class AtlasFactory:
    """Single source of truth for Atlas clients, databases, and collections."""

    @staticmethod
    @lru_cache(maxsize=1)
    def client() -> MongoClient:
        cfg = load_config().atlas
        log.info("Atlas: connecting to %s", _redact(cfg.uri))
        try:
            client = MongoClient(
                cfg.uri,
                appname="searchaas",
                serverSelectionTimeoutMS=8000,
            )
        except ConfigurationError as exc:
            log.error("Atlas: invalid connection string — %s", exc)
            raise
        return client

    @classmethod
    def db(cls) -> Database:
        cfg = load_config().atlas
        return cls.client()[cfg.database]

    @classmethod
    def collection(cls, name: str) -> Collection:
        """Return a collection by logical (COLLECTIONS key) or raw name."""
        physical = COLLECTIONS.get(name, name)
        return cls.db()[physical]

    @classmethod
    def chunks_collection(cls) -> Collection:
        cfg = load_config().atlas
        return cls.db()[cfg.collection]

    # -------------------------------------------------------- diagnostics

    @classmethod
    def ping(cls) -> dict[str, Any]:
        """
        Verify the cluster is reachable. Returns a structured result so the
        diagnostic CLI / /health endpoint can report exactly what failed.
        """
        cfg = load_config().atlas
        out: dict[str, Any] = {"uri": _redact(cfg.uri), "database": cfg.database}
        t0 = time.perf_counter()
        try:
            res = cls.client().admin.command("ping")
            out["ok"] = bool(res.get("ok"))
            out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            log.info("Atlas: ping OK in %.1f ms", out["latency_ms"])
        except ServerSelectionTimeoutError as exc:
            out["ok"] = False
            out["error_kind"] = "server_selection_timeout"
            out["error"] = str(exc)
            log.error("Atlas: cluster unreachable (DNS / network / IP allowlist?) — %s", exc)
        except OperationFailure as exc:
            out["ok"] = False
            out["error_kind"] = "auth_or_op_failure"
            out["error"] = str(exc)
            log.error("Atlas: auth / permission error — %s", exc)
        except Exception as exc:
            out["ok"] = False
            out["error_kind"] = exc.__class__.__name__
            out["error"] = str(exc)
            log.exception("Atlas: unexpected ping failure")
        return out

    @classmethod
    def collection_stats(cls, name: str | None = None) -> dict[str, Any]:
        """
        Report basic stats for the chunks collection: document count, presence
        of `embedding` field, embedding dimension found on a sample doc, and
        which Atlas Search / Vector Search indexes exist.
        """
        cfg = load_config().atlas
        col = cls.collection(name) if name else cls.chunks_collection()
        info: dict[str, Any] = {
            "database": cfg.database,
            "collection": col.name,
            "configured_vector_index": cfg.vector_index,
            "configured_search_index": cfg.search_index,
        }
        try:
            log.info("[MongoDB] estimatedDocumentCount — collection=%r", col.name)
            info["doc_count"] = col.estimated_document_count()
        except Exception as exc:
            info["doc_count_error"] = str(exc)

        # Sample one doc to learn the embedding shape — uses the configured
        # embedding_key so it works regardless of field naming convention.
        emb_field = cfg.embedding_key
        try:
            _sample_filter = {emb_field: {"$exists": True}}
            _sample_proj = {emb_field: 1, "embedding_model": 1}
            log.info(
                "[MongoDB] find_one — collection=%r filter=%s projection=%s",
                col.name, _sample_filter, _sample_proj,
            )
            sample = col.find_one(
                _sample_filter,
                _sample_proj,
            )
            if sample is None:
                info["sample_doc"] = None
                info["embedding_dimensions"] = None
            else:
                emb = sample.get(emb_field) or []
                info["sample_doc"] = True
                info["embedding_field"] = emb_field
                info["embedding_dimensions"] = len(emb) if isinstance(emb, list) else None
                info["sample_embedding_model"] = sample.get("embedding_model")
        except Exception as exc:
            info["sample_error"] = str(exc)

        # Search index inventory (requires MongoDB 7.0+ / Atlas). Preserve
        # `latestDefinition` so callers can validate path / dimensions.
        try:
            log.info("[MongoDB] listSearchIndexes — collection=%r", col.name)
            info["search_indexes"] = [
                {
                    "name": i.get("name"),
                    "type": i.get("type"),
                    "status": i.get("status"),
                    "queryable": i.get("queryable"),
                    "latestDefinition": i.get("latestDefinition"),
                }
                for i in col.list_search_indexes()
            ]
        except Exception as exc:
            info["search_indexes_error"] = str(exc)

        log.info(
            "Atlas: collection %s.%s — %s docs, embedding_dim=%s, indexes=%s",
            cfg.database, col.name,
            info.get("doc_count"),
            info.get("embedding_dimensions"),
            [i.get("name") for i in info.get("search_indexes", []) or []],
        )
        return info
