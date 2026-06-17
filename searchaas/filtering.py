"""
Metadata filter sanitization.

Atlas rejects any `$vectorSearch.filter` / `$search` filter whose path is not
declared as a `filter` field in the index definition (error code 8:
"Path '<field>' needs to be indexed as filter"). Because metadata filters can
be inferred by an LLM (Query Understanding / Planner) or supplied by API
callers, every filter is reduced to the configured allowlist before it
reaches Atlas.
"""
from __future__ import annotations

import logging
from typing import Any


def sanitize_filters(
    filters: dict[str, Any] | None,
    allowed_fields: list[str] | None,
    *,
    log: logging.Logger,
    source: str = "plan",
) -> dict[str, Any]:
    """
    Keep only filter keys present in `allowed_fields`; drop the rest.

    Operator keys (`$and`, `$or`, ...) are dropped too — their nested paths
    cannot be validated cheaply, and an unindexed path inside them would
    still fail the aggregation.
    """
    if not filters:
        return {}
    allowed = set(allowed_fields or [])
    kept = {k: v for k, v in filters.items() if k in allowed}
    dropped = sorted(set(filters) - set(kept))
    if dropped:
        log.warning(
            "Dropping non-filterable metadata filter(s) %s from %s "
            "(allowed filter fields: %s). To enable a field, index it as "
            "{type: filter} in Atlas and mirror it in "
            "`atlas.vector_index_definition` / `atlas.search_index_definition`.",
            dropped, source, sorted(allowed) or "[]",
        )
    return kept
