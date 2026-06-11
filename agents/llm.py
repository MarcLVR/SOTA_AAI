"""
LLM factory — returns a chat model based on settings, via LangChain's
``init_chat_model``.

Provider-agnostic by design: any provider supported by ``init_chat_model``
works with **zero code changes** — set ``LLM_PROVIDER`` and the matching model
+ credentials in ``.env`` and the factory builds the right client. No provider
SDK is imported anywhere outside this module.

First-class (shipped + tested) providers:
    anthropic -> ChatAnthropic  (default; ANTHROPIC_API_KEY required)
    groq      -> ChatGroq        (fast/free; GROQ_API_KEY required)
    ollama    -> ChatOllama       (fully local, NO API key — the offline path)
    openai    -> ChatOpenAI        (also covers OpenAI-compatible endpoints via
                                    OPENAI_BASE_URL: vLLM, LM Studio, OpenRouter…)

Any other provider string (e.g. ``mistralai``, ``google_genai``, ``bedrock``,
``cohere``, ``fireworks``, ``together``) is forwarded straight to
``init_chat_model``; install the matching ``langchain-<provider>`` extra and set
that provider's standard API-key env var. Pick the model with ``LLM_MODEL`` or
a per-role ``ROLE_MODEL_<ROLE>``.

Role-based routing:
    ROLE_PROVIDER_<ROLE>=<provider>   override the default provider for one role
    ROLE_MODEL_<ROLE>=<model>         override the model for one role
    ROLE_MAX_TOKENS_<ROLE>=<int>      override the output-token cap for one role
    Examples:
        ROLE_PROVIDER_CRITIC=groq            # critic on Groq (fast, free)
        ROLE_MODEL_EXECUTOR=claude-sonnet-4-6

Embeddings (Mem0, RAG) always use local models — unaffected by LLM_PROVIDER.
"""
import os
from functools import lru_cache
from loguru import logger
from config import settings


@lru_cache(maxsize=1)
def _dotenv_values() -> dict:
    """Parse the .env file once for dynamically-named ROLE_* overrides.

    pydantic-settings reads .env into the Settings object but, with extra='ignore',
    drops keys it has no field for (the per-role ROLE_PROVIDER_*/ROLE_MODEL_*/
    ROLE_MAX_TOKENS_* are dynamic). Those keys therefore never reach os.environ, so
    a .env-defined override would be silently ignored. We parse .env directly as a
    fallback; a real environment variable still wins.
    """
    try:
        from dotenv import dotenv_values
        return {k: v for k, v in dotenv_values(settings.model_config.get("env_file", ".env")).items() if v is not None}
    except Exception:
        return {}


def _role_env(key: str) -> str | None:
    """Resolve a ROLE_* override: real env var first, then .env fallback."""
    return os.environ.get(key) or _dotenv_values().get(key)


# ── Per-role output-token caps ────────────────────────────────────────────────
# Chat models default to very high max_tokens (often the model maximum) — absurd
# for our use cases. These caps prevent runaway output costs with no quality loss.
# Override any single role via ROLE_MAX_TOKENS_<ROLE>=N in .env.

_ROLE_MAX_TOKENS: dict[str, int] = {
    "supervisor": 512,    # routing decision JSON only
    "critic":     512,    # score + one-line critique
    "auditor":    1500,   # audit summary / findings
    "semantic":   1000,   # compliance analysis (3 findings)
    "general":    2000,   # chat / reasoning
    "researcher": 2000,   # research summary
    "coder":      2000,   # code output
    "executor":   2000,   # document fix instructions
}
_DEFAULT_MAX_TOKENS = 2000  # cap for any role not listed above

# Providers that take the output cap as ``num_predict`` rather than ``max_tokens``.
_NUM_PREDICT_PROVIDERS = {"ollama"}

# Per-provider default model resolver (called lazily so settings stay the source).
_PROVIDER_DEFAULT_MODEL = {
    "anthropic": lambda: settings.anthropic_model,
    "groq":      lambda: settings.groq_model,
    "ollama":    lambda: settings.ollama_model,
    "openai":    lambda: settings.openai_model,
}


def _max_tokens_for_role(role: str | None) -> int:
    """Return the output-token cap for a role, with env-var override support."""
    if role:
        env_val = _role_env(f"ROLE_MAX_TOKENS_{role.upper()}")
        if env_val:
            return int(env_val)
        return _ROLE_MAX_TOKENS.get(role, _DEFAULT_MAX_TOKENS)
    return _DEFAULT_MAX_TOKENS


def _provider_for_role(role: str | None) -> str:
    """Return the provider to use for a given role, or the default."""
    if role:
        override = _role_env(f"ROLE_PROVIDER_{role.upper()}")
        if override:
            return override.lower()
    return settings.llm_provider.lower()


def _model_for_role(role: str | None) -> str | None:
    """Return a per-role model override from ROLE_MODEL_<ROLE>, or None."""
    if role:
        return _role_env(f"ROLE_MODEL_{role.upper()}")
    return None


def _resolve_model(provider: str, role: str | None, model: str | None) -> str:
    """Pick the model: explicit arg > per-role override > provider default > LLM_MODEL."""
    if model:
        return model
    role_model = _model_for_role(role)
    if role_model:
        return role_model
    if provider in _PROVIDER_DEFAULT_MODEL:
        return _PROVIDER_DEFAULT_MODEL[provider]()
    if settings.llm_model:
        return settings.llm_model
    raise RuntimeError(
        f"No model configured for provider={provider!r}. "
        f"Set LLM_MODEL (or ROLE_MODEL_{(role or '').upper()}) in .env."
    )


def _provider_kwargs(provider: str, max_tok: int) -> dict:
    """Provider-specific constructor kwargs (credentials, endpoints, token cap)."""
    kwargs: dict = {}

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError("Provider=anthropic but ANTHROPIC_API_KEY is not set in .env.")
        kwargs["api_key"] = settings.anthropic_api_key

    elif provider == "groq":
        if not settings.groq_api_key:
            raise RuntimeError(
                "Provider=groq but GROQ_API_KEY is not set. "
                "Get a free key at https://console.groq.com/keys"
            )
        kwargs["api_key"] = settings.groq_api_key

    elif provider == "ollama":
        # Fully local — no API key. Ollama uses num_predict for the output cap.
        kwargs["base_url"] = settings.ollama_base_url

    elif provider == "openai":
        # Also covers any OpenAI-compatible endpoint via OPENAI_BASE_URL.
        if settings.openai_api_key:
            kwargs["api_key"] = settings.openai_api_key
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url

    # Any other provider: init_chat_model reads that provider's standard
    # API-key env var itself; nothing extra to inject here.

    if provider in _NUM_PREDICT_PROVIDERS:
        kwargs["num_predict"] = max_tok
    else:
        kwargs["max_tokens"] = max_tok

    return kwargs


@lru_cache(maxsize=16)
def get_llm(
    model: str | None = None,
    temperature: float | None = None,
    role: str | None = None,
):
    """Return a cached chat model built via LangChain ``init_chat_model``.

    Args:
        model: override the resolved model name.
        temperature: override settings.temperature.
        role: agent role (e.g. 'critic'). When set, the provider/model/token-cap
              are resolved via ROLE_PROVIDER_<ROLE> / ROLE_MODEL_<ROLE> /
              ROLE_MAX_TOKENS_<ROLE>, falling back to the global defaults.
    """
    from langchain.chat_models import init_chat_model

    t        = temperature if temperature is not None else settings.temperature
    provider = _provider_for_role(role)
    max_tok  = _max_tokens_for_role(role)
    m        = _resolve_model(provider, role, model)
    kwargs   = _provider_kwargs(provider, max_tok)

    logger.info(
        f"Loading LLM: provider={provider} model={m} "
        f"max_tokens={max_tok} temp={t} role={role}"
    )
    try:
        return init_chat_model(m, model_provider=provider, temperature=t, **kwargs)
    except ImportError as e:
        raise RuntimeError(
            f"Provider {provider!r} needs an extra package that isn't installed: {e}. "
            f"Install the matching 'langchain-{provider}' integration."
        ) from e
