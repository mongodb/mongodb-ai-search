"""
AI-Driven Retrieval Planner.

The LLM proposes a `RetrievalPlan` for an `UnderstoodQuery`; the planner
then clamps the plan against the active `RetrievalPolicy` (Atlas-managed
guardrails) before returning it.

Also exposes `plan_for(strategy, ...)` for explicit per-endpoint routing
used by the FastAPI / FastMCP surfaces.
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from searchaas.observability import get_logger
from searchaas.query_understanding import UnderstoodQuery
from searchaas.utils import extract_json

from .policy import ALLOWED_STRATEGIES, PolicyStore, RetrievalPolicy

log = get_logger("searchaas.planning")

_PLAN_CACHE_MAXSIZE = 256   # per-process plan cache size


class RetrievalPlan(BaseModel):
    strategy: str = "hybrid"   # vector | fulltext | hybrid | graph | parent_doc
    rewrite: bool = True
    filters: dict[str, Any] = Field(default_factory=dict)
    boosts: dict[str, Any] = Field(default_factory=dict)
    top_k: int = 20


_PROMPT = """You are the Retrieval Planner of an enterprise search system.

Given an analyzed user query, produce a JSON plan with EXACTLY these keys:
  strategy  : one of {strategies}
  rewrite   : boolean
  filters   : object of metadata filters (may be empty)
  boosts    : object of field -> boost weight (may be empty)
  top_k     : integer between 1 and 50

Choose the strategy based on intent:
  - exact_lookup / policy_lookup -> "fulltext"
  - semantic_search / summarization -> "vector" or "hybrid"
  - analytical / troubleshooting -> "hybrid"
  - questions about relationships -> "graph"
  - long-form context needed -> "parent_doc"

Active policy (you MUST stay within these constraints):
{policy}

Analyzed query:
  raw              : {raw}
  rewritten        : {rewritten}
  entities         : {entities}
  metadata_filters : {meta}
  intent           : {intent}

Return ONLY the JSON object. No prose, no markdown fences.
"""


class RetrievalPlanner:
    def __init__(
        self,
        llm: BaseChatModel,
        policy_store: PolicyStore,
        default_top_k: int = 20,
    ) -> None:
        self._llm = llm
        self._policies = policy_store
        self._default_top_k = default_top_k
        # Simple FIFO cache keyed on (raw_query, intent) to skip repeat LLM calls.
        self._cache: dict[tuple[str, str | None], RetrievalPlan] = {}

    # ----------------------------------------------------------------- API

    def plan(self, uq: UnderstoodQuery) -> RetrievalPlan:
        """LLM-driven plan, clamped to the active policy."""
        policy = self._policies.active()
        draft = self._generate_plan(uq, policy)
        if uq.metadata_filters and not draft.filters:
            draft.filters = dict(uq.metadata_filters)
        return self._policies.enforce(draft, policy)

    def plan_for(
        self,
        strategy: str,
        query: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> RetrievalPlan:
        """Explicit strategy plan — used by per-strategy REST/MCP endpoints."""
        policy = self._policies.active()
        draft = RetrievalPlan(
            strategy=strategy,
            top_k=top_k or self._default_top_k,
            filters=filters or {},
        )
        return self._policies.enforce(draft, policy)

    # ------------------------------------------------------------ internals

    def _generate_plan(self, uq: UnderstoodQuery, policy: RetrievalPolicy) -> RetrievalPlan:
        cache_key = (uq.raw, uq.intent)
        if cache_key in self._cache:
            return self._cache[cache_key].model_copy()

        prompt = _PROMPT.format(
            strategies=list(policy.allowed_strategies or ALLOWED_STRATEGIES),
            policy=policy.model_dump(),
            raw=uq.raw,
            rewritten=uq.rewritten,
            entities=uq.entities,
            meta=uq.metadata_filters,
            intent=uq.intent,
        )
        try:
            resp = self._llm.invoke([
                SystemMessage(content="You output strict JSON only."),
                HumanMessage(content=prompt),
            ])
            data = extract_json(getattr(resp, "content", str(resp)))
            if not data:
                raise ValueError("empty plan")
            data.setdefault("top_k", self._default_top_k)
            # Strip any Phase-2 keys the LLM may hallucinate
            known = RetrievalPlan.model_fields.keys()
            plan = RetrievalPlan(**{k: v for k, v in data.items() if k in known})
        except Exception as exc:
            log.warning("Planner LLM failed (%s); falling back to defaults.", exc)
            plan = RetrievalPlan(
                strategy=policy.default_strategy,
                top_k=self._default_top_k,
                filters=dict(uq.metadata_filters or {}),
            )

        if len(self._cache) >= _PLAN_CACHE_MAXSIZE:
            self._cache.pop(next(iter(self._cache)))
        self._cache[cache_key] = plan
        return plan.model_copy()
