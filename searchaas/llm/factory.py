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


# Credential/endpoint field names that belong to OTHER providers. The shared
# YAML config block may carry several (api_key, google_api_key, azure_* ...) so
# the active provider can be swapped via env without editing YAML; each factory
# keeps only the fields it understands to avoid "unexpected keyword argument"
# errors.
_AZURE_FIELDS = {
    "azure_endpoint", "azure_deployment", "openai_api_version",
    "azure_api_key", "deployment_name", "api_version",
}


def _keep(cfg: dict[str, Any], *names: str) -> dict[str, Any]:
    """Keep only the listed keys (used to isolate a provider's kwargs)."""
    return {k: v for k, v in cfg.items() if k in names}


def _drop(cfg: dict[str, Any], *names: str) -> dict[str, Any]:
    return {k: v for k, v in cfg.items() if k not in names}


def _make_gemini(cfg: dict[str, Any]) -> BaseChatModel:
    """
    Google Gemini chat model.

    Common config keys:
        model           : e.g. "gemini-2.0-flash", "gemini-1.5-pro"
        google_api_key  : API key (resolved from env in YAML)
        temperature     : float
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(**_drop(cfg, "api_key", *_AZURE_FIELDS))


def _make_azure_openai(cfg: dict[str, Any]) -> BaseChatModel:
    """
    Azure OpenAI chat model.

    Expected config keys (after YAML env-expansion):
        azure_endpoint     : https://<resource>.openai.azure.com/ or
                             https://<resource>.cognitiveservices.azure.com/
        azure_deployment   : the model DEPLOYMENT name (not the bare model id)
        openai_api_version : e.g. "2024-08-01-preview"
        azure_api_key      : key (mapped to AzureChatOpenAI's `api_key`)
        temperature        : float
    """
    from langchain_openai import AzureChatOpenAI
    azure_cfg = {
        k: v for k, v in cfg.items()
        if k in {"azure_endpoint", "azure_deployment", "openai_api_version",
                 "temperature", "max_tokens", "model"}
    }
    # Map our neutral `azure_api_key` to the constructor's `api_key`.
    if cfg.get("azure_api_key"):
        azure_cfg["api_key"] = cfg["azure_api_key"]
    elif cfg.get("api_key"):
        azure_cfg["api_key"] = cfg["api_key"]
    return AzureChatOpenAI(**azure_cfg)


def _make_openai(cfg: dict[str, Any]) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(**_drop(cfg, "google_api_key", *_AZURE_FIELDS))


def _make_anthropic(cfg: dict[str, Any]) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(**_drop(cfg, "google_api_key", "api_key", *_AZURE_FIELDS))


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
        # Drop keys whose value is empty/None so that provider-specific
        # credential fields left unset in YAML (e.g. google_api_key when using
        # OpenAI) don't reach the provider constructor as stray kwargs.
        clean_cfg = {k: v for k, v in (config or {}).items()
                     if v is not None and v != ""}
        log.info("LLM: building provider=%s config=%s", provider, redact_cfg(clean_cfg))
        try:
            return cls._registry[provider](clean_cfg)
        except Exception:
            log.exception("LLM: provider %s failed to construct", provider)
            raise
