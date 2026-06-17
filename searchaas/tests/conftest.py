"""
Shared pytest fixtures for the SearchaaS test suite.

These tests exercise the metadata-filter allowlist fix (branch
`fix/llm-filter-allowlist`) without touching a live Atlas cluster or a real
LLM. The collaborators that would make network calls — the chat model and the
vector store — are replaced with in-memory fakes that record what they were
asked to do, so we can assert on the filters that *would* have been sent to
Atlas.

A subset of the evaluation prompts in `test_prompts.json` drives the
scenarios; see `PROMPT_SUBSET` below.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure the repo root is importable when pytest is invoked from anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_PROMPTS_PATH = Path(__file__).resolve().parent / "test_prompts.json"


@pytest.fixture(autouse=True)
def _propagate_searchaas_logs():
    """The `searchaas` logger is configured with `propagate=False` and its own
    stderr handler, so pytest's `caplog` (attached to root) can't see its
    records. Re-enable propagation for the duration of each test."""
    lg = logging.getLogger("searchaas")
    prev = lg.propagate
    lg.propagate = True
    try:
        yield
    finally:
        lg.propagate = prev


# --------------------------------------------------------------------------- #
# Prompt corpus
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def all_prompts() -> dict[str, dict[str, Any]]:
    """All evaluation prompts, keyed by id."""
    data = json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))
    return {p["id"]: p for p in data["prompts"]}


# The subset of prompts we test against, paired with the metadata_filters a
# hallucination-prone Query Understanding LLM is likely to emit for them. The
# point of the fix is that NONE of these invented fields (company, fiscal_year,
# vendor, ...) are in the configured allowlist, so they must be dropped before
# reaching Atlas instead of triggering "Path '<x>' needs to be indexed as
# filter".
PROMPT_SUBSET: dict[str, dict[str, Any]] = {
    # The original reproducing case from docs/known-issues.md.
    "aws-03": {"company": "Amazon"},
    # Another earnings query that name-drops the company + a period.
    "aws-01": {"company": "Amazon", "fiscal_year": 2024},
    # MongoDB earnings — different invented company value.
    "mdbearn-01": {"company": "MongoDB", "fiscal_quarter": "Q2 FY2026"},
    # A whitepaper text query — LLM may infer a doc_type facet.
    "mdbpaper-02": {"doc_type": "whitepaper"},
    # Pure topic query — typically no filter at all.
    "arxiv-10": {},
    # Off-domain negative — should never produce a filter.
    "neg-02": {},
}


@pytest.fixture(scope="session")
def prompt_subset(all_prompts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The tested prompts, each augmented with the `simulated_filters` an LLM
    would plausibly hallucinate for that query."""
    subset = []
    for pid, sim in PROMPT_SUBSET.items():
        assert pid in all_prompts, f"prompt id {pid!r} missing from test_prompts.json"
        entry = dict(all_prompts[pid])
        entry["simulated_filters"] = sim
        subset.append(entry)
    return subset


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    """Stand-in for a LangChain `BaseChatModel`.

    Returns a canned JSON payload for the query understanding prompt. The
    `metadata_filters` it emits are looked up from `filters_by_query`, keyed by
    the query text, so each prompt can drive a different hallucination.
    """

    def __init__(self, filters_by_query: dict[str, dict[str, Any]]) -> None:
        self._filters_by_query = filters_by_query
        self.calls: list[Any] = []

    def invoke(self, messages: Any) -> FakeMessage:
        self.calls.append(messages)
        # The human message is the second entry; pull the query line out of it.
        human = messages[-1].content
        query = _extract_query_line(human)
        payload = {
            "rewritten": query,
            "entities": [],
            "metadata_filters": self._filters_by_query.get(query, {}),
            "intent": "semantic_search",
        }
        return FakeMessage(json.dumps(payload))


def _extract_query_line(prompt: str) -> str:
    """Recover the original query from the rendered understanding prompt."""
    marker = "User query:"
    if marker in prompt:
        return prompt.split(marker, 1)[1].strip()
    return prompt.strip()


class FakeRetriever:
    def __init__(self, search_kwargs: dict[str, Any]) -> None:
        self.search_kwargs = search_kwargs


class FakeVectorStore:
    """Records the `search_kwargs` handed to `as_retriever` so tests can assert
    on the `pre_filter` that would have been sent into `$vectorSearch`."""

    def __init__(self) -> None:
        self.last_search_kwargs: dict[str, Any] | None = None
        self.embeddings = None

    def as_retriever(self, *, search_type: str, search_kwargs: dict[str, Any]) -> FakeRetriever:
        self.last_search_kwargs = search_kwargs
        return FakeRetriever(search_kwargs)


@pytest.fixture()
def fake_vector_store() -> FakeVectorStore:
    return FakeVectorStore()


@pytest.fixture()
def make_llm():
    """Factory: build a FakeLLM whose hallucinated filters are keyed by query."""
    def _make(filters_by_query: dict[str, dict[str, Any]]) -> FakeLLM:
        return FakeLLM(filters_by_query)
    return _make
