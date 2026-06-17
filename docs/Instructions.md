# Technical Implementation Architecture — Python (Factory Pattern)

This document describes the implementation architecture of the SearchaaS retrieval platform in **Python**, organized around the **Factory design pattern**. Every pluggable concern (embeddings, retrievers, rerankers, response generators, planners) is created through a factory so that providers and strategies can be swapped via configuration without changing call sites.

**Delivery phasing**

* **Phase 1 (core retrieval foundation):** MongoDB Atlas as the Retrieval Foundation, Data Model, Query Understanding Layer, Query Embedding Strategy, AI-Driven Retrieval Planning, Retrieval Strategies (excluding the Self-Query Retriever), YAML Configuration, FastAPI + FastMCP exposure of every retrieval method, and a React testing UI.
* **Phase 2 (enrichment, generation & operations):** Metadata Intelligence, Self-Query Retriever, Reranking Layer, Grounded Response Generation, Security & Governance, Observability & Evaluation.

---

## Library Baseline (latest LangChain packages)

LangChain is now split into focused packages. The platform pins the following:

| Concern | Package | Key classes |
| --- | --- | --- |
| Atlas integration | `langchain-mongodb` | `MongoDBAtlasVectorSearch`, `MongoDBAtlasFullTextSearchRetriever`, `MongoDBAtlasHybridSearchRetriever`, `MongoDBAtlasParentDocumentRetriever`, `MongoDBAtlasSelfQueryRetriever` |
| GraphRAG / graph store | `langchain-mongodb` | `langchain_mongodb.graphrag.graph.MongoDBGraphStore` (Atlas as knowledge graph; `$graphLookup` traversal via `pymongo`) |
| Core abstractions | `langchain-core` | `BaseRetriever`, `Document`, `Runnable`, prompt/`ChatPromptTemplate` |
| Composition retrievers | `langchain` | `SelfQueryRetriever`, `ParentDocumentRetriever`, `EnsembleRetriever`, `MultiQueryRetriever`, `ContextualCompressionRetriever` |
| Embeddings | `langchain-openai`, `langchain-voyageai`, `langchain-cohere`, `langchain-huggingface`, `langchain-google-genai`, `langchain-aws` | `AzureOpenAIEmbeddings`, `OpenAIEmbeddings`, `VoyageAIEmbeddings`, `CohereEmbeddings`, `HuggingFaceEmbeddings`, `GoogleGenerativeAIEmbeddings` (Gemini), `BedrockEmbeddings` (Amazon Titan) |
| LLMs | `langchain-google-genai`, `langchain-openai`, `langchain-anthropic`, `langchain-aws` | `ChatGoogleGenerativeAI` (Gemini), `AzureChatOpenAI`, `ChatOpenAI`, `ChatAnthropic`, `ChatBedrock` |
| Rerankers | `langchain-cohere`, `langchain-voyageai`, `langchain-community` | `CohereRerank`, `VoyageAIRerank`, cross-encoder compressors |
| Driver | `pymongo` (with `motor` for async) | Atlas Search / Vector Search aggregation, `$graphLookup` |
| Config / secrets | `pyyaml`, `python-dotenv` | YAML with `${VAR}` / `${VAR:-default}` expansion; auto-loads `.env` next to the package |

```bash
pip install langchain langchain-core langchain-mongodb \
  langchain-openai langchain-voyageai langchain-cohere langchain-huggingface \
  langchain-google-genai langchain-aws boto3 \
  langchain-community pymongo motor \
  pyyaml python-dotenv fastapi uvicorn fastmcp requests
```

> **Voyage AI note.** `langchain-voyageai 0.1.x` does not expose the Voyage SDK's `output_dimension` / `output_dtype` parameters. The `EmbeddingFactory` ships with a thin subclass (`_VoyageEmbeddingsWithExtras`) that forwards them, so a YAML config like `embeddings.config.output_dimension: 512` lets you project `voyage-4` (native 1024-dim) down to match a 512-dim Atlas Vector Search index without changing call sites.

---

# PHASE 1

## 0. Phase 1 Project Layout

The Phase 1 implementation lives under `searchaas/`. Each module corresponds to a numbered section below.

```
searchaas/
├── config/                 # §7   YAML + loader (single source of truth)
│   ├── searchaas.yaml      #      runtime config (atlas, embeddings, planner, retrieval, server)
│   ├── loader.py           #      load_config() -> AppConfig + .env auto-loading + validation
│   └── __init__.py
├── observability/          # §10a Central logging (dictConfig, JSON/plain)
│   └── logging.py          #      configure_logging() + get_logger()
├── infrastructure/
│   └── atlas.py            # §1   AtlasFactory + ping() + collection_stats() + COLLECTIONS
├── domain/
│   └── models.py           # §2   Chunk, SourceRef
├── embeddings/
│   └── factory.py          # §4   EmbeddingFactory (+ gemini, bedrock_titan, voyage shim) + probe()
├── llm/
│   └── factory.py          # §4a  LLMFactory (+ gemini, bedrock, anthropic, ...)
├── query_understanding/
│   └── layer.py            # §3   QueryUnderstandingLayer / UnderstoodQuery
├── planning/
│   ├── engine.py           # §5   RetrievalPlanner + RetrievalPlan
│   └── policy.py           # §5   PolicyStore (Atlas-managed guardrails)
├── retrieval/
│   └── factory.py          # §6   RetrieverFactory + run_vector() probe + _GraphRAGRetriever
├── app/
│   └── bootstrap.py        # §7   build_container(AppConfig) + _preflight_vector_index()
├── api/
│   └── app.py              # §8   FastAPI endpoints + request-ID middleware + /diagnose
├── mcp_server/
│   └── server.py           # §9   FastMCP tools
└── diagnose.py             # §10a CLI: python -m searchaas.diagnose
```

Run order:

```bash
uvicorn searchaas.api.app:app --port 8000      # REST
python  -m searchaas.mcp_server.server         # MCP
python  -m searchaas.diagnose --query "..."    # vector-search self-check (no server needed)
```

## 1. MongoDB Atlas as the Retrieval Foundation

MongoDB Atlas is the central operational and retrieval datastore, combining operational document storage, full-text search, vector search, metadata querying, and real-time scalability in one unified platform — removing the need for separate vector databases, search engines, and operational stores.

* **Atlas Search** — full-text: fuzzy matching, analyzers, synonyms, autocomplete, exact keyword retrieval (built on Apache Lucene).
* **Atlas Vector Search** — semantic retrieval via vector similarity with HNSW indexing.

Atlas collections also back: Search Profiles, Retrieval Policies, Deployment Configurations, Query Telemetry, Execution History, Evaluation Metrics, Prompt Templates, Synonym Management, Search Analytics.

```python
# searchaas/infrastructure/atlas.py  (excerpt)
from functools import lru_cache
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, OperationFailure, ServerSelectionTimeoutError
from searchaas.config import load_config
from searchaas.observability import get_logger

log = get_logger("searchaas.infrastructure.atlas")

class AtlasFactory:
    """Single source of truth for Atlas clients, databases, and collections."""

    @staticmethod
    @lru_cache(maxsize=1)
    def client() -> MongoClient:
        cfg = load_config().atlas
        log.info("Atlas: connecting to %s", _redact(cfg.uri))
        return MongoClient(cfg.uri, appname="searchaas", serverSelectionTimeoutMS=8000)

    @classmethod
    def db(cls):          return cls.client()[load_config().atlas.database]

    @classmethod
    def collection(cls, name: str):
        physical = COLLECTIONS.get(name, name)
        return cls.db()[physical]

    @classmethod
    def chunks_collection(cls):
        return cls.db()[load_config().atlas.collection]

    # ---- diagnostics -------------------------------------------------------
    @classmethod
    def ping(cls) -> dict:
        """Structured Atlas reachability check (used by /diagnose, /health, CLI)."""

    @classmethod
    def collection_stats(cls, name: str | None = None) -> dict:
        """doc_count, sample embedding dim from the configured `embedding_key`,
        and full search-index inventory (preserving `latestDefinition`)."""

# Named collections used across the platform
COLLECTIONS = {
    "chunks": "knowledge_chunks", "search_profiles": "search_profiles",
    "retrieval_policies": "retrieval_policies", "deployments": "deployment_configs",
    "telemetry": "query_telemetry", "execution_history": "execution_history",
    "eval_metrics": "evaluation_metrics", "prompt_templates": "prompt_templates",
    "synonyms": "synonym_mappings", "analytics": "search_analytics",
}
```

`ping()` returns `{"ok", "latency_ms", "uri" (redacted), "error_kind"?, "error"?}` so the API, CLI, and `/health` endpoint can pinpoint connection / DNS / auth failures explicitly. `collection_stats()` samples a document at the **configured `embedding_key`** (not hardcoded `embedding`), so changing the field name in YAML continues to work.

## 2. Data Model

Enterprise content is assumed to be **chunked and embedded before ingestion**. Each searchable chunk carries content, vector embeddings, metadata attributes, entity information, and source references. Query embeddings must match the indexing model, enabling multiple embedding providers simultaneously.

```python
# domain/models.py
from pydantic import BaseModel, Field
from typing import Any

class SourceRef(BaseModel):
    document_id: str
    uri: str | None = None
    parent_id: str | None = None      # enables parent-document retrieval
    page: int | None = None

class Chunk(BaseModel):
    id: str = Field(alias="_id")
    content: str
    embedding: list[float]
    embedding_model: str               # must match query embedding model
    metadata: dict[str, Any] = {}      # geography, doc_type, department, ACLs...
    entities: list[str] = []
    source: SourceRef

    class Config:
        populate_by_name = True
```

Supporting collections store Retrieval Policies, Search Profiles, Evaluation Metrics, Telemetry, and Deployment Configurations (see `COLLECTIONS`).

## 3. Query Understanding Layer

Transforms raw, messy enterprise queries into optimized retrieval-ready representations. Tasks: spell correction, query rewriting/expansion, entity extraction, metadata extraction, and intent classification (exact lookup, semantic search, analytical, summarization, troubleshooting, policy lookup).

```python
# query_understanding/layer.py
from dataclasses import dataclass, field

@dataclass
class UnderstoodQuery:
    raw: str
    corrected: str
    rewritten: str
    entities: list[str] = field(default_factory=list)
    metadata_filters: dict = field(default_factory=dict)
    intent: str = "semantic_search"

class QueryUnderstandingLayer:
    def __init__(self, llm, spell_corrector):
        self._llm = llm
        self._spell = spell_corrector

    def process(self, raw: str) -> UnderstoodQuery:
        corrected = self._spell.correct(raw)
        rewritten = self._llm.rewrite(corrected)
        entities = self._llm.extract_entities(rewritten)
        filters = self._llm.extract_metadata(rewritten)
        intent = self._llm.classify_intent(rewritten)
        return UnderstoodQuery(raw, corrected, rewritten, entities, filters, intent)
```

## 4. Query Embedding Strategy

Query embeddings are generated **dynamically at runtime** (document embeddings are pre-generated externally). This lets enterprises swap models without rearchitecting, support multiple providers, experiment on quality, and tune latency/cost. With **Atlas Auto-Embeddings**, query vectors can be generated automatically by Atlas using Voyage AI; otherwise the platform generates them via the configured provider before retrieval — balancing simplicity, model governance, cost, and quality.

```python
# searchaas/embeddings/factory.py  (excerpt)
from langchain_core.embeddings import Embeddings
from searchaas.observability import get_logger

log = get_logger("searchaas.embeddings")

class EmbeddingFactory:
    _registry = {
        "azure_openai":  _make_azure_openai,
        "openai":        _make_openai,
        "voyageai":      _make_voyageai,          # adds output_dimension/output_dtype shim
        "cohere":        _make_cohere,
        "huggingface":   _make_huggingface,
        "gemini":        _make_gemini,            # GoogleGenerativeAIEmbeddings
        "bedrock_titan": _make_bedrock_titan,     # BedrockEmbeddings
    }

    @classmethod
    def create(cls, provider: str, config: dict | None = None) -> Embeddings:
        safe_cfg = {k: v for k, v in (config or {}).items()
                    if "key" not in k.lower() and "secret" not in k.lower()}
        log.info("Embeddings: building provider=%s config=%s", provider, safe_cfg)
        return cls._registry[provider](config or {})

    @classmethod
    def probe(cls, embedder: Embeddings, sample: str = "ping") -> dict:
        """Embed a tiny string to verify creds; returns {ok, dimensions, latency_ms}."""
```

Notes on the providers:

- **Gemini** — `GoogleGenerativeAIEmbeddings(model="models/text-embedding-004", google_api_key=...)`. Native 768-dim output.
- **Amazon Titan** — `BedrockEmbeddings(model_id="amazon.titan-embed-text-v2:0", region_name="us-east-1", credentials_profile_name=...)`. Native 1024-dim.
- **Voyage AI** — `voyage-3` is 1024-dim, `voyage-4` is 1024-dim by default but supports `output_dimension` (256/512/1024/2048). Because `langchain-voyageai 0.1.x` doesn't expose this parameter, the factory wraps the model with `_VoyageEmbeddingsWithExtras`, a subclass that forwards `output_dimension` / `output_dtype` to the underlying Voyage SDK:

  ```yaml
  embeddings:
    provider: voyageai
    config:
      model: voyage-4
      voyage_api_key: ${VOYAGE_API_KEY}
      output_dimension: 512        # forwarded to voyageai.Client.embed(...)
  ```

  This is what lets a YAML change make `voyage-4` emit 512-dim vectors to match a 512-dim Atlas Vector Search index. `EmbeddingFactory.create()` never mutates the caller's config dict, so a single config can be re-used to build the embedder for both the query path and the vector store.

- **Probe** — `EmbeddingFactory.probe(embedder)` is used by both the `/diagnose` endpoint and the `searchaas.diagnose` CLI to verify keys + measure live embedding latency.

### 4a. LLM Factory

LLMs power the Query Understanding Layer and the AI-Driven Retrieval Planner (Phase 1) and Grounded Response Generation (Phase 2). A dedicated `LLMFactory` keeps provider selection symmetric with embeddings — the planner's chat model is swappable through YAML.

```python
# searchaas/llm/factory.py  (excerpt)
from langchain_core.language_models import BaseChatModel
from searchaas.observability import get_logger

log = get_logger("searchaas.llm")

class LLMFactory:
    _registry = {
        "gemini":       _make_gemini,        # ChatGoogleGenerativeAI (gemini-2.0-flash, gemini-2.5-flash, gemini-1.5-pro)
        "azure_openai": _make_azure_openai,  # AzureChatOpenAI
        "openai":       _make_openai,        # ChatOpenAI
        "anthropic":    _make_anthropic,     # ChatAnthropic
        "bedrock":      _make_bedrock,       # ChatBedrock (Claude / Titan / Mistral on Bedrock)
    }

    @classmethod
    def create(cls, provider: str, config: dict | None = None) -> BaseChatModel:
        safe_cfg = {k: v for k, v in (config or {}).items()
                    if "key" not in k.lower() and "secret" not in k.lower()}
        log.info("LLM: building provider=%s config=%s", provider, safe_cfg)
        return cls._registry[provider](config or {})
```

---

## 5. AI-Driven Retrieval Planning

An **LLM-powered planner** dynamically produces a retrieval execution plan per query instead of forcing a fixed pipeline. The plan decides: rewrite/expand?, which entities/metadata to extract, text vs. vector vs. hybrid, filters/boosting, `top_k`, rerank? and which model, citation enforcement, and grounding/prompting strategy. The planner operates **within centrally managed policies and templates stored in Atlas** (approved models, mandatory citations, security controls, metadata rules, retrieval constraints). Generated plans, metrics, decisions, latencies, and feedback are recorded for telemetry and continuous improvement.

```python
# planning/engine.py
from pydantic import BaseModel

class RetrievalPlan(BaseModel):
    strategy: str               # "vector" | "fulltext" | "hybrid" | "graph" | "parent_doc" | "self_query"
    rewrite: bool = True
    filters: dict = {}
    boosts: dict = {}
    top_k: int = 20
    rerank: bool = False
    rerank_model: str | None = None
    enforce_citations: bool = True
    grounding_strategy: str = "default"

class RetrievalPlanner:
    def __init__(self, llm, policy_store):
        self._llm = llm
        self._policies = policy_store

    def plan(self, uq) -> RetrievalPlan:
        policy = self._policies.active()                 # Atlas-managed guardrails
        draft = self._llm.generate_plan(uq, policy)      # LLM proposes
        return self._policies.enforce(draft, policy)     # clamp to guardrails
```

## 6. Retrieval Strategies

All retrievers are produced by a **RetrieverFactory** keyed by the plan's `strategy`, returning a `langchain-core` `BaseRetriever`.

| Capability | LangChain implementation | Description |
| --- | --- | --- |
| Vector Search | `MongoDBAtlasVectorSearch(...).as_retriever()` (`langchain-mongodb`) | Embedding-based semantic retrieval via Atlas Vector Search (HNSW). |
| Full-Text Search | `MongoDBAtlasFullTextSearchRetriever` (`langchain-mongodb`) | Keyword, lexical, synonym-aware, relevance-based retrieval on Lucene-backed Atlas Search. |
| Graph Query | `MongoDBGraphStore` (`langchain_mongodb.graphrag`) + `$graphLookup` via `pymongo` | Traverses connected entities/relationships for multi-hop context; Atlas acts as the knowledge graph for GraphRAG. |
| Hybrid Search | `MongoDBAtlasHybridSearchRetriever` (`langchain-mongodb`) | Fuses vector + full-text with per-query weighting for combined semantic and exact-match relevance. |
| Parent Doc Retriever | `MongoDBAtlasParentDocumentRetriever` (`langchain-mongodb`) | Matches a child chunk then returns the larger parent document/section for fuller context on long-form content. |

> The **Self-Query Retriever** is delivered in Phase 2 (see Section 12) because it depends on the Metadata Intelligence layer to supply its `metadata_field_info`.

```python
# searchaas/retrieval/factory.py  (excerpt)
from langchain_core.retrievers import BaseRetriever
from searchaas.observability import get_logger

log = get_logger("searchaas.retrieval")

class RetrieverFactory:
    """All field names and dimensions are injected from `AppConfig.atlas`."""

    def __init__(
        self,
        vector_store,                       # MongoDBAtlasVectorSearch (already bound to text_key + embedding_key + dimensions)
        llm,
        collection,
        *,
        vector_index: str,                  # atlas.vector_index
        search_index: str = "default",      # atlas.search_index (Lucene)
        text_key: str = "content",          # atlas.text_key       (raw text field)
        embedding_key: str = "embedding",   # atlas.embedding_key  (vector field, MUST match index path)
        dimensions: int = -1,               # atlas.dimensions     (MUST match embedder + index numDimensions)
        hybrid_weights: dict[str, float] | None = None,
        vector_num_candidates: int = 200,
    ): ...

    def create(self, plan) -> BaseRetriever:
        s, k = plan.strategy, plan.top_k
        log.info("RetrieverFactory: strategy=%s k=%s filters=%s", s, k, plan.filters)

        if s == "vector":
            # `MongoDBAtlasVectorSearch.similarity_search` accepts only:
            #   k, pre_filter, oversampling_factor
            return self._vs.as_retriever(
                search_type="similarity",
                search_kwargs={
                    "k": k,
                    "oversampling_factor": max(1, self._vector_candidates // max(k, 1)),
                    **({"pre_filter": plan.filters} if plan.filters else {}),
                },
            )

        if s == "fulltext":
            from langchain_mongodb.retrievers import MongoDBAtlasFullTextSearchRetriever
            return MongoDBAtlasFullTextSearchRetriever(
                collection=self._col,
                search_index_name=self._search_index,
                search_field=self._text_key,           # driven by YAML
                k=k,
                filter=plan.filters or None,
            )

        if s == "hybrid":
            # Reciprocal Rank Fusion: score = vw/(vp+rank_v) + fw/(fp+rank_t)
            # *_penalty (default 60.0) discount rank position; *_weight controls channel contribution.
            from langchain_mongodb.retrievers import MongoDBAtlasHybridSearchRetriever
            return MongoDBAtlasHybridSearchRetriever(
                vectorstore=self._vs,
                search_index_name=self._search_index,
                k=k,
                vector_weight=float(self._hybrid_weights.get("vector_weight", 0.6)),
                fulltext_weight=float(self._hybrid_weights.get("fulltext_weight", 0.4)),
                pre_filter=plan.filters or None,
            )

        if s == "parent_doc":
            from langchain_mongodb.retrievers import MongoDBAtlasParentDocumentRetriever
            return MongoDBAtlasParentDocumentRetriever(
                vectorstore=self._vs, collection=self._col,
                search_kwargs={"k": k, "pre_filter": plan.filters or None},
            )

        if s == "graph":
            return _GraphRAGRetriever(
                collection=self._col, top_k=k,
                text_key=self._text_key, embedding_key=self._embedding_key,
            )

        raise ValueError(f"Unknown strategy: {s}")

    # ---- diagnostic helper used by /diagnose/vector and the CLI -----------
    def run_vector(self, query: str, k: int = 5, filters: dict | None = None) -> dict:
        """Instrumented vector search: returns a stage-by-stage report
        ({stage: embedding|dimension_mismatch|search|done, ok, error, hint, ...})."""
```

Three things make this implementation robust to per-deployment schema changes:

1. **Every field name comes from YAML** (`atlas.text_key`, `atlas.embedding_key`). `_build_vector`, `_build_fulltext`, `_build_hybrid`, `_GraphRAGRetriever`, and the API/MCP response serializers all read from these — there are no hardcoded `"content"` / `"embedding"` strings left in the retrieval path.
2. **`atlas.dimensions` is enforced.** `MongoDBAtlasVectorSearch` is built with `dimensions=cfg.atlas.dimensions`; the `_preflight_vector_index` check (run at container build) compares this against the live Atlas index's `numDimensions`; `run_vector` compares query-vector dim against `atlas.dimensions` AND the sample-stored dim.
3. **Per-call structured logs** at INFO level: every retriever build emits one line with `index`, `path`, `dim`, `k`, `oversampling`, `filters`, weights — so failures like `"embedding is not indexed as vector"` are reproducible from a single grep.

> Composition retrievers from `langchain` (`EnsembleRetriever`, `MultiQueryRetriever`, `ContextualCompressionRetriever`) are layered on top of these base retrievers when a plan calls for fusion, query fan-out, or post-retrieval compression.

## 7. Configuration (YAML)

All providers, models, indexes, and runtime options are supplied through a single YAML file loaded at startup and used to drive every factory. Nothing is hard-coded — switching an embedding model, reranker, or LLM is a config change, not a code change.

```yaml
# searchaas/config/searchaas.yaml
atlas:
  uri: ${ATLAS_URI}                       # resolved from env / Azure Key Vault
  database: ${ATLAS_DB:-searchaas}
  collection: knowledge_chunks

  # ---- Vector Search index ----------------------------------------------------
  # `vector_index`  MUST match an existing Atlas Vector Search index.
  # `embedding_key` MUST equal the index `path` AND the document field name
  #                 produced at ingestion time.
  # `dimensions`    MUST equal the index `numDimensions` AND the query
  #                 embedder's output size.
  # `text_key`      MUST equal the document field that holds the raw text.
  vector_index:       vector_index
  search_index:       default             # Atlas Search (Lucene) index for fulltext/hybrid
  text_key:           content
  embedding_key:      embedding
  relevance_score_fn: cosine
  dimensions:         1024

# Query-time embedding provider (must match what was used at ingestion).
# Supported: azure_openai | openai | voyageai | cohere | huggingface
#            gemini | bedrock_titan
embeddings:
  provider: gemini
  config:
    model: models/text-embedding-004
    google_api_key: ${GOOGLE_API_KEY}

    # --- VoyageAI example (with output projection to match a 512-dim index) ---
    # model: voyage-4
    # voyage_api_key: ${VOYAGE_API_KEY}
    # output_dimension: 512

    # --- Bedrock Titan example ---
    # model_id: amazon.titan-embed-text-v2:0
    # region_name: us-east-1
    # credentials_profile_name: default

# LLM for the Query Understanding Layer + Retrieval Planner.
# Supported: gemini | azure_openai | openai | anthropic | bedrock
planner:
  llm_provider: gemini
  config:
    model: gemini-2.5-flash
    google_api_key: ${GOOGLE_API_KEY}
    temperature: 0.1
  default_top_k: 20

retrieval:
  default_strategy: hybrid     # vector | fulltext | hybrid | graph | parent_doc | self_query
  hybrid:
    vector_weight: 0.6
    fulltext_weight: 0.4
  vector:
    num_candidates: 200        # used to derive `oversampling_factor`

# Phase 2 — present in config for forward compatibility.
reranking:
  enabled: false
  provider: cohere             # cohere | voyageai | cross_encoder
  model: rerank-english-v3.0

generation:
  provider: gemini             # gemini | azure_openai | openai | anthropic | bedrock | mistral
  model: gemini-1.5-pro

server:
  host: 0.0.0.0
  port: 8000
  mcp_host: 0.0.0.0
  mcp_port: 8001
  mcp_transport: streamable-http
  log_level: info
```

Environment loading is automatic: `searchaas/config/loader.py` calls `python-dotenv` to load the nearest `.env` walking up from the config module, so `uvicorn` / the diagnose CLI all see `${ATLAS_URI}`, `${GOOGLE_API_KEY}`, etc. without a wrapper script. Real shell vars always win over `.env` (`override=False`).

```python
# searchaas/config/loader.py  (excerpt)
import os, re, yaml
from functools import lru_cache
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

# ---- Auto-load the nearest .env on import (override=False) -----------------
def _autoload_dotenv() -> None:
    from dotenv import load_dotenv
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / ".env").exists():
            load_dotenv(parent / ".env", override=False); return
_autoload_dotenv()

_ENV = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")   # ${VAR} or ${VAR:-default}

def _expand(value):
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):  return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):  return [_expand(v) for v in value]
    return value

class AtlasConfig(BaseModel):
    uri: str
    database: str = "searchaas"
    collection: str = "knowledge_chunks"
    vector_index: str = "vector_index"
    search_index: str = "default"
    text_key: str = "content"
    embedding_key: str = "embedding"
    relevance_score_fn: str = "cosine"
    dimensions: int = -1                       # -1 = "do not validate"

    @field_validator("uri")
    @classmethod
    def _uri_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("atlas.uri is empty — set ATLAS_URI in env / .env")
        if not v.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError(f"atlas.uri must be a mongodb:// URI (got {v[:30]!r})")
        return v.strip()

class ProviderBlock(BaseModel):
    provider: str
    config: dict = Field(default_factory=dict)

class PlannerConfig(BaseModel):
    llm_provider: str
    config: dict = Field(default_factory=dict)
    default_top_k: int = 20

class RetrievalConfig(BaseModel):
    default_strategy: str = "hybrid"
    hybrid: dict = Field(default_factory=dict)
    vector: dict = Field(default_factory=dict)

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"; port: int = 8000
    mcp_host: str = "0.0.0.0"; mcp_port: int = 8001
    mcp_transport: str = "streamable-http"; log_level: str = "info"

class AppConfig(BaseModel):
    atlas: AtlasConfig
    embeddings: ProviderBlock
    planner: PlannerConfig
    retrieval: RetrievalConfig
    server: ServerConfig

@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> AppConfig:
    cfg_path = Path(path) if path else (Path(__file__).resolve().parent / "searchaas.yaml")
    if os.environ.get("SEARCHAAS_CONFIG"):
        cfg_path = Path(os.environ["SEARCHAAS_CONFIG"])
    with cfg_path.open() as f:
        return AppConfig(**_expand(yaml.safe_load(f)))
```

`AtlasConfig` is a real Pydantic model with field validators (not a raw dict), so misconfigurations like an empty URI or an unset dimension are caught at startup with an explicit error instead of failing inside `pymongo` / Atlas hundreds of lines later.

A single `build_container(AppConfig)` bootstrap function instantiates `EmbeddingFactory`, `LLMFactory`, `MongoDBAtlasVectorSearch`, `QueryUnderstandingLayer`, `RetrievalPlanner`, `RetrieverFactory`, and (in Phase 2) the orchestrator from this config and is shared by the API, MCP, and UI layers below.

### 7a. Configuration-driven flow (Phase 1)

```
.env (auto-loaded)  ──┐
                      ▼
searchaas/config/searchaas.yaml
        │  (${VAR} / ${VAR:-default} expansion)
        ▼
config.load_config() ─► AppConfig (Pydantic-validated)
        │
        ▼
app.build_container(AppConfig)
        │
        ├── AtlasFactory.chunks_collection()
        │       └── (later) AtlasFactory.ping() / collection_stats()
        │
        ├── EmbeddingFactory.create(cfg.embeddings.provider, cfg.embeddings.config)
        │       └── voyageai → _VoyageEmbeddingsWithExtras (output_dimension forwarding)
        │
        ├── LLMFactory.create(cfg.planner.llm_provider, cfg.planner.config)
        │
        ├── MongoDBAtlasVectorSearch.from_connection_string(
        │       connection_string=cfg.atlas.uri,
        │       namespace=f"{db}.{collection}",
        │       embedding=embeddings,
        │       index_name=cfg.atlas.vector_index,
        │       text_key=cfg.atlas.text_key,
        │       embedding_key=cfg.atlas.embedding_key,
        │       relevance_score_fn=cfg.atlas.relevance_score_fn,
        │       dimensions=cfg.atlas.dimensions,
        │   )
        │       └── _preflight_vector_index():
        │              • lists Atlas Search indexes
        │              • verifies vector_index exists + type=vectorSearch
        │              • verifies a field with path=embedding_key has type=vector
        │              • verifies index numDimensions == atlas.dimensions
        │              (logs ERROR on any mismatch — startup still completes)
        │
        ├── QueryUnderstandingLayer(llm)
        ├── PolicyStore(default_strategy=cfg.retrieval.default_strategy)
        ├── RetrievalPlanner(llm, policies, default_top_k)
        └── RetrieverFactory(
              vector_store, llm, collection,
              vector_index=cfg.atlas.vector_index,
              search_index=cfg.atlas.search_index,
              text_key=cfg.atlas.text_key,
              embedding_key=cfg.atlas.embedding_key,
              dimensions=cfg.atlas.dimensions,
              hybrid_weights=cfg.retrieval.hybrid,
              vector_num_candidates=cfg.retrieval.vector.num_candidates,
            )
        │
        ▼
Shared `Container` (lru_cached singleton) ─► FastAPI / FastMCP / CLI
```

Switching embeddings from **Gemini** to **Bedrock Titan**, the planner LLM from **Gemini** to **Azure OpenAI**, or the vector index from `vector_index` (768) to `voyage_vector_index` (512), is a YAML change only — no call site changes.

## 8. API Layer — FastAPI Endpoints

Every retrieval method is exposed as its own endpoint, alongside a planner-driven `/retrieve` route and an end-to-end `/query` route. A shared container wires the factories from the loaded YAML config.

```python
# searchaas/api/app.py  (excerpt)
import time, uuid
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from searchaas.app.bootstrap import get_container
from searchaas.config import load_config
from searchaas.infrastructure import AtlasFactory
from searchaas.observability import configure_logging, get_logger

configure_logging()
log = get_logger("searchaas.api")
app = FastAPI(title="SearchaaS Retrieval API")

# ---- request-ID + timing middleware ---------------------------------------
@app.middleware("http")
async def _request_logger(request: Request, call_next):
    rid = uuid.uuid4().hex[:8]; t0 = time.perf_counter()
    log.info("req %s -> %s %s", rid, request.method, request.url.path)
    try:
        resp = await call_next(request)
        log.info("req %s <- %s %sms", rid, resp.status_code,
                 round((time.perf_counter() - t0) * 1000, 1))
        resp.headers["x-request-id"] = rid
        return resp
    except Exception:
        log.exception("req %s !!", rid); raise

# ---- startup: ping Atlas so the operator sees connectivity issues early ---
@app.on_event("startup")
def _on_startup():
    log.info("Startup: embeddings=%s llm=%s", load_config().embeddings.provider,
             load_config().planner.llm_provider)
    ping = AtlasFactory.ping()
    (log.info if ping.get("ok") else log.error)("Startup: Atlas ping %s", ping)

class RetrieveRequest(BaseModel):
    query: str
    top_k: int | None = Field(default=None, ge=1, le=100)
    filters: dict = Field(default_factory=dict)

# ---- per-strategy endpoints ----
@app.post("/retrieve/vector")
def retrieve_vector(req): return _run("vector", req)
@app.post("/retrieve/fulltext")
def retrieve_fulltext(req): return _run("fulltext", req)
@app.post("/retrieve/hybrid")
def retrieve_hybrid(req): return _run("hybrid", req)
@app.post("/retrieve/graph")
def retrieve_graph(req): return _run("graph", req)
@app.post("/retrieve/parent-doc")
def retrieve_parent_doc(req): return _run("parent_doc", req)

@app.post("/retrieve")
def retrieve_auto(req: RetrieveRequest):
    """Planner picks the strategy dynamically from the analyzed query."""
    c = get_container()
    uq = c.understanding.process(req.query)
    plan = c.planner.plan(uq)
    if req.top_k:   plan.top_k = req.top_k
    if req.filters: plan.filters = {**plan.filters, **req.filters}
    docs = c.retrievers.create(plan).invoke(uq.rewritten)
    return {"strategy": plan.strategy, "plan": plan.model_dump(),
            "results": _serialize_docs(docs)}

# ---- diagnostics ----
@app.get("/health")
def health():
    cfg = load_config()
    return {"status": "ok",
            "embeddings_provider": cfg.embeddings.provider,
            "llm_provider": cfg.planner.llm_provider,
            "default_strategy": cfg.retrieval.default_strategy}

@app.get("/diagnose")
def diagnose():
    """Full self-check: Atlas ping + collection stats + embedder probe."""
    from searchaas.embeddings import EmbeddingFactory
    c = get_container()
    return {
        "config": {...},
        "atlas_ping": AtlasFactory.ping(),
        "collection_stats": AtlasFactory.collection_stats(),
        "embedder_probe": EmbeddingFactory.probe(c.embeddings),
    }

@app.post("/diagnose/vector")
def diagnose_vector(req):
    """Instrumented vector search — returns a stage-by-stage report."""
    return get_container().retrievers.run_vector(
        req.query, k=req.k, filters=req.filters)
```

Run with `uvicorn searchaas.api.app:app --host 0.0.0.0 --port 8000`. Response serializers strip the configured `embedding_key` from metadata (driven by YAML, not hardcoded), so changing `atlas.embedding_key` flows through to API output too.

## 9. MCP Layer — FastMCP Tools

The same retrieval methods are also published as MCP tools using **FastMCP**, so any MCP-compatible client or agent can call them. Each tool maps one-to-one to a strategy and reuses the identical factory container, guaranteeing parity between the REST and MCP surfaces.

```python
# mcp/server.py
from fastmcp import FastMCP
from config.loader import load_config
from bootstrap import build_container

mcp = FastMCP("searchaas")
container = build_container(load_config())

def _retrieve(strategy: str, query: str, top_k: int = 20, filters: dict | None = None):
    plan = container.planner.plan_for(strategy, query, top_k, filters or {})
    docs = container.retrievers.create(plan).invoke(query)
    return [d.page_content for d in docs]

@mcp.tool
def vector_search(query: str, top_k: int = 20) -> list[str]:
    """Semantic retrieval via Atlas Vector Search."""
    return _retrieve("vector", query, top_k)

@mcp.tool
def fulltext_search(query: str, top_k: int = 20) -> list[str]:
    """Keyword / lexical retrieval via Atlas Search."""
    return _retrieve("fulltext", query, top_k)

@mcp.tool
def hybrid_search(query: str, top_k: int = 20) -> list[str]:
    """Fused vector + full-text retrieval."""
    return _retrieve("hybrid", query, top_k)

@mcp.tool
def graph_search(query: str, top_k: int = 20) -> list[str]:
    """Multi-hop graph retrieval via $graphLookup / GraphRAG."""
    return _retrieve("graph", query, top_k)

@mcp.tool
def parent_doc_search(query: str, top_k: int = 20) -> list[str]:
    """Child-chunk match that returns the parent document."""
    return _retrieve("parent_doc", query, top_k)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

## 10a. Observability & Diagnostics (Phase 1)

Phase 1 ships a small but opinionated observability layer so that the most common Atlas + retrieval failures (URI not set, vector index not found, field-name mismatch, dimension mismatch, empty collection) are diagnosable in seconds without reading the codebase.

### Central logging

`searchaas/observability/logging.py` exposes `configure_logging()` (idempotent, dictConfig-based) and `get_logger(name)`. Every entrypoint — FastAPI, FastMCP, the CLI — calls `configure_logging()` at import time. Library loggers (`pymongo`, `langchain_mongodb`, `httpx`) are tuned through env vars instead of code changes:

| Env var | Default | Purpose |
| --- | --- | --- |
| `SEARCHAAS_LOG_LEVEL`  | `INFO`    | Root + `searchaas.*` level |
| `SEARCHAAS_LOG_FORMAT` | `plain`   | `json` for structured log shipping |
| `PYMONGO_LOG_LEVEL`    | `WARNING` | Wire-level Mongo traces (`DEBUG` is very verbose) |
| `LANGCHAIN_LOG_LEVEL`  | `INFO`    | `langchain_mongodb` internals |

Construction logs strip API keys / secrets, request middleware emits a per-request ID, and retriever builds log the configured `index / path / dim / k / filters` on every call.

### `searchaas.diagnose` CLI

```bash
python -m searchaas.diagnose --query "your test query" --k 5 [--filters '{"doc_type":"policy"}'] [--json]
```

Walks six stages, prints a `PASS/FAIL/WARN` line for each, exits non-zero on failure (CI-friendly):

1. **config** — YAML loads, AppConfig validates, `.env` resolved
2. **atlas connectivity** — `AtlasFactory.ping()` (redacted URI + latency)
3. **collection inventory** — doc count, sample embedding dim at the configured `embedding_key`, full search-index inventory with `latestDefinition`, vector-index path/dim match, atlas.dimensions vs index numDimensions, presence of `search_index`
4. **embedder probe** — `EmbeddingFactory.probe()` returns `{ok, dimensions, latency_ms}`
5. **dimension match** — compares `query` vs `stored` vs `index numDimensions`
6. **`$vectorSearch`** — `RetrieverFactory.run_vector()` runs a real query through the same factory the API uses; returns a structured report with stage = `embedding | dimension_mismatch | search | done` plus a `hint` for `OperationFailure`

The exact same logic is exposed over HTTP:

```bash
curl localhost:8000/health
curl localhost:8000/diagnose | jq
curl -X POST localhost:8000/diagnose/vector \
     -H 'content-type: application/json' \
     -d '{"query":"red costume","k":5}' | jq
```

### Pre-flight guarantees at container build time

`app/bootstrap.py:_preflight_vector_index()` runs every time the container is built. It:

- lists the collection's Atlas Search indexes,
- verifies a `vectorSearch` index with the configured `vector_index` name exists,
- verifies one of its fields has `path == atlas.embedding_key` and `type in {vector, autoEmbed}`,
- verifies `atlas.dimensions == numDimensions` for that field.

Any failure is logged at `ERROR` with a precise remediation hint (e.g. _"index has vector path(s) ['embedding-vectors'] but config embedding_key='embedding' — change `atlas.embedding_key` in YAML or recreate the index with path='embedding'"_) — but the app still starts so the operator can fix YAML and reload without restarting deps.

---

# PHASE 2

## 11. Metadata Intelligence

Metadata drives retrieval quality: **pre-filters** reduce search scope, **boosting** improves ranking during hybrid fusion, and **post-retrieval filters** enforce access control/governance.

```python
# metadata/intelligence.py
class MetadataEngine:
    def pre_filter(self, plan):           # narrow scope before retrieval
        return plan.filters
    def boost(self, results, boosts):     # adjust ranking during fusion
        return sorted(results, key=lambda r: self._score(r, boosts), reverse=True)
    def enforce_acl(self, results, principal):   # governance after retrieval
        return [r for r in results if self._authorized(r, principal)]
```

## 12. Self-Query Retriever

The Self-Query Retriever builds directly on the **Metadata Intelligence** layer, which is why it ships in Phase 2 rather than with the core retrieval strategies. Using `MongoDBAtlasSelfQueryRetriever` (`langchain-mongodb`) instantiated against the vector store, it does **not** independently "pick a retrieval strategy" from many options. The LLM's job is to analyze the user query, infer metadata filters, form a structured vector search query, and run it against Atlas Vector Search. Cross-strategy routing (vector vs. full-text vs. graph vs. hybrid) remains the responsibility of the AI-Driven Retrieval Planning Engine.

```python
# retrieval/self_query.py
from langchain_mongodb import MongoDBAtlasSelfQueryRetriever

class SelfQueryRetrieverFactory:
    def __init__(self, vector_store, llm, metadata_field_info):
        self._vs = vector_store
        self._llm = llm
        self._fields = metadata_field_info   # supplied by the Metadata Intelligence layer

    def create(self):
        # LLM infers metadata filters + builds a structured vector query
        return MongoDBAtlasSelfQueryRetriever.from_llm(
            llm=self._llm,
            vectorstore=self._vs,
            metadata_field_info=self._fields,
            document_contents="Enterprise knowledge chunks",
        )
```

## 13. Reranking Layer

Initial retrieval maximizes **recall**; reranking optimizes **precision** over an expanded candidate set. Providers: Cohere, Azure AI, Jina AI, Voyage AI, and open-source cross-encoders — especially important for consolidating multiple hybrid signals.

```python
# reranking/factory.py
from langchain_cohere import CohereRerank
from langchain_voyageai import VoyageAIRerank
from langchain_community.cross_encoders import HuggingFaceCrossEncoder

class RerankerFactory:
    _registry = {
        "cohere":   lambda c: CohereRerank(**c),
        "voyageai": lambda c: VoyageAIRerank(**c),
        "cross_encoder": lambda c: HuggingFaceCrossEncoder(**c),
        # "azure_ai" / "jina" wrappers implement the same compressor interface
    }

    @classmethod
    def create(cls, provider: str, config: dict):
        return cls._registry[provider](config)
```

Wrap any reranker with `ContextualCompressionRetriever` (`langchain`) to apply it as a post-retrieval compressor over a base retriever.

## 14. Grounded Response Generation

Top-ranked chunks are assembled into a grounded context payload for the response model. Providers: Azure OpenAI, OpenAI, Anthropic Claude, Gemini, Mistral, self-hosted LLMs. The generator receives original query, rewritten query, extracted metadata, retrieved chunks, citation references, and retrieval reasoning — improving grounding, explainability, and citation fidelity. Supports prompt template versioning and configurable grounding strategies.

```python
# generation/factory.py
class ResponseGeneratorFactory:
    @classmethod
    def create(cls, provider: str, config: dict):
        if provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI; return ChatGoogleGenerativeAI(**config)
        if provider == "azure_openai":
            from langchain_openai import AzureChatOpenAI; return AzureChatOpenAI(**config)
        if provider == "openai":
            from langchain_openai import ChatOpenAI; return ChatOpenAI(**config)
        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic; return ChatAnthropic(**config)
        if provider == "bedrock":
            from langchain_aws import ChatBedrock; return ChatBedrock(**config)
        if provider == "mistral":
            from langchain_mistralai import ChatMistralAI; return ChatMistralAI(**config)
        raise ValueError(f"Unsupported generation provider: {provider}")
```

> Generation reuses `LLMFactory` in practice (same provider registry), keeping the planner LLM and the response generator symmetric and config-driven.

## 15. Security and Governance

First-class concerns: authentication via **Microsoft Entra ID**; APIs secured with **OAuth2 + JWT validation**; secrets/model credentials in **Azure Key Vault**; network isolation via private endpoints/VNET; **document-level ACL enforcement** through metadata filtering. Audit trails are maintained for queries, prompt execution, model usage, generated retrieval plans, citations, and feedback loops — essential for regulated deployments.

## 16. Observability and Evaluation

Comprehensive telemetry: query/retrieval/reranking latency, token usage, model costs, retrieval quality, user feedback, empty-result queries, and hallucination indicators. Evaluation datasets and benchmark results are stored in Atlas for continuous, iterative improvement of retrieval quality over time.

---

## Orchestration (Factory Pattern, end to end)

```python
# app/orchestrator.py
class RetrievalOrchestrator:
    def __init__(self, understanding, planner, retriever_factory,
                 reranker_factory, generator_factory, metadata_engine, telemetry):
        self._uq = understanding
        self._planner = planner
        self._retrievers = retriever_factory
        self._rerankers = reranker_factory
        self._generators = generator_factory
        self._meta = metadata_engine
        self._telemetry = telemetry

    def run(self, raw_query: str, principal):
        uq = self._uq.process(raw_query)                       # Phase 1
        plan = self._planner.plan(uq)                          # Phase 1
        retriever = self._retrievers.create(plan)              # Phase 1
        results = retriever.invoke(uq.rewritten)               # Phase 1

        results = self._meta.enforce_acl(results, principal)   # Phase 2
        if plan.rerank:                                        # Phase 2
            reranker = self._rerankers.create(plan.rerank_model, {})
            results = reranker.compress_documents(results, uq.rewritten)

        generator = self._generators.create("azure_openai", {})# Phase 2
        answer = generator.invoke(self._ground(uq, plan, results))
        self._telemetry.record(uq, plan, results, answer)      # Phase 2
        return answer
```