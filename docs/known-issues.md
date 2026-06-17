# Known Issues

## 1. LLM-inferred metadata filters break `$vectorSearch` (`Path 'company' needs to be indexed as filter`)

**Status:** Fixed on branch `fix/llm-filter-allowlist` (see "Fix implemented" below)
**First observed:** 2026-06-11, hitting the FastAPI ECS service from the S3-hosted UI
**Endpoints affected:** `/retrieve` (auto), and any endpoint where the plan carries filters — `/retrieve/vector`, `/retrieve/hybrid`, `/retrieve/parent-doc`

### Reproducing prompt

The issue was caught with this query (test prompt `aws-03` from the multimodal
retrieval evaluation set):

> Show me Amazon's operating income trend across all four quarters of 2022.

The mention of "Amazon" leads the Query Understanding LLM to infer
`{"company": "Amazon"}` as a metadata filter.

### Symptom

Requests fail with HTTP 500. Server log shows:

```
pymongo.errors.OperationFailure: PlanExecutor error during aggregation ::
caused by :: Path 'company' needs to be indexed as filter
```

The failure is query-dependent and looks intermittent: it only happens when the
Query Understanding LLM decides to infer a metadata filter from the user's query.

### Root cause

The Query Understanding LLM invents metadata filters for fields that don't
exist in the documents or the index, and they are passed verbatim into the
Atlas `$vectorSearch.filter` clause.

The chain:

1. `searchaas/query_understanding/layer.py:59-60` — the prompt asks the LLM for
   `metadata_filters : object of metadata field -> value filters you can confidently infer
   (geography, doc_type, department, date ranges, etc.)` with **no constraint on
   which fields actually exist**. The reproducing prompt above makes Haiku emit
   `{"company": "Amazon"}`.
2. `searchaas/planning/engine.py:95-96` — the planner copies `uq.metadata_filters`
   into `plan.filters` unchanged.
3. `searchaas/retrieval/factory.py:294` (`_build_hybrid`) — `plan.filters` is passed
   as `pre_filter` to `MongoDBAtlasHybridSearchRetriever`, which embeds it in the
   `$vectorSearch.filter` clause. The vector (`factory.py:228`) and parent-doc
   (`factory.py:310`) builders do the same.
4. Atlas Vector Search requires every path referenced in `$vectorSearch.filter`
   to be declared with `"type": "filter"` in the vector index definition.
   `aisearch_vector_index` only indexes the `embedding` field, so Atlas rejects
   the aggregation with error code 8.

### Why "just index the field" is not enough

The hallucinated field generally doesn't exist on the documents at all. The
collection is `pdf_multimodal_chunks`; chunk metadata has fields like
`source`/`page`, not `company`. Indexing `company` as a filter would stop the
500 but the filter would match zero documents and silently return empty
results. Any other field the LLM dreams up (`geography`, `doc_type`, `year`, …)
would still trigger the same error.

### Fix implemented

The user's own Atlas index definitions are the source of truth. They are
pasted verbatim into `searchaas/config/searchaas.yaml` under
`atlas.vector_index_definition` and `atlas.search_index_definition` (Atlas UI
→ Search & Vector Search → index → JSON Editor; YAML is a superset of JSON,
so the raw JSON works as-is). From these, the filter allowlists are derived —
no separate field list to keep in sync:

* `AtlasConfig.filter_fields` (`searchaas/config/loader.py`) — paths declared
  with `{"type": "filter"}` in the vector index definition. These are the only
  paths `$vectorSearch.filter` accepts.
* `AtlasConfig.search_filter_fields` — paths mapped in the Lucene index;
  `None` when the mapping is `dynamic: true` (every field indexed, no
  restriction).

Enforcement happens at three layers:

1. **Prompt constraint** (`searchaas/query_understanding/layer.py`) — the
   Query Understanding prompt now tells the LLM exactly which metadata fields
   exist; with none configured it must return `{}`. Its output is additionally
   sanitized against the allowlist.
2. **Retriever choke point** (`searchaas/retrieval/factory.py`) —
   `RetrieverFactory.create()` reduces `plan.filters` to the allowlist for the
   chosen strategy (vector/hybrid/parent_doc use the vector-index allowlist,
   fulltext uses the Lucene one) and logs a warning for anything dropped. This
   also covers filters merged in from API callers via `req.filters`. The plan
   is mutated, so API responses report the filters that actually ran.
3. **Startup preflight** (`searchaas/app/bootstrap.py`) — logs an error when
   the configured definition declares filter fields the live Atlas index does
   not actually have (config/index drift).

The shared sanitizer lives in `searchaas/filtering.py`. An LLM-invented filter
now degrades to an unfiltered search with a warning instead of a 500.

### To enable real metadata filtering

1. Add the field (e.g. `company`) to chunk metadata at ingestion time.
2. Add `{"type": "filter", "path": "company"}` to the `aisearch_vector_index`
   definition in Atlas (and map it in the Lucene `ai_search_index` if
   full-text filtering should work too).
3. Mirror the updated definition(s) in `atlas.vector_index_definition` /
   `atlas.search_index_definition` in the YAML.
