"""
Resilient LLM invocation.

Every LLM call in the graph (supervisor routing, critic scoring, specialist
ReAct turns, executor fixes) is a network call to a provider that can rate-limit,
time out, or — for local Ollama — briefly stall while a model loads. A single
transient hiccup would otherwise abort the whole turn.

``resilient_invoke`` wraps any LangChain Runnable's ``.invoke`` with bounded
exponential-backoff retries (tenacity). It is provider-agnostic: it retries on
any exception, with attempt count and backoff cap driven by settings.
"""
from __future__ import annotations

from typing import Any
from loguru import logger
from tenacity import (
    Retrying,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import logging

from config import settings

# Bridge loguru <- tenacity's stdlib-logging before_sleep hook
_std_logger = logging.getLogger("llm.retry")


def resilient_invoke(runnable: Any, payload: Any, *, label: str = "LLM call",
                     config: dict | None = None) -> Any:
    """Invoke ``runnable`` with retries on transient failure.

    Args:
        runnable: any object with an ``.invoke(payload, **kw)`` method
                  (a chat model, a structured-output runnable, or a compiled agent).
        payload:  the input passed to ``.invoke``.
        label:    short name used in log lines (e.g. the agent/role name).
        config:   optional LangChain RunnableConfig (callbacks, thread, …).

    Returns:
        The runnable's result.

    Raises:
        The last exception if every attempt fails (``reraise=True``).
    """
    attempts = max(1, settings.llm_max_retries + 1)
    retryer = Retrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=1, max=settings.llm_retry_max_wait),
        reraise=True,
        before_sleep=before_sleep_log(_std_logger, logging.WARNING),
    )
    for attempt in retryer:
        with attempt:
            n = attempt.retry_state.attempt_number
            if n > 1:
                logger.warning(f"[{label}] retry attempt {n}/{attempts}")
            return runnable.invoke(payload, config=config) if config else runnable.invoke(payload)
