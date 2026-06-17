"""
Embedding Factory — pluggable QUERY embedding providers.

Document embeddings are pre-generated externally; this factory only generates
QUERY embeddings at runtime. The chosen provider/model MUST match what was
used at ingestion time (see `Chunk.embedding_model`).

Supported providers
-------------------
- auto             : langchain_mongodb.AutoEmbeddings — MongoDB server-side
                     auto-embedding; no client-side API keys or dimensions.
                     Embedding happens inside Atlas Vector Search at query +
                     ingestion time using the model configured in the index.
- azure_openai     : langchain_openai.AzureOpenAIEmbeddings
- openai           : langchain_openai.OpenAIEmbeddings
- voyageai         : langchain_voyageai.VoyageAIEmbeddings
- cohere           : langchain_cohere.CohereEmbeddings
- huggingface      : langchain_huggingface.HuggingFaceEmbeddings
- gemini           : langchain_google_genai.GoogleGenerativeAIEmbeddings
- bedrock_titan    : langchain_aws.BedrockEmbeddings (Amazon Titan models)
"""
from __future__ import annotations

import time
from typing import Any, Callable

from langchain_core.embeddings import Embeddings

from searchaas.observability import get_logger
from searchaas.utils import redact_cfg

log = get_logger("searchaas.embeddings")


# -------- Provider constructors (lazy imports so optional deps stay optional) --


def _make_auto(cfg: dict[str, Any]) -> Embeddings:
    """
    MongoDB Atlas server-side auto-embedding.

    No client-side API key, no dimensions, no provider-specific config — Atlas
    handles embedding inside the vector index using the model declared in the
    `autoEmbed` field of the index definition.

    Required config key:
        model : embedding model name (e.g. "voyage-3-large",
                "text-embedding-3-large", "amazon.titan-embed-text-v2:0").
                MUST match what is configured in the Atlas vector index.

    See: https://reference.langchain.com/python/langchain-mongodb/embeddings/AutoEmbeddings
    """
    from langchain_mongodb.embeddings import AutoEmbeddings

    model = cfg.get("model")
    if not model:
        raise ValueError(
            "auto-embeddings requires `model` in embeddings.config — "
            "set it to the model declared in your Atlas vector index."
        )
    return AutoEmbeddings(model=model)


def _make_azure_openai(cfg: dict[str, Any]) -> Embeddings:
    from langchain_openai import AzureOpenAIEmbeddings
    return AzureOpenAIEmbeddings(**cfg)


def _make_openai(cfg: dict[str, Any]) -> Embeddings:
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(**cfg)


class _VoyageEmbeddingsWithExtras(Embeddings):
    """
    Thin wrapper around `VoyageAIEmbeddings` that exposes the Voyage SDK's
    `output_dimension` and `output_dtype` parameters (not exposed by
    `langchain-voyageai` 0.1.x).

    Subclassing rather than monkey-patching because `VoyageAIEmbeddings` is a
    Pydantic v2 model and disallows attribute assignment.
    """

    def __init__(self, base, **extras: Any) -> None:
        self._base = base
        self._extras = extras

    @property
    def model(self) -> str:
        return self._base.model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        resp = self._base._client.embed(
            texts,
            model=self._base.model,
            input_type="document",
            truncation=bool(self._base.truncation),
            **self._extras,
        )
        return resp.embeddings

    def embed_query(self, text: str) -> list[float]:
        resp = self._base._client.embed(
            [text],
            model=self._base.model,
            input_type="query",
            truncation=bool(self._base.truncation),
            **self._extras,
        )
        return resp.embeddings[0]


def _make_voyageai(cfg: dict[str, Any]) -> Embeddings:
    """
    Voyage AI embeddings.

    Supports the SDK's `output_dimension` and `output_dtype` parameters
    (not exposed by langchain-voyageai 0.1.x) via a thin subclass — useful
    for matching a smaller-dimension Atlas Vector Search index.
    """
    from langchain_voyageai import VoyageAIEmbeddings

    extra_keys = {"output_dimension", "output_dtype"}
    cfg = dict(cfg)                                       # never mutate caller's dict
    extras = {k: cfg.pop(k) for k in list(cfg) if k in extra_keys}
    base = VoyageAIEmbeddings(**cfg)
    if not extras:
        return base
    log.info("Voyage: applying extra params %s", extras)
    return _VoyageEmbeddingsWithExtras(base, **extras)


def _make_cohere(cfg: dict[str, Any]) -> Embeddings:
    from langchain_cohere import CohereEmbeddings
    return CohereEmbeddings(**cfg)


def _make_huggingface(cfg: dict[str, Any]) -> Embeddings:
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(**cfg)


def _make_gemini(cfg: dict[str, Any]) -> Embeddings:
    """
    Google Gemini embeddings.

    Common config keys:
        model            : e.g. "models/text-embedding-004"
        google_api_key   : API key (resolved from env in YAML)
        task_type        : optional, e.g. "retrieval_query"
    """
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    return GoogleGenerativeAIEmbeddings(**cfg)


def _make_bedrock_titan(cfg: dict[str, Any]) -> Embeddings:
    """
    Amazon Bedrock Titan embeddings.

    Common config keys:
        model_id                 : e.g. "amazon.titan-embed-text-v2:0"
        region_name              : AWS region, e.g. "us-east-1"
        credentials_profile_name : optional AWS profile
    """
    try:
        from langchain_aws import BedrockEmbeddings
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "bedrock_titan requires `langchain-aws` and `boto3`. "
            "Install with: pip install langchain-aws boto3"
        ) from exc
    # Drop empty `credentials_profile_name` so boto3 falls back to the default
    # credential chain (env vars / ECS task role / EC2 instance role / SSO).
    cfg = {k: v for k, v in cfg.items() if not (k == "credentials_profile_name" and not v)}
    return BedrockEmbeddings(**cfg)


# ---------------------------------- Factory -----------------------------------


class EmbeddingFactory:
    """Create QUERY embedders by provider name + config dict."""

    _registry: dict[str, Callable[[dict[str, Any]], Embeddings]] = {
        "auto":          _make_auto,
        "azure_openai":  _make_azure_openai,
        "openai":        _make_openai,
        "voyageai":      _make_voyageai,
        "cohere":        _make_cohere,
        "huggingface":   _make_huggingface,
        "gemini":        _make_gemini,
        "bedrock_titan": _make_bedrock_titan,
    }

    @classmethod
    def supported(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def register(cls, name: str, factory: Callable[[dict[str, Any]], Embeddings]) -> None:
        """Register a custom embedding provider at runtime."""
        cls._registry[name] = factory

    @classmethod
    def create(cls, provider: str, config: dict[str, Any] | None = None) -> Embeddings:
        if provider not in cls._registry:
            raise ValueError(
                f"Unsupported embedding provider: {provider!r}. "
                f"Supported: {cls.supported()}"
            )
        log.info("Embeddings: building provider=%s config=%s", provider, redact_cfg(config or {}))
        try:
            return cls._registry[provider](config or {})
        except Exception:
            log.exception("Embeddings: provider %s failed to construct", provider)
            raise

    @classmethod
    def probe(cls, embedder: Embeddings, sample: str = "ping") -> dict[str, Any]:
        """
        Embed a tiny string to verify creds + return the vector dimensionality.

        For AutoEmbeddings (MongoDB server-side), embedding happens inside the
        Atlas index — there is no client-side embed call to probe, so we just
        return a `mode='auto'` marker instead of failing.
        """
        # AutoEmbeddings doesn't implement embed_query (raises NotImplementedError)
        # because the server handles it.  Detect by class name to avoid an import.
        if type(embedder).__name__ == "AutoEmbeddings":
            model = getattr(embedder, "model", None)
            log.info("Embeddings: probe — auto-embed mode (model=%s, server-side)", model)
            return {"ok": True, "mode": "auto", "model": model, "dimensions": None}

        t0 = time.perf_counter()
        try:
            vec = embedder.embed_query(sample)
            dim = len(vec) if hasattr(vec, "__len__") else None
            ms = round((time.perf_counter() - t0) * 1000, 1)
            log.info("Embeddings: probe OK dim=%s latency_ms=%s", dim, ms)
            return {"ok": True, "dimensions": dim, "latency_ms": ms}
        except Exception as exc:
            log.exception("Embeddings: probe failed")
            return {"ok": False, "error_kind": exc.__class__.__name__, "error": str(exc)}
