"""
LLM factory — returns a chat model based on settings.

Supported providers (LLM_PROVIDER):
    anthropic -> ChatAnthropic  (default; ANTHROPIC_API_KEY required)
    groq      -> ChatGroq       (fast/free; GROQ_API_KEY required)
    ollama    -> ChatOllama     (local; Ollama service required)
    # gemini  -> DISABLED (20 req/day free-tier quota; see commented block below)

Role-based provider routing:
    ROLE_PROVIDER_<ROLE>=<provider>  overrides the default for a specific role.
    Examples:
        ROLE_PROVIDER_CRITIC=groq         # critic on Groq (fast, free)
        ROLE_PROVIDER_SUPERVISOR=anthropic

Per-role model overrides:
    ROLE_MODEL_<ROLE>=<model>  selects a specific model for that role regardless
    of the provider's default. Only applies when the role's provider supports it.
    Example:
        ROLE_MODEL_EXECUTOR=claude-sonnet-4-6  # stronger model for document rewriting

Embeddings (Mem0, RAG) always use Ollama nomic-embed-text — unaffected by LLM_PROVIDER.
"""
import os
from functools import lru_cache
from loguru import logger
from config import settings


def _provider_for_role(role: str | None) -> str:
    """Return the provider to use for a given role, or the default."""
    if role:
        override = os.environ.get(f"ROLE_PROVIDER_{role.upper()}")
        if override:
            return override.lower()
    return settings.llm_provider.lower()


def _model_for_role(role: str | None) -> str | None:
    """Return a per-role model override from ROLE_MODEL_<ROLE>, or None."""
    if role:
        return os.environ.get(f"ROLE_MODEL_{role.upper()}")
    return None


@lru_cache(maxsize=8)
def get_llm(
    model: str | None = None,
    temperature: float | None = None,
    role: str | None = None,
):
    """Return a cached chat model.

    Args:
        model: override the provider's default model
        temperature: override settings.temperature
        role: agent role (e.g. 'critic', 'supervisor'). When set, the provider
              is resolved via ROLE_PROVIDER_<ROLE> env var, falling back to
              settings.llm_provider.
    """
    t = temperature if temperature is not None else settings.temperature
    provider = _provider_for_role(role)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if not settings.anthropic_api_key:
            raise RuntimeError(
                "Provider=anthropic but ANTHROPIC_API_KEY is not set in .env."
            )
        m = model or _model_for_role(role) or settings.anthropic_model
        logger.info(f"Loading LLM: provider=anthropic model={m} temp={t} role={role}")
        return ChatAnthropic(
            model=m,
            temperature=t,
            anthropic_api_key=settings.anthropic_api_key,
        )

    if provider == "groq":
        from langchain_groq import ChatGroq

        if not settings.groq_api_key:
            raise RuntimeError(
                "Provider=groq but GROQ_API_KEY is not set. "
                "Get a free key at https://console.groq.com/keys"
            )
        m = model or _model_for_role(role) or settings.groq_model
        logger.info(f"Loading LLM: provider=groq model={m} temp={t} role={role}")
        return ChatGroq(
            model=m,
            temperature=t,
            groq_api_key=settings.groq_api_key,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        m = model or _model_for_role(role) or settings.ollama_model
        logger.info(f"Loading LLM: provider=ollama model={m} temp={t} role={role}")
        return ChatOllama(
            base_url=settings.ollama_base_url,
            model=m,
            temperature=t,
        )

    # ── Gemini disabled — free tier limited to ~20 req/day, breaks multi-agent runs ──
    # To re-enable: set LLM_PROVIDER=gemini and GOOGLE_API_KEY in .env,
    # then uncomment the block below and add langchain-google-genai back to imports.
    #
    # if provider == "gemini":
    #     from langchain_google_genai import ChatGoogleGenerativeAI
    #     if not settings.google_api_key:
    #         raise RuntimeError("Provider=gemini but GOOGLE_API_KEY is not set.")
    #     m = model or _model_for_role(role) or settings.gemini_model
    #     logger.info(f"Loading LLM: provider=gemini model={m} temp={t} role={role}")
    #     return ChatGoogleGenerativeAI(
    #         model=m, temperature=t,
    #         google_api_key=settings.google_api_key,
    #         convert_system_message_to_human=False,
    #     )

    raise ValueError(
        f"Unknown provider={provider!r}. Valid: 'anthropic', 'groq', 'ollama'."
    )
