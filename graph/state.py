"""
Shared LangGraph state definition.

AgentState flows through every node in the supervisor graph.
Using TypedDict + Annotated lets LangGraph merge message lists automatically.
"""
from __future__ import annotations

import operator
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

    # ── Plan-and-execute (opt-in, PLANNER_ENABLED) ────────────────────────────
    # Only populated when the planner subgraph is wired in. Additive — old
    # checkpoints without these keys still load (nodes read with .get()).
    plan: list                                      # remaining planned steps (dicts)
    step_results: Annotated[list, operator.add]     # results across groups (reducer: parallel-safe)
    plan_start: int                                 # offset where this run's results begin
    processed_count: int                            # absolute boundary of folded-in results
    replan_count: int                               # number of replanning rounds this run

    # ── Human-in-the-loop ─────────────────────────────────────────────────────
    hitl_required: bool       # whether HITL check is triggered before FINISH

    # ── Permissions ───────────────────────────────────────────────────────────
    role:     str   # active role for this session: viewer | analyst | admin
    identity: str   # caller identity for rate-limit tracking (api_key or "ui")
