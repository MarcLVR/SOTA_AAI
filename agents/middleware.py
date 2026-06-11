"""
Agent middleware factory.

`SummarizationMiddleware` (LangChain 1.x) makes context compaction declarative:
it fires on every model step inside a `create_agent` agent and, once the history
crosses a threshold, replaces the older turns with a single summary message while
keeping the most recent turns verbatim. This supersedes the hand-rolled
`graph.compaction.compact_messages` call for the *specialist* agents (it also
covers intra-turn tool loops, which the node-entry call did not).

The critic is a plain structured-output LLM call, not a `create_agent` agent, so
the middleware cannot attach to it — `graph/compaction.py` is retained for that
path (see `agents/critic.py`).

Local-Ollama constraint (verified, langchain 1.3.2): `("fraction", f)` triggers
require a model profile (`max_input_tokens`) that `ChatOllama` does not expose and
would raise at construction. We therefore use absolute `("messages", N)` counts,
which work on every provider including the local default.
"""
from __future__ import annotations

from langchain.agents.middleware import SummarizationMiddleware

from .llm import get_llm
from config import settings


def build_summarizer() -> SummarizationMiddleware:
    """Return a SummarizationMiddleware configured from settings.

    `trigger` mirrors the old `context_max_messages` threshold and `keep` mirrors
    `context_keep_last`, so behaviour stays equivalent for short threads (no-op
    until the threshold) and compacts long/resumed ones. The summariser uses the
    default chat model, so the fully-local Ollama path needs no API key.
    """
    return SummarizationMiddleware(
        model=get_llm(),
        trigger=("messages", settings.context_max_messages),
        keep=("messages", settings.context_keep_last),
    )
