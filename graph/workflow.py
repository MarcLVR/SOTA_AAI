"""
LangGraph supervisor workflow — full SOTA topology.

Graph topology:
  ┌────────────────────────────────────────────────────────────────┐
  │                                                                │
  │  [START] → guardrail_in → supervisor                          │
  │                               │                               │
  │              ┌────────────────┼────────────────┐              │
  │              ▼                ▼                ▼              │
  │          researcher        coder           general            │
  │              └────────────────┼────────────────┘              │
  │                               ▼                               │
  │                            critic                             │
  │                          ↙        ↘                           │
  │              (revise: back to      (pass: supervisor)         │
  │               specialist)                │                    │
  │                                         ▼                    │
  │                                  FINISH → hitl → [END]        │
  │                                                                │
  └────────────────────────────────────────────────────────────────┘

New in this version:
  - guardrail_in  : input validation before supervisor sees the message
  - critic        : Reflexion-style quality scoring after each specialist
  - hitl          : LangGraph interrupt() for human approval before END
  - observability : callbacks wired into every invocation
  - Mem0 tools    : remember + recall added to all agents
"""
from __future__ import annotations

from typing import Any
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt
from loguru import logger
import sqlite3

from graph.state import AgentState
from agents import (
    supervisor_node,
    build_researcher_agent,
    build_coder_agent,
    build_general_agent,
    build_auditor_agent,
)
from agents.critic import critic_node, route_after_critic
from config import settings
from guardrails import input_guard, output_guard
from memory import remember, recall


# ── Guardrail nodes ───────────────────────────────────────────────────────────

def guardrail_in_node(state: AgentState) -> dict:
    """Validate and sanitise the latest user message before routing."""
    last_human = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None
    )
    if last_human is None:
        return {}

    result = input_guard(last_human.content)
    if not result.passed:
        logger.warning(f"[guardrail_in] blocked: {result.reason}")
        # Inject a synthetic AI refusal so the graph can exit cleanly
        return {
            "messages": [AIMessage(content=f"⚠️ Request blocked: {result.reason}")],
            "next_agent": "FINISH",
        }

    # Warn but allow if PII was redacted
    if result.warnings:
        for w in result.warnings:
            logger.warning(f"[guardrail_in] {w}")

    return {}   # pass through unchanged


# ── Specialist agent node factory ─────────────────────────────────────────────

def _make_agent_node(agent, name: str):
    """Wrap a create_react_agent into a LangGraph node that also injects critique."""
    def node(state: AgentState) -> dict:
        messages = list(state["messages"])

        # Inject critique as a human follow-up on revision passes.
        # Must be HumanMessage — Claude rejects multiple non-consecutive SystemMessages.
        critique = state.get("critique", "")
        if critique and state.get("revision_count", 0) > 0:
            messages.append(
                HumanMessage(
                    content=f"[Revision requested] Your previous response scored "
                            f"{state.get('critique_score', 0):.0%}. "
                            f"Critique: {critique}\nPlease improve your response."
                )
            )

        logger.info(f"[{name}] invoked (revision={state.get('revision_count', 0)})")
        result = agent.invoke({"messages": messages})
        last_msg = result["messages"][-1]

        return {
            "messages": [last_msg],
            "last_specialist": name,
            # Signal that this specialist is done — hitl should proceed to END,
            # not loop back here.  The hitl conditional uses next_agent to decide:
            # "general" → re-enter general (only for HITL rejection feedback),
            # anything else → END.  Setting "FINISH" here prevents the approval
            # path from accidentally routing back to the specialist that just ran.
            "next_agent": "FINISH",
            # Reset revision flag so critic starts fresh
            "should_revise": False,
        }

    node.__name__ = name
    return node


# ── HITL node ─────────────────────────────────────────────────────────────────

def hitl_node(state: AgentState) -> dict:
    """
    Human-in-the-loop gate using LangGraph's interrupt().

    When hitl_required=True, execution pauses here and the graph is
    serialised to the SQLite checkpoint. The UI reads the interrupt value,
    shows the approval panel, and resumes the graph with the human's decision.

    Interrupt payload → UI:
      {"question": "...", "response": "...", "score": float}

    Resume payload ← UI:
      {"approved": bool, "feedback": str}
    """
    if not state.get("hitl_required", False):
        return {}

    # Find the last AI message to show for review
    last_ai = next(
        (m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None
    )
    response_text = last_ai.content if last_ai else "(no response)"

    human_decision = interrupt({
        "question": "Review the agent's response before it is sent to the user.",
        "response": response_text,
        "critique_score": state.get("critique_score", None),
    })

    approved = human_decision.get("approved", True)
    feedback = human_decision.get("feedback", "")

    if not approved and feedback:
        logger.info(f"[hitl] rejected — feedback: {feedback[:80]}")
        return {
            "messages": [HumanMessage(content=f"Please revise: {feedback}")],
            "next_agent": "general",
            "supervisor_rounds": 0,
            "revision_count": 0,
            "hitl_required": False,
        }

    logger.info("[hitl] approved")
    return {"hitl_required": False}


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph(mcp_tools: list | None = None, enable_hitl: bool = False) -> Any:
    """
    Build and compile the full supervisor LangGraph.

    Args:
        mcp_tools: Optional MCP-sourced LangChain tools injected into all agents.
        enable_hitl: If True, the graph pauses before END for human approval.

    Returns:
        Compiled LangGraph with SQLite persistence.
    """
    # Memory tools added to every agent
    memory_tools = [remember, recall]

    researcher = build_researcher_agent(extra_tools=(mcp_tools or []) + memory_tools)
    coder      = build_coder_agent(extra_tools=(mcp_tools or []) + memory_tools)
    general    = build_general_agent(extra_tools=(mcp_tools or []) + memory_tools)
    auditor    = build_auditor_agent(extra_tools=(mcp_tools or []) + memory_tools)

    researcher_node = _make_agent_node(researcher, "researcher")
    coder_node      = _make_agent_node(coder,      "coder")
    general_node    = _make_agent_node(general,    "general")
    auditor_node    = _make_agent_node(auditor,    "auditor")

    # ── Build graph ───────────────────────────────────────────────────────────
    builder = StateGraph(AgentState)

    builder.add_node("guardrail_in",  guardrail_in_node)
    builder.add_node("supervisor",    supervisor_node)
    builder.add_node("researcher",    researcher_node)
    builder.add_node("coder",         coder_node)
    builder.add_node("general",       general_node)
    builder.add_node("auditor",       auditor_node)
    builder.add_node("critic",        critic_node)
    builder.add_node("hitl",          hitl_node)

    # ── Edges ─────────────────────────────────────────────────────────────────

    # Entry
    builder.add_edge(START, "guardrail_in")

    # Guardrail → supervisor (or short-circuit to END if blocked)
    builder.add_conditional_edges(
        "guardrail_in",
        lambda s: "END" if s.get("next_agent") == "FINISH" else "supervisor",
        {"END": END, "supervisor": "supervisor"},
    )

    # Supervisor → specialists or finish
    builder.add_conditional_edges(
        "supervisor",
        lambda s: s["next_agent"],
        {
            "researcher": "researcher",
            "coder":      "coder",
            "general":    "general",
            "auditor":    "auditor",
            "FINISH":     "hitl",
        },
    )

    # Specialists → critic (when enabled) or directly to hitl (when disabled)
    if settings.critic_enabled:
        builder.add_edge("researcher", "critic")
        builder.add_edge("coder",      "critic")
        builder.add_edge("general",    "critic")
        builder.add_edge("auditor",    "critic")

        # Critic → back to specialist (revise) or hitl (pass)
        builder.add_conditional_edges(
            "critic",
            route_after_critic,
            {
                "researcher": "researcher",
                "coder":      "coder",
                "general":    "general",
                "auditor":    "auditor",
                "FINISH":     "hitl",
            },
        )
    else:
        # Critic disabled: specialists go straight to hitl, skipping Reflexion loop.
        # critic_node is still registered (the node object exists) but unreachable.
        builder.add_edge("researcher", "hitl")
        builder.add_edge("coder",      "hitl")
        builder.add_edge("general",    "hitl")
        builder.add_edge("auditor",    "hitl")

    # HITL → END (or back to general if rejected)
    builder.add_conditional_edges(
        "hitl",
        lambda s: "general" if s.get("next_agent") == "general" else "END",
        {"general": "general", "END": END},
    )

    # ── Persistence ───────────────────────────────────────────────────────────
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    db_path = str(settings.chroma_path / "checkpoints.sqlite")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    graph = builder.compile(checkpointer=checkpointer)
    logger.info(
        f"LangGraph compiled (hitl={enable_hitl}, critic={settings.critic_enabled})"
    )
    return graph


# ── Convenience helpers ───────────────────────────────────────────────────────

def run_query(
    graph,
    query: str,
    thread_id: str = "default",
    stream: bool = False,
    hitl_required: bool = False,
) -> str:
    """Send a user message to the graph and return the final AI response."""
    from observability import get_callbacks

    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": get_callbacks(),
    }
    initial_state = {
        "messages": [HumanMessage(content=query)],
        "next_agent": "",
        "reasoning": "",
        "last_specialist": "",
        "supervisor_rounds": 0,
        "critique": "",
        "critique_score": 0.0,
        "should_revise": False,
        "revision_count": 0,
        "retrieved_context": "",
        "hitl_required": hitl_required,
    }

    if stream:
        return graph.stream(initial_state, config=config, stream_mode="values")

    result = graph.invoke(initial_state, config=config)
    last_ai = next(
        (m for m in reversed(result["messages"]) if isinstance(m, AIMessage)),
        None,
    )
    return last_ai.content if last_ai else "No response generated."


def resume_after_hitl(graph, thread_id: str, approved: bool, feedback: str = "") -> str:
    """
    Resume a graph that was interrupted by the HITL node.

    Call this after the user approves or rejects in the UI.
    """
    from observability import get_callbacks

    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": get_callbacks(),
    }
    result = graph.invoke(
        {"approved": approved, "feedback": feedback},
        config=config,
    )
    last_ai = next(
        (m for m in reversed(result["messages"]) if isinstance(m, AIMessage)),
        None,
    )
    return last_ai.content if last_ai else "No response generated."
