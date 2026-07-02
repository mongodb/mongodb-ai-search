"""
Persistent store for Query Understanding results (the `query_facts` collection).

Serves two purposes from one collection:

  1. **L2 cache** — extraction survives process restarts and is shared across
     workers, so a repeat query skips the LLM call entirely.
  2. **Audit log** — each row records the query, the facts extracted, and the
     pre-/post-filters derived from them, for later analysis.

Atlas-access mirrors `PolicyStore` (`searchaas/planning/policy.py`): all I/O is
wrapped so a cache/store outage degrades to "recompute", never an error.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from searchaas.facts import Fact
from searchaas.infrastructure import AtlasFactory, COLLECTIONS
from searchaas.observability import get_logger
from searchaas.query_understanding.layer import UnderstoodQuery

log = get_logger("searchaas.query_understanding.store")

# Bump when extraction/routing logic changes so cached entries produced by an
# older version are never served (they get new keys and are recomputed).
_EXTRACTION_VERSION = "2"


def _hash(query: str, namespace: str = "") -> str:
    """Stable cache key: extraction version + namespace + normalized query.

    ``namespace`` carries the active `filter_fields` fingerprint so that
    changing which fields are indexable automatically invalidates stale
    extractions (a fact's pre/post routing depends on it).
    """
    norm = " ".join(query.strip().lower().split())
    key = "\x1f".join((_EXTRACTION_VERSION, namespace, norm))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _to_doc(query: str, uq: UnderstoodQuery, namespace: str = "") -> dict[str, Any]:
    return {
        "_id": _hash(query, namespace),
        "query": query,
        "rewritten": uq.rewritten,
        "entities": list(uq.entities or []),
        "facts": [f.to_dict() for f in uq.facts],
        "pre_filter": dict(uq.metadata_filters or {}),
        "post_filters": [f.to_dict() for f in uq.post_filters],
        "intent": uq.intent,
        "updated_at": datetime.now(timezone.utc),
    }


def _from_doc(doc: dict[str, Any]) -> UnderstoodQuery:
    raw = doc.get("query", "")
    return UnderstoodQuery(
        raw=raw,
        corrected=raw.strip(),
        rewritten=doc.get("rewritten") or raw,
        entities=list(doc.get("entities") or []),
        facts=[Fact(**f) for f in (doc.get("facts") or []) if isinstance(f, dict)],
        metadata_filters=dict(doc.get("pre_filter") or {}),
        post_filters=[Fact(**f) for f in (doc.get("post_filters") or []) if isinstance(f, dict)],
        intent=doc.get("intent") or "semantic_search",
    )


class FactStore:
    """Read/write Query Understanding results to the `query_facts` collection."""

    def __init__(self, namespace: str = "") -> None:
        self._col: Any | None = None
        # Fingerprint of the config that affects extraction (filter_fields).
        self._namespace = namespace

    def _collection(self):
        if self._col is None:
            self._col = AtlasFactory.collection(COLLECTIONS["query_facts"])
        return self._col

    def get(self, query: str) -> UnderstoodQuery | None:
        try:
            doc = self._collection().find_one({"_id": _hash(query, self._namespace)})
        except Exception as exc:
            log.debug("FactStore.get failed: %s", exc)
            return None
        if not doc:
            return None
        try:
            return _from_doc(doc)
        except Exception as exc:
            log.debug("FactStore: malformed query_facts doc, ignoring: %s", exc)
            return None

    def put(self, query: str, uq: UnderstoodQuery) -> None:
        doc = _to_doc(query, uq, self._namespace)
        self._collection().update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
