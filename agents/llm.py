"""
LLM factory — returns a chat model based on settings.

Supported providers (LLM_PROVIDER):
    anthropic -> ChatAnthropic  (default; ANTHROPIC_API_KEY required)
    groq      -> ChatGroq       (fast/free; GROQ_API_KEY required)
    ollama    -> ChatOllama     (local; Ollama service required)

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


# ── Per-role output-token caps ────────────────────────────────────────────────
# ChatAnthropic's default is 64 000 (the model maximum) — absurd for our use
# cases. These caps prevent runaway output costs with no quality loss.
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


def _max_tokens_for_role(role: str | None) -> int:
    """Return the output-token cap for a role, with env-var override support."""
    if role:
        env_val = os.environ.get(f"ROLE_MAX_TOKENS_{role.upper()}")
        if env_val:
            return int(env_val)
        return _ROLE_MAX_TOKENS.get(role, _DEFAULT_MAX_TOKENS)
    return _DEFAULT_MAX_TOKENS


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
    t        = temperature if temperature is not None else settings.temperature
    provider = _provider_for_role(role)
    max_tok  = _max_tokens_for_role(role)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if not settings.anthropic_api_key:
            raise RuntimeError(
                "Provider=anthropic but ANTHROPIC_API_KEY is not set in .env."
            )
        m = model or _model_for_role(role) or settings.anthropic_model
        logger.info(
            f"Loading LLM: provider=anthropic model={m} "
            f"max_tokens={max_tok} temp={t} role={role}"
        )
        return ChatAnthropic(
            model=m,
            temperature=t,
            max_tokens=max_tok,
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
        logger.info(
            f"Loading LLM: provider=groq model={m} "
            f"max_tokens={max_tok} temp={t} role={role}"
        )
        return ChatGroq(
            model=m,
            temperature=t,
            max_tokens=max_tok,
            groq_api_key=settings.groq_api_key,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        m = model or _model_for_role(role) or settings.ollama_model
        logger.info(
            f"Loading LLM: provider=ollama model={m} "
            f"max_tokens={max_tok} temp={t} role={role}"
        )
        return ChatOllama(
            base_url=settings.ollama_base_url,
            model=m,
            temperature=t,
            num_predict=max_tok,   # Ollama uses num_predict instead of max_tokens
        )

    raise ValueError(
        f"Unknown provider={provider!r}. Valid: 'anthropic', 'groq', 'ollama'."
    )
