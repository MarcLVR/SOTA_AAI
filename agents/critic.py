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

import re

from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import SystemMessage
from loguru import logger

from .llm import get_llm
from .resilience import resilient_invoke
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
        # Threshold is the sole arbiter — ignore the LLM's boolean to prevent
        # unnecessary revisions when score >= threshold.
        score = info.data.get("score", 1.0)
        return score < REVISION_THRESHOLD


_SCORE_RE = re.compile(r'["\']?score["\']?\s*[:=]\s*([01](?:\.\d+)?|\.\d+)', re.IGNORECASE)


def _fallback_decision(llm, messages) -> CriticDecision:
    """Small-model fallback: many local models can't satisfy with_structured_output.

    Ask for a plain answer and scrape the score from the text. If even that fails
    we return an HONEST low-confidence pass-through — score at the threshold and
    no revision — rather than the misleading score=1.0 that masks critic failure.
    """
    try:
        raw = resilient_invoke(llm, messages, label="critic-fallback")
        text = raw.content if hasattr(raw, "content") else str(raw)
        m = _SCORE_RE.search(text)
        if m:
            score = max(0.0, min(1.0, float(m.group(1))))
            logger.info(f"[critic] fallback parsed score={score:.2f} from free text")
            return CriticDecision(score=score, critique=text[:300], should_revise=False)
    except Exception as e:
        logger.error(f"[critic] free-text fallback also failed: {e}")

    logger.warning("[critic] could not score response — passing through at threshold (no revision)")
    return CriticDecision(
        score=REVISION_THRESHOLD,
        critique="(critic unavailable — response not scored)",
        should_revise=False,
    )


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
        decision: CriticDecision = resilient_invoke(structured_llm, messages, label="critic")
        logger.info(
            f"[critic] score={decision.score:.2f} revise={decision.should_revise} | "
            f"{decision.critique[:80]}…"
        )
    except Exception as e:
        logger.error(f"[critic] structured output failed: {e}. Trying free-text fallback.")
        decision = _fallback_decision(llm, messages)

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
