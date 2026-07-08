"""
Shared utilities used by the FastAPI and FastMCP surfaces.

Consolidates logic that was previously copy-pasted between api/app.py and
mcp_server/server.py, and between planning/engine.py and
query_understanding/layer.py.
"""
from __future__ import annotations

import json
import re
from typing import Any

from searchaas.observability import get_logger

log = get_logger("searchaas.utils")

# ---------------------------------------------------------------------------
# JSON extraction (shared by QueryUnderstandingLayer and RetrievalPlanner)
# ---------------------------------------------------------------------------
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from an LLM response string."""
    match = _JSON_BLOCK.search(text or "")
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Auto-strategy clamping (shared by /retrieve and auto_search MCP tool)
# ---------------------------------------------------------------------------
# hybrid/fulltext/vector are the general-purpose auto strategies; `metadata` is
# included so ordering/superlative queries route to a find/$sort rather than a
# semantic search. graph / parent_doc stay explicit-only.
_AUTO_ALLOWED = frozenset({"hybrid", "fulltext", "vector", "metadata"})


def clamp_auto_strategy(planner_choice: str, intent: str | None) -> str:
    """Clamp the planner's strategy choice to the auto-mode strategies."""
    if planner_choice in _AUTO_ALLOWED:
        return planner_choice
    if intent in ("exact_lookup", "policy_lookup"):
        return "fulltext"
    if intent in ("semantic_search", "summarization"):
        return "vector"
    return "hybrid"


# ---------------------------------------------------------------------------
# Result summarization (shared by /retrieve and auto_search MCP tool)
# ---------------------------------------------------------------------------
def summarize(
    llm: Any,
    query: str,
    results: list[dict[str, Any]],
    max_docs: int = 10,
) -> str | None:
    """Best-effort descriptive summary of the returned documents. Never raises."""
    if not results or llm is None:
        return None
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        snippets = []
        for i, r in enumerate(results[:max_docs], 1):
            text = (r.get("content") or "").strip().replace("\n", " ")
            if len(text) > 1000:
                text = text[:1000] + "…"
            snippets.append(f"[{i}] {text}")
        prompt = (
            "Write a descriptive summary of the documents returned for the query below. "
            "Synthesize the key information across ALL documents into 1-3 well-developed "
            "paragraphs: cover what the documents say, including specific names, numbers, "
            "and details that answer the query. Cite [1], [2], etc. for each claim. "
            "Stay strictly grounded in the documents — do not invent facts.\n\n"
            f"Query: {query}\n\nDocuments:\n" + "\n".join(snippets)
        )
        resp = llm.invoke([
            SystemMessage(content=(
                "You are a retrieval summarizer. Write descriptive, well-organized "
                "summaries of the retrieved documents with specific details and citations."
            )),
            HumanMessage(content=prompt),
        ])
        return (getattr(resp, "content", str(resp)) or "").strip() or None
    except Exception as exc:
        log.warning("Summarization failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Document serialization (shared by FastAPI and FastMCP serialize helpers)
# ---------------------------------------------------------------------------
# Score fields surfaced as a first-class result attribute, checked in order.
_SCORE_KEYS: tuple[str, ...] = ("score", "vectorSearchScore", "searchScore")


def serialize_docs(
    docs: Any,
    emb_key: str | None,
    include_score: bool = False,
) -> list[dict[str, Any]]:
    """
    Strip embedding vectors from document metadata.

    Args:
        docs:          Iterable of LangChain Document objects.
        emb_key:       The configured embedding field name to strip. May be
                       ``None`` when running in AutoEmbeddings mode (Atlas
                       manages the vector field server-side, so no client-side
                       embedding ever appears in metadata).
        include_score: If True, surface the Atlas relevance score as a
                       first-class field (popped from metadata).

    Hot path: this runs once per request, iterating up to ~100 results.
    Optimised for low allocation:
      * single dict shallow-copy via `dict(metadata)` instead of repeated copies
      * skip the copy entirely when metadata is empty
      * tuple-based score key probe (no per-row list construction)
    """
    out: list[dict[str, Any]] = []
    append = out.append
    score_keys = _SCORE_KEYS
    for d in docs:
        meta_src = getattr(d, "metadata", None)
        meta: dict[str, Any] = dict(meta_src) if meta_src else {}
        if emb_key and emb_key in meta:
            del meta[emb_key]
        # Belt-and-suspenders for legacy field name; the `in` check avoids
        # the implicit hash + miss when the key is absent.
        if "embedding" in meta:
            del meta["embedding"]
        row: dict[str, Any] = {
            "content": getattr(d, "page_content", None) or str(d),
            "metadata": meta,
        }
        if include_score:
            score: float | None = None
            for key in score_keys:
                if key in meta:
                    try:
                        score = float(meta.pop(key))
                    except (TypeError, ValueError):
                        meta.pop(key, None)
                    break
            row["score"] = score
        append(row)
    return out


# ---------------------------------------------------------------------------
# Entity-based post-filter (shared by FastAPI and FastMCP surfaces)
# ---------------------------------------------------------------------------

def filter_by_entities(
    docs: list[dict[str, Any]],
    entities: list[str],
) -> list[dict[str, Any]]:
    """Post-filter: keep docs whose content contains at least one extracted entity.

    Entities shorter than 3 characters are skipped to avoid false negatives
    from stop-word fragments. Falls back to all docs when nothing matches so
    callers never receive an empty result set due to over-filtering.
    """
    if not entities or not docs:
        return docs
    terms = [e.lower() for e in entities if len(e.strip()) >= 3]
    if not terms:
        return docs
    kept = [d for d in docs if any(t in (d.get("content") or "").lower() for t in terms)]
    if not kept:
        log.debug("entity post-filter: no docs matched %s; returning all %d", terms, len(docs))
        return docs
    log.debug("entity post-filter: %d/%d docs passed for %s", len(kept), len(docs), terms)
    return kept


# ---------------------------------------------------------------------------
# Config scrubbing (shared by EmbeddingFactory and LLMFactory log output)
# ---------------------------------------------------------------------------
def redact_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a config dict with secret-looking keys removed."""
    _SENSITIVE = ("key", "secret", "token", "password", "credential")
    return {
        k: v for k, v in cfg.items()
        if not any(s in k.lower() for s in _SENSITIVE)
    }
