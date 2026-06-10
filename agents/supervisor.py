"""
Supervisor node — routes the conversation to the right specialist agent
or decides the task is done (FINISH).

Uses structured output (Pydantic) so the LLM returns a predictable JSON decision.
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from loguru import logger

from .llm import get_llm
from .resilience import resilient_invoke
from config import settings
from graph.state import AgentState
from guardrails.permissions import check_agent

MEMBERS = ["researcher", "coder", "general", "auditor"]
OPTIONS = MEMBERS + ["FINISH"]

SUPERVISOR_PROMPT = f"""You are a supervisor orchestrating a team of AI agents.
Team members: {', '.join(MEMBERS)}

Roles:
  researcher  — web search, document retrieval, fact-finding
  coder       — write and execute Python code, data analysis, scripting
  general     — reasoning, planning, Q&A, synthesis, anything else
  auditor     — document auditing, AI-readiness scanning, data hygiene, corpus quality checks

Given the conversation so far, decide:
  1. Which agent should act next?
  2. Or should we FINISH (the user's question has been fully answered)?

Reply ONLY with JSON matching the schema: {{"next": "<agent_or_FINISH>", "reasoning": "<1-sentence reason>"}}.
Do not call any tools yourself — just route."""


class SupervisorDecision(BaseModel):
    next: Literal["researcher", "coder", "general", "auditor", "FINISH"] = Field(
        description="Which agent acts next, or FINISH if the task is complete."
    )
    reasoning: str = Field(description="One-sentence rationale for routing decision.")


def supervisor_node(state: AgentState) -> dict:
    """LangGraph node: reads state, returns routing decision."""
    rounds = state.get("supervisor_rounds", 0)

    # Hard stop to prevent infinite loops
    if rounds >= settings.max_supervisor_rounds:
        logger.warning(f"Supervisor hit max rounds ({rounds}). Forcing FINISH.")
        return {"next_agent": "FINISH", "reasoning": "Max rounds reached.", "supervisor_rounds": rounds + 1}

    llm = get_llm()
    structured_llm = llm.with_structured_output(SupervisorDecision)

    messages = [
        SystemMessage(content=SUPERVISOR_PROMPT),
        *state["messages"],
    ]

    try:
        decision: SupervisorDecision = resilient_invoke(structured_llm, messages, label="supervisor")
        logger.info(f"[supervisor] round={rounds+1} → {decision.next} | {decision.reasoning}")
    except Exception as e:
        logger.error(f"[supervisor] structured output failed: {e}. Defaulting to general.")
        decision = SupervisorDecision(next="general", reasoning="Fallback due to parsing error.")

    # Permission check — downgrade to an allowed agent if role forbids the chosen one
    role = state.get("role", "admin")
    perm = check_agent(role, decision.next)
    if not perm.allowed:
        logger.warning(f"[supervisor] {perm.reason} — routing to 'general' instead.")
        decision = SupervisorDecision(next="general", reasoning=perm.reason)

    return {
        "next_agent": decision.next,
        "reasoning": decision.reasoning,
        "supervisor_rounds": rounds + 1,
    }
