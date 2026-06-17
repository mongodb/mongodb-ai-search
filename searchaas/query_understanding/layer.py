"""
Query Understanding Layer.

Transforms raw enterprise queries into retrieval-ready representations:
query rewriting/expansion, entity extraction, metadata extraction, and
intent classification.

The LLM is the same `BaseChatModel` produced by `LLMFactory`; it is
prompted to return strict JSON so we can parse without an extra schema
client.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any  # retained for metadata_filters type hint

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from searchaas.filtering import sanitize_filters
from searchaas.observability import get_logger
from searchaas.utils import extract_json

log = get_logger("searchaas.query_understanding")

_CACHE_MAXSIZE = 256   # number of distinct queries to remember per process

INTENTS = (
    "exact_lookup",
    "semantic_search",
    "analytical",
    "summarization",
    "troubleshooting",
    "policy_lookup",
)


@dataclass
class UnderstoodQuery:
    raw: str
    corrected: str
    rewritten: str
    entities: list[str] = field(default_factory=list)
    metadata_filters: dict[str, Any] = field(default_factory=dict)
    intent: str = "semantic_search"


_PROMPT = """You are the Query Understanding component of an enterprise search system.

Given a user query, produce a JSON object with EXACTLY these keys:
  rewritten        : a cleaned, expanded, retrieval-friendly version of the query
  entities         : list of named entities / key terms (strings)
  metadata_filters : object of metadata field -> value filters you can confidently infer.
                     {filter_rule}
  intent           : one of {intents}

Return ONLY the JSON object. No prose, no markdown fences.

User query:
{query}
"""


class QueryUnderstandingLayer:
    def __init__(
        self,
        llm: BaseChatModel,
        allowed_filter_fields: list[str] | None = None,
    ) -> None:
        self._llm = llm
        self._filter_fields = list(allowed_filter_fields or [])
        self._cache: dict[str, UnderstoodQuery] = {}

    def process(self, raw: str) -> UnderstoodQuery:
        if raw in self._cache:
            return self._cache[raw]

        corrected = raw.strip()
        parsed = self._invoke_llm(corrected)
        result = UnderstoodQuery(
            raw=raw,
            corrected=corrected,
            rewritten=parsed.get("rewritten") or corrected,
            entities=list(parsed.get("entities") or []),
            metadata_filters=sanitize_filters(
                dict(parsed.get("metadata_filters") or {}),
                self._filter_fields,
                log=log,
                source="query understanding LLM output",
            ),
            intent=parsed.get("intent") if parsed.get("intent") in INTENTS else "semantic_search",
        )

        # Evict oldest entry when at capacity (FIFO)
        if len(self._cache) >= _CACHE_MAXSIZE:
            self._cache.pop(next(iter(self._cache)))
        self._cache[raw] = result
        return result

    # ---------------------------------------------------------------- internals

    def _filter_rule(self) -> str:
        if self._filter_fields:
            return (
                f"Only these document metadata fields exist and may be used: "
                f"{self._filter_fields}. May be empty {{}}."
            )
        return "No filterable metadata fields are configured, so this MUST be {}."

    def _invoke_llm(self, query: str) -> dict[str, Any]:
        prompt = _PROMPT.format(
            intents=list(INTENTS),
            query=query,
            filter_rule=self._filter_rule(),
        )
        try:
            resp = self._llm.invoke([
                SystemMessage(content="You output strict JSON only."),
                HumanMessage(content=prompt),
            ])
            return extract_json(getattr(resp, "content", str(resp)))
        except Exception as exc:
            log.warning("Query understanding LLM failed: %s", exc)
            return {}
