"""
LLM factory — returns a chat model based on settings.

Supports three providers selected via LLM_PROVIDER:
    gemini  -> ChatGoogleGenerativeAI (Google AI Studio, free tier)
    groq    -> ChatGroq (Groq, free tier, ultra-fast Llama 3.3 70B)
    ollama  -> ChatOllama (local, requires Ollama running)

Role-based routing:
    Different agent roles can use different providers. Set ROLE_PROVIDER_<role>
    in .env to override the default provider for that role.

    Examples:
        ROLE_PROVIDER_CRITIC=groq        # critic uses Groq (fast)
        ROLE_PROVIDER_SUPERVISOR=gemini  # supervisor uses Gemini (reasoning)

    Any role without an override uses the default LLM_PROVIDER.

Embeddings (Mem0, RAG via nomic-embed-text) are unaffected. They live in
memory/episodic.py and memory/vector_store.py and continue to use Ollama.
"""
import os
from functools import lru_cache
from loguru import logger
from config import settings


def _provider_for_role(role: str | None) -> str:
    """Return the provider to use for a given role, or the default."""
    if role:
        env_key = f"ROLE_PROVIDER_{role.upper()}"
        override = os.environ.get(env_key)
        if override:
            return override.lower()
    return settings.llm_provider.lower()


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

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not settings.google_api_key:
            raise RuntimeError(
                "Provider=gemini but GOOGLE_API_KEY is not set. "
                "Get a free key at https://aistudio.google.com/apikey"
            )
        m = model or settings.gemini_model
        logger.info(f"Loading LLM: provider=gemini model={m} temp={t} role={role}")
        return ChatGoogleGenerativeAI(
            model=m,
            temperature=t,
            google_api_key=settings.google_api_key,
            convert_system_message_to_human=False,
        )

    if provider == "groq":
        from langchain_groq import ChatGroq

        if not settings.groq_api_key:
            raise RuntimeError(
                "Provider=groq but GROQ_API_KEY is not set. "
                "Get a free key at https://console.groq.com/keys"
            )
        m = model or settings.groq_model
        logger.info(f"Loading LLM: provider=groq model={m} temp={t} role={role}")
        return ChatGroq(
            model=m,
            temperature=t,
            groq_api_key=settings.groq_api_key,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        m = model or settings.ollama_model
        logger.info(f"Loading LLM: provider=ollama model={m} temp={t} role={role}")
        return ChatOllama(
            base_url=settings.ollama_base_url,
            model=m,
            temperature=t,
        )

    raise ValueError(
        f"Unknown provider={provider!r}. Use 'gemini', 'groq', or 'ollama'."
    )
