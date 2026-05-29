"""
Shared LangGraph state definition.

AgentState flows through every node in the supervisor graph.
Using TypedDict + Annotated lets LangGraph merge message lists automatically.
"""
from __future__ import annotations

from typing import Annotated, Sequence, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # ── Message history (auto-merged by LangGraph) ────────────────────────────
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # ── Supervisor routing ────────────────────────────────────────────────────
    next_agent: str           # "researcher" | "coder" | "general" | "FINISH"
    reasoning: str            # supervisor's routing rationale
    last_specialist: str      # which specialist ran most recently (used by critic)
    supervisor_rounds: int    # incremented each supervisor call

    # ── Reflection (Reflexion pattern) ────────────────────────────────────────
    critique: str             # critic's textual feedback
    critique_score: float     # 0.0–1.0 quality score
    should_revise: bool       # critic decision: revise or pass through
    revision_count: int       # number of revisions for the current specialist turn

    # ── RAG context ───────────────────────────────────────────────────────────
    retrieved_context: str    # relevant memory chunks for the current query

    # ── Human-in-the-loop ─────────────────────────────────────────────────────
    hitl_required: bool       # whether HITL check is triggered before FINISH
