"""
Critic agent — implements the Reflexion pattern.

After each specialist responds, the critic scores the response and decides
whether to send it back for revision.

Reference: Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement
Learning" (2023). https://arxiv.org/abs/2303.11366

Decision schema:
  {
    "score": 0.0–1.0,        # overall quality
    "critique": "...",        # specific, actionable feedback
    "should_revise": true/false
  }

The revision threshold is configurable (default 0.7).
Max revisions per turn is also capped (default 2) to prevent loops.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import SystemMessage
from loguru import logger

from .llm import get_llm
from config import settings
from graph.state import AgentState

REVISION_THRESHOLD = 0.70   # scores below this trigger a revision
MAX_REVISIONS = 2           # hard cap on revisions per specialist turn


CRITIC_PROMPT = """You are a rigorous quality-control critic for an AI agent system.

Evaluate the LAST assistant message in the conversation on these axes:
  1. Completeness  — does it fully answer the question?
  2. Accuracy      — are factual claims grounded in tool outputs or verifiable?
  3. Clarity       — is it easy to understand, appropriately structured?
  4. Tool use      — did the agent use tools when it should have?

Return a JSON object ONLY:
{
  "score": <float 0.0–1.0>,
  "critique": "<specific, actionable feedback — what is wrong and how to fix it>",
  "should_revise": <true if score < 0.70, else false>
}

Be strict. A good score (>0.85) requires: complete answer, grounded claims,
clear structure, and appropriate tool use."""


class CriticDecision(BaseModel):
    score: float = Field(ge=0.0, le=1.0, description="Quality score 0–1")
    critique: str = Field(description="Specific actionable feedback")
    should_revise: bool = Field(description="True if the response needs revision")

    @field_validator("should_revise", mode="before")
    @classmethod
    def enforce_threshold(cls, v, info):
        score = info.data.get("score", 1.0)
        return bool(v) or (score < REVISION_THRESHOLD)


def critic_node(state: AgentState) -> dict:
    """
    LangGraph node: evaluate the last AI response and decide whether to revise.
    Updates state with critique and revision_count.
    """
    revision_count = state.get("revision_count", 0)

    # Hard cap — don't loop forever
    if revision_count >= MAX_REVISIONS:
        logger.info(f"[critic] max revisions ({MAX_REVISIONS}) reached — passing through")
        return {"should_revise": False, "critique": "", "revision_count": revision_count}

    llm = get_llm(role="critic")
    structured_llm = llm.with_structured_output(CriticDecision)

    messages = [
        SystemMessage(content=CRITIC_PROMPT),
        *state["messages"],
    ]

    try:
        decision: CriticDecision = structured_llm.invoke(messages)
        logger.info(
            f"[critic] score={decision.score:.2f} revise={decision.should_revise} | "
            f"{decision.critique[:80]}…"
        )
    except Exception as e:
        logger.error(f"[critic] structured output failed: {e}. Skipping revision.")
        decision = CriticDecision(score=1.0, critique="", should_revise=False)

    return {
        "critique": decision.critique,
        "critique_score": decision.score,
        "should_revise": decision.should_revise,
        "revision_count": revision_count + (1 if decision.should_revise else 0),
    }


def route_after_critic(state: AgentState) -> str:
    """
    Conditional edge: after critic, go back to the last specialist or to supervisor.
    We track which specialist ran last via `next_agent` (set by supervisor before routing).
    """
    if state.get("should_revise", False):
        last_agent = state.get("last_specialist", "general")
        logger.info(f"[critic] routing back to {last_agent} for revision")
        return last_agent
    return "FINISH"
