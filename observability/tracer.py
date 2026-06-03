"""
Observability layer — tracing callbacks for LangChain / LangGraph.

Priority order:
  1. Langfuse  — if LANGFUSE_SECRET_KEY is set (self-hosted or cloud)
  2. LangSmith — if LANGCHAIN_API_KEY is set
  3. Console   — always available; logs key events to stdout via loguru

Usage:
    from observability import get_callbacks
    graph.invoke(state, config={"callbacks": get_callbacks(), ...})

Langfuse self-host (Docker, free):
    docker run -d --name langfuse \\
      -e DATABASE_URL=postgresql://... \\
      -p 3000:3000 langfuse/langfuse
"""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from loguru import logger

from config import settings


# ── Console callback (always on) ──────────────────────────────────────────────

class ConsoleTracerCallback(BaseCallbackHandler):
    """Lightweight callback that logs key events to the console via loguru."""

    def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs: Any) -> None:
        model = serialized.get("kwargs", {}).get("model", "?")
        total_chars = sum(len(p) for p in prompts)
        logger.info(f"[LLM] ▶ {model} | ~{total_chars // 4:,} tokens estimated ({total_chars:,} chars)")
        self._t0 = time.perf_counter()

    def on_chat_model_start(
        self, serialized: dict, messages: list, **kwargs: Any
    ) -> None:
        """Called for chat models (ChatAnthropic, ChatGroq, etc.)."""
        model = (
            serialized.get("kwargs", {}).get("model")
            or serialized.get("kwargs", {}).get("model_name")
            or serialized.get("name", "?")
        )
        total_chars = sum(
            len(m.content) if hasattr(m, "content") else len(str(m))
            for batch in messages
            for m in batch
        )
        logger.info(
            f"[LLM] ▶ {model} | ~{total_chars // 4:,} input tokens estimated "
            f"({total_chars:,} chars across {sum(len(b) for b in messages)} messages)"
        )
        self._t0 = time.perf_counter()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        elapsed = time.perf_counter() - getattr(self, "_t0", time.perf_counter())
        out = response.llm_output or {}

        # Anthropic: {"usage": {"input_tokens": N, "output_tokens": N}}
        # Groq/OpenAI: {"token_usage": {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}}
        usage = out.get("usage") or out.get("token_usage") or {}
        input_tok  = usage.get("input_tokens")  or usage.get("prompt_tokens",     0)
        output_tok = usage.get("output_tokens") or usage.get("completion_tokens", 0)
        total_tok  = usage.get("total_tokens")  or ((input_tok or 0) + (output_tok or 0)) or "?"

        logger.info(
            f"[LLM] ✓ {elapsed:.2f}s | tokens={total_tok} "
            f"(in={input_tok or '?'} out={output_tok or '?'})"
        )

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        name = serialized.get("name", "?")
        logger.info(f"[tool] ▶ {name}({input_str[:80]}…)")

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        logger.info(f"[tool] ✓ {str(output)[:120]}…")

    def on_tool_error(self, error: Exception | str, **kwargs: Any) -> None:
        logger.error(f"[tool] ✗ {error}")

    def on_chain_start(self, serialized: dict, inputs: dict, **kwargs: Any) -> None:
        name = (serialized or {}).get("id", ["?"])[-1]
        logger.debug(f"[chain] ▶ {name}")

    def on_chain_end(self, outputs: dict, **kwargs: Any) -> None:
        logger.debug("[chain] ✓")

    def on_agent_action(self, action: Any, **kwargs: Any) -> None:
        logger.info(f"[agent] → {action.tool}({str(action.tool_input)[:80]})")

    def on_agent_finish(self, finish: Any, **kwargs: Any) -> None:
        logger.info(f"[agent] ✓ finished")


# ── Langfuse callback (optional) ──────────────────────────────────────────────

def _try_langfuse():
    """Return a Langfuse CallbackHandler or None if not configured."""
    try:
        from langfuse.langchain import CallbackHandler as LangfuseHandler  # type: ignore
        secret = getattr(settings, "langfuse_secret_key", None)
        public = getattr(settings, "langfuse_public_key", None)
        host   = getattr(settings, "LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
        if not (secret and public):
            return None
        handler = LangfuseHandler()
        logger.info(f"Langfuse tracing enabled → {host}")
        return handler
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"Langfuse init failed: {e}")
        return None


# ── LangSmith callback (optional) ─────────────────────────────────────────────

def _try_langsmith():
    """Return a LangSmith tracer or None if not configured."""
    try:
        import os
        api_key = os.getenv("LANGCHAIN_API_KEY") or getattr(settings, "langchain_api_key", None)
        if not api_key:
            return None
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", "agentic-ai")
        # LangSmith tracing is automatic when env vars are set — no explicit handler needed
        logger.info("LangSmith tracing enabled (LANGCHAIN_TRACING_V2=true)")
        return None  # LangSmith hooks in automatically via env
    except Exception as e:
        logger.warning(f"LangSmith setup failed: {e}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

_console_tracer = ConsoleTracerCallback()


def get_callbacks() -> list:
    """
    Return the list of active callback handlers.
    Always includes the console tracer; adds Langfuse/LangSmith if configured.
    """
    callbacks = [_console_tracer]

    langfuse = _try_langfuse()
    if langfuse:
        callbacks.append(langfuse)

    _try_langsmith()  # activates via env vars, no handler object needed

    return callbacks
