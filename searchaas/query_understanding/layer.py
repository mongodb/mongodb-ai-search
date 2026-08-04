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
from langchain_core.runnables import RunnableConfig

from searchaas.facts import (
    Fact,
    canonicalize_facts,
    compile_prefilter,
    parse_facts,
    parse_sort,
    split_facts,
)
from searchaas.filtering import sanitize_filters
from searchaas.observability import get_logger
from searchaas.utils import extract_json

log = get_logger("searchaas.query_understanding")

_CACHE_MAXSIZE = 256   # number of distinct queries to remember per process
_MAX_LIMIT = 100       # cap on a query-specified result count


def _parse_limit(raw: Any) -> int | None:
    """A positive result count named in the query, capped; else None."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= _MAX_LIMIT else None

INTENTS = (
    "exact_lookup",
    "semantic_search",
    "analytical",
    "summarization",
    "troubleshooting",
    "policy_lookup",
    "ordering",       # ranking / superlative queries ("lowest rated", "top ...")
    "lookup",         # structured filter lookup, no semantic topic ("movies 1970-2000")
)


@dataclass
class UnderstoodQuery:
    raw: str
    corrected: str
    rewritten: str
    entities: list[str] = field(default_factory=list)
    # All structured facts inferred from the query (pre- + post-filterable).
    facts: list[Fact] = field(default_factory=list)
    # Pre-filter: facts on indexed fields, compiled to an Atlas filter dict.
    # Kept under this name so the planner / retriever factory stay unchanged.
    metadata_filters: dict[str, Any] = field(default_factory=dict)
    # Post-filter: facts on non-indexed fields, applied in-memory after retrieval.
    post_filters: list[Fact] = field(default_factory=list)
    # Ordering for ranking/superlative queries, as a Mongo sort dict
    # (e.g. {"imdb.rating": 1}). None when the query has no ordering intent.
    sort: dict[str, int] | None = None
    # Result count named in the query ("top 5", "3 cheapest", "10 movies").
    # None when the query doesn't specify one (caller default then applies).
    limit: int | None = None
    intent: str = "semantic_search"


_PROMPT = """You are the Query Understanding component of an enterprise search system.

Given a user query, produce a JSON object with EXACTLY these keys:
  rewritten        : a cleaned, expanded, retrieval-friendly version of the query
  entities         : list of named entities / key terms (strings)
  facts            : list of structured constraints you can CONFIDENTLY infer
                     from the query. Each is an object
                     {{"field": <name>, "op": <operator>, "value": <value>}}.
                     Operators: eq, ne, gt, gte, lt, lte, in, nin.
                     Capture concrete constraints only — dates/years, document
                     types, departments, statuses, amounts, etc. Omit a fact if
                     the query does not clearly imply it. May be empty [].
                     {filter_rule}
  sort             : ordering for ranking / superlative queries, as
                     {{"field": <indexed field>, "direction": 1 or -1}}
                     (1 = ascending, -1 = descending). Use ONLY when ranking is
                     the intent: "lowest / worst / cheapest / oldest" -> 1 on the
                     relevant numeric field; "highest / top / best / newest" -> -1.
                     The field MUST be one of the indexed fields above. Return
                     null when the query has no ordering intent.
  limit            : integer result count IF the query names one — "top 5" -> 5,
                     "3 cheapest" -> 3, "10 movies" -> 10, "lowest rating 5
                     movies" -> 5. Return null when no count is stated.
  metadata_filters : DEPRECATED — return {{}} and use `facts` instead.
  intent           : one of {intents}.
                     Use "ordering" when the query is primarily a ranking /
                     superlative over a field ("lowest rated", "top 5 by X").
                     Use "lookup" when the query is FULLY answerable by the
                     structured facts/filters above (dates, years, categories,
                     exact field values) with NO descriptive topic — e.g.
                     "movies between 1970 and 2000", "sci-fi from the 90s".
                     If a real semantic topic remains after the filters (e.g.
                     "movies about robots from the 90s"), use "semantic_search".

Return ONLY the JSON object. No prose, no markdown fences.

User query:
{query}
"""


class QueryUnderstandingLayer:
    def __init__(
        self,
        llm: BaseChatModel,
        allowed_filter_fields: list[str] | None = None,
        field_aliases: dict[str, list[str]] | None = None,
        fact_store: Any | None = None,
        llm_timeout_s: float = 5.0,
    ) -> None:
        self._llm = llm
        self._filter_fields = list(allowed_filter_fields or [])
        # {indexed_field: [synonyms]} injected into the prompt (see _filter_rule).
        self._field_aliases = dict(field_aliases or {})
        # L1: in-process FIFO cache (fast, per-worker, lost on restart).
        self._cache: dict[str, UnderstoodQuery] = {}
        # L2: optional persistent FactStore (shared across workers/restarts,
        # also serves as the facts audit log). Typed loosely to avoid importing
        # the store here (it imports UnderstoodQuery from this module).
        self._store = fact_store
        # Hard deadline per LLM call — prevents slow Gemini responses from
        # hanging the request for 15+ seconds. 0 = no timeout.
        self._llm_timeout_s: float = llm_timeout_s

    def process(self, raw: str) -> UnderstoodQuery:
        if raw in self._cache:
            return self._cache[raw]

        # L2 lookup before paying for an LLM call.
        if self._store is not None:
            try:
                cached = self._store.get(raw)
            except Exception as exc:  # never let cache errors break retrieval
                log.debug("FactStore.get failed: %s", exc)
                cached = None
            if cached is not None:
                self._remember(raw, cached)
                return cached

        corrected = raw.strip()
        parsed = self._invoke_llm(corrected)

        facts = parse_facts(parsed.get("facts"))
        # Back-compat: fold any legacy metadata_filters object into eq facts.
        legacy = parsed.get("metadata_filters")
        if isinstance(legacy, dict):
            facts.extend(Fact(field=k, op="eq", value=v) for k, v in legacy.items())

        # Remap loose field names ("rating") onto indexed paths ("imdb.rating")
        # so they can pre-filter instead of silently falling through to post.
        facts = canonicalize_facts(facts, self._filter_fields)
        pre_facts, post_facts = split_facts(facts, self._filter_fields)
        result = UnderstoodQuery(
            raw=raw,
            corrected=corrected,
            rewritten=parsed.get("rewritten") or corrected,
            entities=list(parsed.get("entities") or []),
            facts=facts,
            metadata_filters=sanitize_filters(
                compile_prefilter(pre_facts),
                self._filter_fields,
                log=log,
                source="query understanding facts",
            ),
            post_filters=post_facts,
            sort=parse_sort(parsed.get("sort"), self._filter_fields),
            limit=_parse_limit(parsed.get("limit")),
            intent=parsed.get("intent") if parsed.get("intent") in INTENTS else "semantic_search",
        )

        self._remember(raw, result)
        if self._store is not None:
            try:
                self._store.put(raw, result)
            except Exception as exc:  # persistence is best-effort
                log.debug("FactStore.put failed: %s", exc)
        return result

    # ---------------------------------------------------------------- internals

    def _remember(self, raw: str, result: UnderstoodQuery) -> None:
        """Insert into the L1 cache, evicting the oldest entry at capacity (FIFO)."""
        if len(self._cache) >= _CACHE_MAXSIZE:
            self._cache.pop(next(iter(self._cache)))
        self._cache[raw] = result

    def _filter_rule(self) -> str:
        if not self._filter_fields:
            return (
                "No indexed filter fields are configured; only include a fact "
                "when the query clearly implies a concrete field/value constraint."
            )
        rule = (
            f"Indexed fields you MUST reuse by their EXACT name (including any "
            f"dotted path) whenever the query refers to that concept: "
            f"{self._filter_fields}. If the query uses a shorter synonym for one "
            f"of these (e.g. a bare 'y' for a listed 'x.y'), you MUST output the "
            f"exact listed field name — never invent a new one. Only introduce a "
            f"different field for a constraint none of the listed fields cover."
        )
        hints = "; ".join(
            f"{field} (also: {', '.join(aliases)})"
            for field, aliases in self._field_aliases.items()
            if aliases
        )
        if hints:
            rule += (
                f" Known synonyms — resolve each to the exact indexed field using "
                f"query context (a word may not always mean the field, e.g. "
                f"'stars' can mean cast): {hints}."
            )
        return rule

    def _invoke_llm(self, query: str) -> dict[str, Any]:
        prompt = _PROMPT.format(
            intents=list(INTENTS),
            query=query,
            filter_rule=self._filter_rule(),
        )
        messages = [
            SystemMessage(content="You output strict JSON only."),
            HumanMessage(content=prompt),
        ]
        try:
            if self._llm_timeout_s > 0:
                try:
                    cfg = RunnableConfig(timeout=self._llm_timeout_s)
                    resp = self._llm.invoke(messages, config=cfg)
                except TypeError:
                    # LLM implementation does not accept config kwarg (e.g. test fakes).
                    resp = self._llm.invoke(messages)
            else:
                resp = self._llm.invoke(messages)
            return extract_json(getattr(resp, "content", str(resp)))
        except Exception as exc:
            # Timeout surfaces as TimeoutError or langchain's RunnableTimeoutError.
            # Both are caught here; the caller falls back to a safe default UnderstoodQuery.
            if "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower():
                log.warning(
                    "Query understanding LLM timed out after %.1f s for query %r — "
                    "falling back to empty UnderstoodQuery (hybrid retrieval, no filters).",
                    self._llm_timeout_s, query[:80],
                )
            else:
                log.warning("Query understanding LLM failed: %s", exc)
            return {}
