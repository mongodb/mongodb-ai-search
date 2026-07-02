"""
Query fact extraction → filter routing.

The Query Understanding LLM emits typed *facts* about a query, each shaped
``{"field": <name>, "op": <operator>, "value": <value>}``. Every fact is routed
by whether its field is indexed as a filterable path in Atlas:

  * indexed field      -> compiled into the Atlas ``$vectorSearch.filter`` /
                          ``$search`` pre-filter dict. Atlas narrows the
                          candidate set BEFORE scoring — fewer records scanned,
                          lower latency. This is the primary win.
  * non-indexed field  -> applied as an in-memory post-filter over retrieved
                          results (precision only; the scan already happened,
                          but irrelevant records are dropped from the answer).

Keeping this routing in one module means the Query Understanding layer and the
API surface share identical semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from searchaas.observability import get_logger

log = get_logger("searchaas.facts")

# Operators we accept from the LLM, mapped to MongoDB query operators. Anything
# outside this set is coerced to "eq" so a hallucinated operator never produces
# an invalid Atlas filter.
_MONGO_OP: dict[str, str] = {
    "eq": "$eq", "ne": "$ne",
    "gt": "$gt", "gte": "$gte",
    "lt": "$lt", "lte": "$lte",
    "in": "$in", "nin": "$nin",
}


@dataclass
class Fact:
    """A single structured constraint inferred from the user query."""

    field: str
    op: str = "eq"
    value: Any = None

    @classmethod
    def from_obj(cls, obj: Any) -> "Fact | None":
        """Build a Fact from raw LLM JSON, returning None if malformed."""
        if not isinstance(obj, dict):
            return None
        field = obj.get("field")
        if not isinstance(field, str) or not field:
            return None
        if "value" not in obj:
            return None
        op = str(obj.get("op") or "eq").lower()
        if op not in _MONGO_OP:
            op = "eq"
        return cls(field=field, op=op, value=obj.get("value"))

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "op": self.op, "value": self.value}


def parse_facts(raw: Any) -> list[Fact]:
    """Parse the LLM ``facts`` array into validated Facts (drops malformed)."""
    if not isinstance(raw, list):
        return []
    out: list[Fact] = []
    for item in raw:
        fact = Fact.from_obj(item)
        if fact is not None:
            out.append(fact)
    return out


def canonicalize_field(field: str, allowed_fields: list[str] | None) -> str:
    """Map a loosely-named field onto an indexed field when unambiguous.

    The LLM often emits a short label ("rating") for a nested indexed path
    ("imdb.rating"). If ``field`` is not itself indexed but equals the last
    dotted segment of exactly one indexed field, remap it — so it becomes a
    fast pre-filter instead of a silently-skipped post-filter. Ambiguous or
    unmatched names are left unchanged.
    """
    allowed = list(allowed_fields or [])
    if field in allowed:
        return field
    matches = [a for a in allowed if a.split(".")[-1] == field]
    return matches[0] if len(matches) == 1 else field


def canonicalize_facts(facts: list[Fact], allowed_fields: list[str] | None) -> list[Fact]:
    """Return facts with each field canonicalized onto an indexed path where possible."""
    out: list[Fact] = []
    for f in facts:
        canon = canonicalize_field(f.field, allowed_fields)
        out.append(f if canon == f.field else Fact(field=canon, op=f.op, value=f.value))
    return out


def split_facts(
    facts: list[Fact], allowed_fields: list[str] | None
) -> tuple[list[Fact], list[Fact]]:
    """Partition facts into ``(pre_filterable, post_only)`` by index support."""
    allowed = set(allowed_fields or [])
    pre = [f for f in facts if f.field in allowed]
    post = [f for f in facts if f.field not in allowed]
    return pre, post


def compile_prefilter(facts: list[Fact]) -> dict[str, Any]:
    """Compile pre-filterable facts into an Atlas filter dict.

    Multiple facts on the same field merge into one operator object, e.g.
    ``year>=2020`` and ``year<=2023`` -> ``{"year": {"$gte": 2020, "$lte": 2023}}``.
    """
    out: dict[str, dict[str, Any]] = {}
    for f in facts:
        out.setdefault(f.field, {})[_MONGO_OP.get(f.op, "$eq")] = f.value
    return out


# --------------------------------------------------------------------------- #
# In-memory post-filter (non-indexed facts)
# --------------------------------------------------------------------------- #

def _doc_value(doc: dict[str, Any], field: str) -> Any:
    """Read ``field`` from a serialized doc: metadata first, then top level,
    with dotted-path support inside metadata."""
    meta = doc.get("metadata") or {}
    if field in meta:
        return meta[field]
    if field in doc:
        return doc[field]
    if "." in field:
        cur: Any = meta
        for part in field.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return cur
    return None


def _match(actual: Any, op: str, expected: Any) -> bool:
    """Evaluate a single comparison, returning False on any type mismatch."""
    try:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "in":
            return isinstance(expected, (list, tuple, set)) and actual in expected
        if op == "nin":
            return not (isinstance(expected, (list, tuple, set)) and actual in expected)
        if actual is None:
            return False
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
    except TypeError:
        return False
    return False


def apply_post_filters(
    docs: list[dict[str, Any]], facts: list[Fact]
) -> list[dict[str, Any]]:
    """Keep docs satisfying every *evaluable* post-filter fact.

    A fact whose field is absent from every result is un-checkable (a field that
    doesn't exist in this corpus, or a hallucinated one) and is skipped rather
    than emptying the result set. Facts whose field IS present are applied
    strictly — an empty result then is the honest answer.
    """
    if not facts or not docs:
        return docs
    evaluable = [f for f in facts if any(_doc_value(d, f.field) is not None for d in docs)]
    if len(evaluable) != len(facts):
        skipped = [f.to_dict() for f in facts if f not in evaluable]
        log.debug("post-filter: skipping un-checkable facts %s", skipped)
    if not evaluable:
        return docs
    kept = [
        d for d in docs
        if all(_match(_doc_value(d, f.field), f.op, f.value) for f in evaluable)
    ]
    log.debug(
        "post-filter: %d/%d docs passed for %s",
        len(kept), len(docs), [f.to_dict() for f in evaluable],
    )
    return kept
