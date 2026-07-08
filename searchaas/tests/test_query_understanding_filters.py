"""
Prompt-driven tests for the Query Understanding layer.

Each prompt in the tested subset is fed through `QueryUnderstandingLayer` with
a FakeLLM that hallucinates metadata filters. With an allowlist that only
contains the real index filter fields (`file_name`, `page_number`), every
invented field must be stripped from `metadata_filters` so it never reaches
the planner / Atlas.
"""
from __future__ import annotations

import pytest

from searchaas.query_understanding import QueryUnderstandingLayer

ALLOWED = ["file_name", "page_number"]


def _make_layer(make_llm, prompt_subset, allowed):
    filters_by_query = {p["query"]: p["simulated_filters"] for p in prompt_subset}
    return QueryUnderstandingLayer(
        llm=make_llm(filters_by_query),
        allowed_filter_fields=allowed,
    )


def test_understanding_drops_all_hallucinated_filters(make_llm, prompt_subset):
    """None of the subset's invented fields are in the allowlist, so the
    understood query must carry no metadata filters."""
    layer = _make_layer(make_llm, prompt_subset, ALLOWED)
    for p in prompt_subset:
        uq = layer.process(p["query"])
        assert uq.metadata_filters == {}, (
            f"{p['id']}: expected hallucinated filters "
            f"{p['simulated_filters']} to be dropped, got {uq.metadata_filters}"
        )


def test_aws03_repro_company_filter_is_dropped(make_llm, prompt_subset):
    """The exact regression from docs/known-issues.md: 'Amazon' -> company."""
    layer = _make_layer(make_llm, prompt_subset, ALLOWED)
    aws03 = next(p for p in prompt_subset if p["id"] == "aws-03")
    uq = layer.process(aws03["query"])
    assert "company" not in uq.metadata_filters
    assert uq.metadata_filters == {}


def test_understanding_keeps_allowlisted_field(make_llm):
    """A fact on a genuinely indexed field is compiled into the Atlas pre-filter,
    while a non-indexed field is routed to post-filters (never sent to Atlas)."""
    query = "page 3 of the Q1 2024 release"
    layer = QueryUnderstandingLayer(
        llm=make_llm({query: {"page_number": 3, "company": "Amazon"}}),
        allowed_filter_fields=ALLOWED,
    )
    uq = layer.process(query)
    assert uq.metadata_filters == {"page_number": {"$eq": 3}}
    post_fields = {f.field for f in uq.post_filters}
    assert "company" in post_fields and "page_number" not in post_fields


def test_empty_allowlist_drops_every_filter(make_llm, prompt_subset):
    """With no filterable fields configured, all filters are removed."""
    layer = _make_layer(make_llm, prompt_subset, allowed=[])
    for p in prompt_subset:
        uq = layer.process(p["query"])
        assert uq.metadata_filters == {}


def test_prompt_constraint_injected_into_llm_prompt(make_llm, prompt_subset):
    """The rendered prompt should tell the LLM which fields are allowed."""
    llm = make_llm({p["query"]: p["simulated_filters"] for p in prompt_subset})
    layer = QueryUnderstandingLayer(llm=llm, allowed_filter_fields=ALLOWED)
    layer.process(prompt_subset[0]["query"])
    rendered = llm.calls[-1][-1].content
    assert "file_name" in rendered and "page_number" in rendered


def test_prompt_constraint_forbids_filters_when_none_configured(make_llm, prompt_subset):
    llm = make_llm({p["query"]: p["simulated_filters"] for p in prompt_subset})
    layer = QueryUnderstandingLayer(llm=llm, allowed_filter_fields=[])
    layer.process(prompt_subset[0]["query"])
    rendered = llm.calls[-1][-1].content
    assert "{}" in rendered  # "this MUST be {}"


def test_non_indexed_facts_routed_to_post_filters(make_llm, prompt_subset):
    """Non-indexed inferred fields are no longer dropped — they become in-memory
    post-filters, while the Atlas pre-filter (metadata_filters) stays empty so
    no unindexed path is ever sent to $vectorSearch.filter."""
    layer = _make_layer(make_llm, prompt_subset, ALLOWED)
    aws01 = next(p for p in prompt_subset if p["id"] == "aws-01")
    uq = layer.process(aws01["query"])
    assert uq.metadata_filters == {}
    # aws-01 simulates {"company": "Amazon", "fiscal_year": 2024}; both non-indexed.
    post_fields = {f.field for f in uq.post_filters}
    assert {"company", "fiscal_year"} <= post_fields
