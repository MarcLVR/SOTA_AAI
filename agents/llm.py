"""
LLM factory — returns a ChatOllama instance (or a remote model if configured).
Import get_llm() everywhere to keep the model in one place.
"""
from functools import lru_cache
from langchain_ollama import ChatOllama
from config import settings
from loguru import logger


@lru_cache(maxsize=4)
def get_llm(model: str | None = None, temperature: float | None = None):
    """
    Return a cached ChatOllama LLM.

    Args:
        model: Ollama model tag (e.g. 'llama3.2', 'qwen2.5', 'mistral').
               Defaults to settings.ollama_model.
        temperature: Sampling temperature. Defaults to settings.temperature.
    """
    m = model or settings.ollama_model
    t = temperature if temperature is not None else settings.temperature
    logger.info(f"Loading LLM: model={m} temperature={t}")
    return ChatOllama(
        base_url=settings.ollama_base_url,
        model=m,
        temperature=t,
        # streaming=True is handled at invocation level via .stream()
    )
