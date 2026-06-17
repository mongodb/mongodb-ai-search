"""
LLM Factory — pluggable chat-LLM providers used by the Query Understanding
Layer and the Retrieval Planner in Phase 1 (and Response Generation in Phase 2).

Supported providers
-------------------
- gemini        : langchain_google_genai.ChatGoogleGenerativeAI
- azure_openai  : langchain_openai.AzureChatOpenAI
- openai        : langchain_openai.ChatOpenAI
- anthropic     : langchain_anthropic.ChatAnthropic
- bedrock       : langchain_aws.ChatBedrock        (Claude/Titan/Mistral on Bedrock)
"""
from __future__ import annotations

from typing import Any, Callable

from langchain_core.language_models import BaseChatModel

from searchaas.observability import get_logger
from searchaas.utils import redact_cfg

log = get_logger("searchaas.llm")


def _make_gemini(cfg: dict[str, Any]) -> BaseChatModel:
    """
    Google Gemini chat model.

    Common config keys:
        model           : e.g. "gemini-2.0-flash", "gemini-1.5-pro"
        google_api_key  : API key (resolved from env in YAML)
        temperature     : float
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(**cfg)


def _make_azure_openai(cfg: dict[str, Any]) -> BaseChatModel:
    from langchain_openai import AzureChatOpenAI
    return AzureChatOpenAI(**cfg)


def _make_openai(cfg: dict[str, Any]) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(**cfg)


def _make_anthropic(cfg: dict[str, Any]) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(**cfg)


def _make_bedrock(cfg: dict[str, Any]) -> BaseChatModel:
    try:
        from langchain_aws import ChatBedrock
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "bedrock requires `langchain-aws` and `boto3`. "
            "Install with: pip install langchain-aws boto3"
        ) from exc
    # Drop empty `credentials_profile_name` so boto3 falls back to the default
    # credential chain (env vars / ECS task role / EC2 instance role / SSO).
    cfg = {k: v for k, v in cfg.items() if not (k == "credentials_profile_name" and not v)}
    return ChatBedrock(**cfg)


class LLMFactory:
    """Create chat LLMs by provider name + config dict."""

    _registry: dict[str, Callable[[dict[str, Any]], BaseChatModel]] = {
        "gemini":       _make_gemini,
        "azure_openai": _make_azure_openai,
        "openai":       _make_openai,
        "anthropic":    _make_anthropic,
        "bedrock":      _make_bedrock,
    }

    @classmethod
    def supported(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def register(cls, name: str, factory: Callable[[dict[str, Any]], BaseChatModel]) -> None:
        cls._registry[name] = factory

    @classmethod
    def create(cls, provider: str, config: dict[str, Any] | None = None) -> BaseChatModel:
        if provider not in cls._registry:
            raise ValueError(
                f"Unsupported LLM provider: {provider!r}. "
                f"Supported: {cls.supported()}"
            )
        log.info("LLM: building provider=%s config=%s", provider, redact_cfg(config or {}))
        try:
            return cls._registry[provider](config or {})
        except Exception:
            log.exception("LLM: provider %s failed to construct", provider)
            raise
