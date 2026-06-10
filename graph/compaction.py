"""
Context compaction for long, checkpointed conversations.

Threads resume by ``thread_id`` from the SQLite checkpointer, so ``messages``
grows without bound across turns. Left unchecked it eventually overflows the
model's context window. ``compact_messages`` keeps a bounded, *valid* recent
window before each specialist/critic call.

Validity matters: naively slicing a message list can orphan a ``ToolMessage``
from the ``AIMessage`` that requested it, which providers reject. We delegate
that boundary logic to LangChain's ``trim_messages`` (``start_on="human"``),
which never starts the window on an orphaned tool/AI message.

When ``CONTEXT_SUMMARIZE=true`` the dropped older messages are condensed into a
single note (one cheap LLM call) so the agent keeps the gist of earlier turns;
otherwise a short placeholder marks the elision. The note is a ``HumanMessage``
on purpose — Claude rejects multiple non-consecutive ``SystemMessage``s, and the
ReAct agents already inject their own system prompt.
"""
from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, trim_messages
from loguru import logger

from config import settings


def _summarize(dropped: list[BaseMessage]) -> str:
    """One-paragraph summary of the messages being elided (best-effort)."""
    from agents.llm import get_llm
    from agents.resilience import resilient_invoke

    transcript = "\n".join(
        f"{m.__class__.__name__}: {getattr(m, 'content', '')}"[:500] for m in dropped
    )
    prompt = (
        "Summarise the earlier conversation below in 3-4 sentences, preserving "
        "facts, decisions, and open questions an assistant would need to continue:\n\n"
        f"{transcript}"
    )
    try:
        resp = resilient_invoke(get_llm(role="general"), prompt, label="compaction-summary")
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception as e:  # never let compaction break a turn
        logger.warning(f"[compaction] summary failed: {e}")
        return f"{len(dropped)} earlier messages omitted."


def compact_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Return a bounded, provider-valid recent window of ``messages``.

    No-op when the thread is short. Otherwise keep the last
    ``CONTEXT_KEEP_LAST`` messages (trimmed to a valid boundary) and prepend a
    summary/placeholder note describing what was dropped.
    """
    messages = list(messages)
    if len(messages) <= settings.context_max_messages:
        return messages

    kept = trim_messages(
        messages,
        max_tokens=settings.context_keep_last,
        token_counter=len,            # count messages, not tokens — provider-agnostic
        strategy="last",
        start_on="human",             # never begin on an orphaned tool/AI message
        include_system=False,
        allow_partial=False,
    )

    dropped = messages[: len(messages) - len(kept)]
    if not dropped:
        return kept

    note = _summarize(dropped) if settings.context_summarize else (
        f"[{len(dropped)} earlier messages omitted to stay within the context window]"
    )
    logger.info(f"[compaction] {len(messages)} → {len(kept) + 1} messages (dropped {len(dropped)})")
    return [HumanMessage(content=f"[Earlier context]\n{note}"), *kept]
