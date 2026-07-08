"""
Retrieval Policies — Atlas-managed guardrails for planner output.

For Phase 1 we provide an in-memory default policy (so the system runs
without seed data) and a thin loader that reads the latest active policy
from Atlas if present.
"""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from searchaas.infrastructure import AtlasFactory, COLLECTIONS
from searchaas.observability import get_logger

log = get_logger("searchaas.planning.policy")


ALLOWED_STRATEGIES = ("vector", "fulltext", "hybrid", "graph", "parent_doc", "metadata")


class RetrievalPolicy(BaseModel):
    name: str = "default"
    allowed_strategies: list[str] = Field(default_factory=lambda: list(ALLOWED_STRATEGIES))
    default_strategy: str = "hybrid"
    max_top_k: int = 50
    min_top_k: int = 1
    enforce_citations: bool = True
    allow_rerank: bool = True
    metadata_whitelist: list[str] = Field(default_factory=list)  # empty == allow all


class PolicyStore:
    """Loads / enforces retrieval policies. Falls back to defaults if Atlas is empty."""

    _POLICY_TTL = 60.0  # seconds between Atlas round-trips

    def __init__(self, default_strategy: str = "hybrid") -> None:
        self._default = RetrievalPolicy(default_strategy=default_strategy)
        self._cached: RetrievalPolicy | None = None
        self._cached_at: float = 0.0

    def active(self) -> RetrievalPolicy:
        """Return the active policy, re-fetching from Atlas at most once per TTL."""
        now = time.monotonic()
        if self._cached is not None and (now - self._cached_at) < self._POLICY_TTL:
            return self._cached
        policy = self._fetch()
        self._cached = policy
        self._cached_at = now
        return policy

    def _fetch(self) -> RetrievalPolicy:
        try:
            col = AtlasFactory.collection(COLLECTIONS["retrieval_policies"])
            _filter = {"active": True}
            _sort = [("updated_at", -1)]
            log.info(
                "[MongoDB] find_one — collection=%r filter=%s sort=%s",
                col.name, _filter, _sort,
            )
            doc = col.find_one(_filter, sort=_sort)
        except Exception:
            doc = None
        if not doc:
            return self._default
        doc.pop("_id", None)
        try:
            return RetrievalPolicy(**doc)
        except Exception:
            return self._default

    def enforce(self, draft: "RetrievalPlan", policy: RetrievalPolicy) -> "RetrievalPlan":  # noqa: F821
        # Clamp strategy to allowed list
        if draft.strategy not in policy.allowed_strategies:
            draft.strategy = policy.default_strategy
        # Clamp top_k to policy bounds
        draft.top_k = max(policy.min_top_k, min(policy.max_top_k, draft.top_k))
        # Metadata whitelist filtering
        if policy.metadata_whitelist and isinstance(draft.filters, dict):
            draft.filters = {
                k: v for k, v in draft.filters.items() if k in policy.metadata_whitelist
            }
        return draft
