"""
Tests for the `RetrieverFactory` filter choke point.

`RetrieverFactory.create()` is the last line of defence before a filter
reaches Atlas: it clamps `plan.filters` to the allowlist appropriate for the
chosen strategy. These tests prove that:

  * hallucinated filters are dropped for the vector path (no `pre_filter`
    leaks into `$vectorSearch`), preventing the `Path '<x>' needs to be
    indexed as filter` OperationFailure;
  * genuinely indexed fields survive;
  * the per-strategy allowlist is applied correctly (vector vs fulltext);
  * the mutation is reflected on the plan so API responses are truthful.

The subset of evaluation prompts drives the vector-path scenarios.
"""
from __future__ import annotations

import pytest

from searchaas.planning import RetrievalPlan
from searchaas.retrieval.factory import RetrieverFactory

VECTOR_FILTERS = ["file_name", "page_number"]


def _factory(fake_vector_store, *, filter_fields=VECTOR_FILTERS, fulltext_filter_fields=None):
    return RetrieverFactory(
        vector_store=fake_vector_store,
        llm=None,
        collection=None,
        vector_index="aisearch_vector_index",
        search_index="ai_search_index",
        text_key="raw_text",
        embedding_key="embedding",
        dimensions=1024,
        filter_fields=filter_fields,
        fulltext_filter_fields=fulltext_filter_fields,
    )


# --------------------------------------------------------------------------- #
# Vector path — prompt driven
# --------------------------------------------------------------------------- #
def test_hallucinated_filters_never_reach_vector_search(fake_vector_store, prompt_subset):
    """For every tested prompt, the LLM's invented filters are stripped, so no
    `pre_filter` is sent to $vectorSearch."""
    factory = _factory(fake_vector_store)
    for p in prompt_subset:
        plan = RetrievalPlan(strategy="vector", top_k=5, filters=dict(p["simulated_filters"]))
        factory.create(plan)
        assert plan.filters == {}, f"{p['id']}: filters not cleared -> {plan.filters}"
        assert "pre_filter" not in fake_vector_store.last_search_kwargs, (
            f"{p['id']}: pre_filter leaked into $vectorSearch"
        )


def test_aws03_repro_no_operation_failure_filter(fake_vector_store, prompt_subset):
    """The exact regression: company=Amazon must not become a pre_filter."""
    factory = _factory(fake_vector_store)
    aws03 = next(p for p in prompt_subset if p["id"] == "aws-03")
    plan = RetrievalPlan(strategy="vector", top_k=5, filters=dict(aws03["simulated_filters"]))
    factory.create(plan)
    assert plan.filters == {}
    assert fake_vector_store.last_search_kwargs.get("pre_filter") is None


def test_allowlisted_filter_passes_through_to_pre_filter(fake_vector_store):
    """A real indexed field reaches $vectorSearch; an invented one alongside it
    is dropped."""
    factory = _factory(fake_vector_store)
    plan = RetrievalPlan(
        strategy="vector",
        top_k=5,
        filters={"file_name": "AMZN-Q1-2024-Earnings-Release.pdf", "company": "Amazon"},
    )
    factory.create(plan)
    assert plan.filters == {"file_name": "AMZN-Q1-2024-Earnings-Release.pdf"}
    assert fake_vector_store.last_search_kwargs["pre_filter"] == {
        "file_name": "AMZN-Q1-2024-Earnings-Release.pdf"
    }


def test_api_supplied_filters_are_also_sanitized(fake_vector_store):
    """Filters merged in by an API caller (req.filters) flow through the same
    choke point."""
    factory = _factory(fake_vector_store)
    # Simulates app.py merging req.filters into plan.filters before create().
    plan = RetrievalPlan(strategy="vector", top_k=10, filters={"page_number": 2, "vendor": "x"})
    factory.create(plan)
    assert plan.filters == {"page_number": 2}


# --------------------------------------------------------------------------- #
# Per-strategy allowlist via _sanitize_for (no external retriever construction)
# --------------------------------------------------------------------------- #
def test_sanitize_for_vector_uses_vector_allowlist(fake_vector_store):
    factory = _factory(fake_vector_store)
    out = factory._sanitize_for("vector", {"file_name": "a", "company": "b"})
    assert out == {"file_name": "a"}


def test_sanitize_for_hybrid_uses_vector_allowlist(fake_vector_store):
    """Hybrid feeds $vectorSearch pre_filter, so it uses the vector allowlist."""
    factory = _factory(fake_vector_store)
    out = factory._sanitize_for("hybrid", {"page_number": 1, "company": "b"})
    assert out == {"page_number": 1}


def test_sanitize_for_fulltext_dynamic_mapping_keeps_all(fake_vector_store):
    """A dynamic Lucene mapping (fulltext_filter_fields=None) imposes no
    restriction on $search filters."""
    factory = _factory(fake_vector_store, fulltext_filter_fields=None)
    filters = {"file_name": "a", "anything": "b"}
    assert factory._sanitize_for("fulltext", filters) == filters


def test_sanitize_for_fulltext_static_mapping_restricts(fake_vector_store):
    factory = _factory(fake_vector_store, fulltext_filter_fields=["raw_text"])
    out = factory._sanitize_for("fulltext", {"raw_text": "x", "company": "b"})
    assert out == {"raw_text": "x"}


def test_sanitize_for_empty_filters_returns_empty(fake_vector_store):
    factory = _factory(fake_vector_store)
    assert factory._sanitize_for("vector", {}) == {}
    assert factory._sanitize_for("vector", None) == {}


# --------------------------------------------------------------------------- #
# Empty allowlist (default config) drops everything on the vector path
# --------------------------------------------------------------------------- #
def test_empty_allowlist_blocks_all_vector_filters(fake_vector_store, prompt_subset):
    factory = _factory(fake_vector_store, filter_fields=[])
    for p in prompt_subset:
        plan = RetrievalPlan(strategy="vector", top_k=5, filters=dict(p["simulated_filters"]))
        factory.create(plan)
        assert plan.filters == {}
        assert fake_vector_store.last_search_kwargs.get("pre_filter") is None
