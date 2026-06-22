"""
YAML configuration loader with environment variable expansion.

Supports `${VAR}` and `${VAR:-default}` substitution so that secrets stay in
env / Azure Key Vault while non-secret defaults live in YAML.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Auto-load a project-local .env on import so that running
# `uvicorn searchaas.api.app:app` "just works" without a wrapper script.
# A real shell env var always wins over the .env file.
# ---------------------------------------------------------------------------
def _autoload_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            load_dotenv(candidate, override=False)
            return


_autoload_dotenv()


# Matches ${VAR} or ${VAR:-default}
_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def _strip_quotes(v: str) -> str:
    """Remove wrapping single or double quotes injected by docker --env-file.

    Docker's --env-file passes values verbatim (including surrounding quotes),
    whereas a shell `source .env` strips them.  For example:
        ATLAS_URI='mongodb+srv://...'  →  atlas.uri = "'mongodb+srv://...'"
    This function normalises both cases to the bare value.
    """
    for q in ('"', "'"):
        if len(v) >= 2 and v[0] == q and v[-1] == q:
            return v[1:-1]
    return v


def _expand(value: Any) -> Any:
    """Recursively expand ${ENV_VAR} references in a config tree."""
    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            name = match.group(1)
            default = match.group(2) or ""
            raw = os.environ.get(name, default)
            return _strip_quotes(raw)
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


class AtlasConfig(BaseModel):
    uri: str
    database: str = "searchaas"
    collection: str = "knowledge_chunks"
    vector_index: str = "vector_index"
    search_index: str = "default"
    # Field names inside each document. These MUST match the `path` set in
    # the Atlas Vector Search index, and the document fields produced at
    # ingestion time. Override per-deployment in YAML.
    text_key: str = "content"
    # `embedding_key` MUST be `None` when using AutoEmbeddings (embeddings.provider: auto)
    # because Atlas Vector Search manages embedding storage internally. For other
    # providers, this is the field name where the vector is stored in each document.
    embedding_key: str | None = "embedding"
    # `relevance_score_fn` MUST be `None` when using AutoEmbeddings; the similarity
    # is fully managed by the Atlas vector index definition.
    relevance_score_fn: str | None = "cosine"
    # Vector dimensionality. MUST equal the `numDimensions` configured on the
    # Atlas Vector Search index AND the query embedder's output size.
    # -1 means "do not validate" (langchain-mongodb default).
    # MUST be -1 when using AutoEmbeddings.
    dimensions: int = -1
    # The user's own Atlas index definitions, pasted verbatim from the Atlas
    # UI JSON editor. They are the source of truth for which metadata fields
    # are filterable; filters on any other field are dropped before querying
    # Atlas instead of failing the aggregation.
    vector_index_definition: dict[str, Any] = Field(default_factory=dict)
    search_index_definition: dict[str, Any] = Field(default_factory=dict)

    @property
    def filter_fields(self) -> list[str]:
        """Metadata paths declared as `type: filter` in the vector index
        definition — the only paths Atlas accepts in `$vectorSearch.filter`."""
        fields = (self.vector_index_definition or {}).get("fields") or []
        return [
            f["path"] for f in fields
            if isinstance(f, dict) and f.get("type") == "filter" and f.get("path")
        ]

    @property
    def search_filter_fields(self) -> list[str] | None:
        """Field paths filterable in the Lucene `$search` index.
        `None` means the mapping is dynamic, i.e. every field is indexed and
        no restriction applies. An empty list (no definition provided, or a
        static mapping with no fields) means nothing is filterable."""
        mappings = (self.search_index_definition or {}).get("mappings") or {}
        if mappings.get("dynamic"):
            return None
        return sorted((mappings.get("fields") or {}).keys())

    @field_validator("uri")
    @classmethod
    def _uri_not_empty(cls, v: str) -> str:  # noqa: D401
        if not v or not v.strip():
            raise ValueError(
                "atlas.uri is empty. Set ATLAS_URI in your environment or .env file. "
                "Expected format: mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true"
            )
        if not v.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError(
                f"atlas.uri must start with mongodb:// or mongodb+srv:// (got: {v[:30]!r}...)"
            )
        return v.strip()

    @field_validator("embedding_key", "relevance_score_fn", mode="before")
    @classmethod
    def _empty_or_null_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().lower() in {"", "null", "none"}:
            return None
        return v


class ProviderBlock(BaseModel):
    provider: str
    config: dict[str, Any] = Field(default_factory=dict)


class PlannerConfig(BaseModel):
    llm_provider: str
    config: dict[str, Any] = Field(default_factory=dict)
    default_top_k: int = 20


class RetrievalConfig(BaseModel):
    default_strategy: str = "hybrid"
    hybrid: dict[str, Any] = Field(default_factory=dict)
    vector: dict[str, Any] = Field(default_factory=dict)


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8001
    mcp_transport: str = "streamable-http"
    log_level: str = "info"


class AppConfig(BaseModel):
    atlas: AtlasConfig
    embeddings: ProviderBlock
    planner: PlannerConfig
    retrieval: RetrievalConfig
    server: ServerConfig


def _default_config_path() -> Path:
    override = os.environ.get("SEARCHAAS_CONFIG")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "searchaas.yaml"


@lru_cache(maxsize=1)
def load_config(path: str | os.PathLike | None = None) -> AppConfig:
    """Load and validate the YAML config exactly once per process."""
    cfg_path = Path(path) if path else _default_config_path()
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    expanded = _expand(raw)
    return AppConfig(**expanded)



