# AiSearch Test Suite

Tests for the metadata-filter allowlist fix (branch `fix/llm-filter-allowlist`),
which stops the Query Understanding LLM from sending invented filter fields
into Atlas `$vectorSearch` (the `Path '<field>' needs to be indexed as filter`
`OperationFailure`).

The suite is **hermetic** — it never touches a live Atlas cluster or a real
LLM. The chat model and vector store are replaced with in-memory fakes, so the
tests are fast (~0.2s) and run anywhere.

## Prerequisites

```bash
# From the repo root, with the project venv active
pip install pytest        # already present in ./venv (pytest 9.x)
```

No AWS, Atlas, or network access is required. The config loader autoloads the
project `.env`, so `ATLAS_URI` / `ATLAS_DB` are picked up automatically if
present; if they are absent the tests still pass (the one config test sets its
own env via `monkeypatch`).

## Running

```bash
# Run everything (uses pytest.ini -> testpaths = AiSearch/tests)
pytest

# Equivalent explicit form
python3 -m pytest AiSearch/tests/

# Verbose: list every test name
pytest -v

# Run a single file
pytest AiSearch/tests/test_retriever_filter_allowlist.py

# Run a single test
pytest AiSearch/tests/test_query_understanding_filters.py::test_aws03_repro_company_filter_is_dropped

# Filter by keyword
pytest -k "aws03 or allowlist"

# Stop on first failure, show locals
pytest -x -l
```

All commands are run from the **repo root**
(`mongodb-ai-enterprise-search/`). `pytest.ini` sets `testpaths`, so a bare
`pytest` finds the suite.

## What's covered

| File | Focus |
|---|---|
| `test_filtering.py` | `sanitize_filters()` + `AtlasConfig.filter_fields` / `search_filter_fields` derivation from the index definitions in `AiSearch.yaml` |
| `test_query_understanding_filters.py` | `QueryUnderstandingLayer` strips hallucinated filters and injects the allowed-fields constraint into the LLM prompt |
| `test_retriever_filter_allowlist.py` | `RetrieverFactory.create()` choke point — no invented `pre_filter` reaches `$vectorSearch`; allowlisted fields pass through; per-strategy allowlists |
| `conftest.py` | Shared fixtures, fakes (`FakeLLM`, `FakeVectorStore`), and the prompt subset |

### Prompt-driven scenarios

The tests are driven by a subset of the evaluation prompts in
`test_prompts.json`, defined in `conftest.py::PROMPT_SUBSET`. Each prompt is
paired with the metadata filters a hallucination-prone LLM would emit for it
(e.g. `aws-03` → `{"company": "Amazon"}`, the original reproducing case). To
extend coverage, add an entry to `PROMPT_SUBSET`:

```python
PROMPT_SUBSET = {
    ...
    "mdbpaper-05": {"company": "MongoDB", "doc_type": "whitepaper"},
}
```

The prompt id must exist in `test_prompts.json`; the dict is the filter set the
fake LLM will hallucinate for that query.

## Expected output

```
collected 32 items

AiSearch/tests/test_filtering.py ...............                        [ 46%]
AiSearch/tests/test_query_understanding_filters.py .......              [ 68%]
AiSearch/tests/test_retriever_filter_allowlist.py ..........            [100%]

============================== 32 passed in 0.22s ==============================
```

## Coverage (optional)

```bash
pip install pytest-cov
pytest --cov=AiSearch.filtering \
       --cov=AiSearch.config.loader \
       --cov=AiSearch.query_understanding.layer \
       --cov=AiSearch.retrieval.factory \
       --cov-report=term-missing
```

## Troubleshooting

- **`ModuleNotFoundError: AiSearch`** — run from the repo root, not from
  inside `AiSearch/`. `conftest.py` also inserts the repo root onto
  `sys.path` as a fallback.
- **`caplog` assertions empty** — the `AiSearch` logger sets
  `propagate=False`; the autouse `_propagate_AiSearch_logs` fixture in
  `conftest.py` re-enables propagation so `caplog` can capture warnings. Keep
  that fixture if you add log-assertion tests.
