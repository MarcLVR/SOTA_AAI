"""
Plan-and-execute planner with parallel specialist fan-out (opt-in).

Enabled only when ``PLANNER_ENABLED=true``; otherwise none of this is wired into
the graph and the topology is identical to the supervisor-only default. The
subgraph sits between ``guardrail_in`` and the critic/hitl tail:

    planner → plan_dispatch ──Send──▶ fanout_worker (×N, parallel)
                  ▲                          │
                  │                          ▼
               replan ◀──────────────── plan_aggregate (fan-in, runs once)
                  │
            (complete) → critic/hitl

Design rules that keep parallelism safe under LangGraph 1.x (verified against the
installed langgraph 1.2.2):

* Fan-out is a conditional edge returning ``[Send("fanout_worker", arg), …]`` —
  the current map-reduce idiom, not deprecated.
* Parallel branches may only write **reducer-protected** state keys. ``step_results``
  carries an ``operator.add`` reducer; workers append to it and write nothing else.
  Writing a scalar (e.g. ``next_agent``) from parallel branches raises
  ``InvalidUpdateError`` — the aggregator (a single downstream node) owns scalars.
* The fan-in node runs exactly once after all branches complete. On an
  ``interrupt()`` mid-fan-out, only the interrupted branch re-runs on resume;
  completed branches are restored from the checkpoint (so specialists never
  double-execute). HITL therefore composes with the fan-out.

Plan grouping: each ``Step`` has an integer ``group``. Steps sharing a group are
independent and fan out in parallel; ascending groups run sequentially.
"""
from __future__ import annotations

import operator
from typing import Annotated, List, Literal

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, BaseMessage
from langgraph.types import Send
from loguru import logger

from agents.llm import get_llm
from agents.resilience import resilient_invoke
from config import settings

SPECIALISTS = ["researcher", "coder", "general", "auditor"]


# ── Structured-output schemas ──────────────────────────────────────────────────

class Step(BaseModel):
    description: str = Field(description="A single, self-contained sub-task.")
    specialist: Literal["researcher", "coder", "general", "auditor"] = Field(
        description="Which specialist should execute this step."
    )
    group: int = Field(
        default=0,
        description="Steps with the same group are independent and run in parallel; "
                    "lower groups run before higher ones. Use sequential groups only "
                    "when a step depends on an earlier step's result.",
    )


class Plan(BaseModel):
    steps: List[Step] = Field(description="Ordered, grouped steps that solve the request.")


class Act(BaseModel):
    """Replanner decision: finish with an answer, or revise the remaining plan."""
    is_complete: bool = Field(description="True if the request is fully answered.")
    response: str = Field(default="", description="Final answer to the user (when complete).")
    next_steps: List[Step] = Field(
        default_factory=list,
        description="Revised remaining steps (when NOT complete, e.g. after a failure).",
    )


PLANNER_PROMPT = f"""You are a planner. Break the user's request into the smallest
useful set of steps, each assigned to one specialist:
  researcher — web search, document retrieval, fact-finding
  coder      — write/execute Python, data analysis
  general    — reasoning, synthesis, Q&A, anything else
  auditor    — document/corpus auditing, AI-readiness, data hygiene

Assign each step a `group` integer. Steps that DON'T depend on each other should
share a group so they run in parallel. Only use a higher group number when a step
truly needs an earlier step's output. Prefer few steps; a simple request may be a
single step in group 0. Specialists: {', '.join(SPECIALISTS)}."""


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _last_human(messages: List[BaseMessage]) -> str:
    return next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )


def _current_group(plan: list[dict]) -> int | None:
    if not plan:
        return None
    return min(s["group"] for s in plan)


# ── Nodes (built with the live specialist agents) ────────────────────────────────

def make_planner_nodes(agents: dict):
    """Return the planner node set, bound to the already-built specialist agents.

    `agents` maps specialist name → compiled create_agent. The fan-out worker
    dispatches each step to the named agent.
    """

    def planner_node(state) -> dict:
        query = _last_human(state["messages"])
        llm = get_llm().with_structured_output(Plan)
        msgs = [SystemMessage(content=PLANNER_PROMPT), HumanMessage(content=query)]
        try:
            plan: Plan = resilient_invoke(llm, msgs, label="planner")
            steps = [s.model_dump() for s in plan.steps]
        except Exception as e:
            logger.error(f"[planner] failed ({e}); falling back to a single general step")
            steps = []
        if not steps:
            steps = [{"description": query, "specialist": "general", "group": 0}]
        logger.info(f"[planner] {len(steps)} step(s), groups={sorted({s['group'] for s in steps})}")
        # plan_start marks where this run's results begin in the append-only
        # step_results list, so multi-query threads don't re-read stale results.
        start = len(state.get("step_results", []))
        return {
            "plan": steps,
            "plan_start": start,
            "processed_count": start,
            "replan_count": 0,
        }

    def plan_dispatch_node(state) -> dict:
        # Passthrough; the conditional edge below performs the Send fan-out.
        grp = _current_group(state.get("plan", []))
        if grp is not None:
            n = sum(1 for s in state["plan"] if s["group"] == grp)
            logger.info(f"[plan_dispatch] fanning out group {grp} → {n} parallel step(s)")
        return {}

    def fanout_worker(state) -> dict:
        # `state` here is the Send arg, not the full AgentState.
        step = state["step"]
        spec = step.get("specialist", "general")
        agent = agents.get(spec) or agents["general"]
        prompt = (
            f"{state['query']}\n\n"
            f"Focus only on this sub-task and answer it directly: {step['description']}"
        )
        try:
            result = resilient_invoke(
                agent, {"messages": [HumanMessage(content=prompt)]}, label=f"plan:{spec}"
            )
            text = result["messages"][-1].content
            ok = True
        except Exception as e:
            logger.error(f"[fanout_worker:{spec}] failed: {e}")
            text, ok = f"ERROR: {e}", False
        return {"step_results": [{
            "step": step["description"], "specialist": spec, "result": text, "ok": ok,
        }]}

    def plan_aggregate_node(state) -> dict:
        # Fan-in: runs once after all branches. Advance past the group that ran
        # and fold its results in. `processed_count` is the absolute boundary in
        # the append-only step_results list (only this node writes it).
        plan = state.get("plan", [])
        grp = _current_group(plan)
        remaining = [s for s in plan if s["group"] != grp] if grp is not None else []
        all_results = state.get("step_results", [])
        processed = state.get("processed_count", state.get("plan_start", 0))
        new = all_results[processed:]
        ok = sum(1 for r in new if r["ok"])
        logger.info(f"[plan_aggregate] group done: {ok}/{len(new)} ok; {len(remaining)} step(s) left")
        return {"plan": remaining, "processed_count": processed + len(new)}

    def replan_node(state) -> dict:
        plan = state.get("plan", [])
        start = state.get("plan_start", 0)
        run_results = state.get("step_results", [])[start:]
        failed = [r for r in run_results if not r["ok"]]
        replan_count = state.get("replan_count", 0)

        # More groups queued and nothing to fix → loop back to dispatch (no LLM call).
        if plan and not failed:
            return {}

        # Failure(s) with replan budget left → ask the LLM to revise the plan.
        if failed and replan_count < settings.planner_max_replans:
            decision = _replan_llm(state, run_results)
            if not decision.is_complete and decision.next_steps:
                steps = [s.model_dump() for s in decision.next_steps]
                logger.info(f"[replan] revising plan → {len(steps)} step(s) (attempt {replan_count+1})")
                return {"plan": steps, "replan_count": replan_count + 1}
            # LLM chose to finish despite failure, or gave no steps.
            return _finish(decision.response or _join(run_results), state)

        # Plan exhausted (or out of replan budget) → synthesize the final answer.
        if plan:  # budget exhausted but steps remain → stop cleanly
            logger.warning("[replan] replan budget exhausted; finishing with partial results")
        decision = _replan_llm(state, run_results)
        answer = decision.response if decision.is_complete and decision.response else _join(run_results)
        return _finish(answer, state)

    return {
        "planner": planner_node,
        "plan_dispatch": plan_dispatch_node,
        "fanout_worker": fanout_worker,
        "plan_aggregate": plan_aggregate_node,
        "replan": replan_node,
    }


def _replan_llm(state, run_results: list[dict]) -> Act:
    query = _last_human(state["messages"])
    transcript = _join(run_results)
    llm = get_llm().with_structured_output(Act)
    msgs = [
        SystemMessage(content=(
            "You are a replanner. Given the original request and the results of the "
            "steps executed so far, decide: is the request fully answered? If yes, set "
            "is_complete=true and write the final answer in `response`. If a step failed "
            "or more work is needed, set is_complete=false and provide revised `next_steps`."
        )),
        HumanMessage(content=f"Original request:\n{query}\n\nResults so far:\n{transcript}"),
    ]
    try:
        return resilient_invoke(llm, msgs, label="replan")
    except Exception as e:
        logger.error(f"[replan] structured output failed ({e}); finishing with joined results")
        return Act(is_complete=True, response=_join(run_results))


def _join(results: list[dict]) -> str:
    if not results:
        return "No results were produced."
    parts = []
    for r in results:
        tag = "" if r["ok"] else " (failed)"
        parts.append(f"### {r['specialist']}{tag}: {r['step']}\n{r['result']}")
    return "\n\n".join(parts)


def _finish(answer: str, state) -> dict:
    return {
        "messages": [AIMessage(content=answer)],
        "plan": [],
        "last_specialist": "general",   # gives route_after_critic a revision target
        "next_agent": "FINISH",
        "should_revise": False,
    }


# ── Routing edge functions ────────────────────────────────────────────────────

def fan_out(state) -> list[Send]:
    """Conditional edge: emit one Send per step in the current (lowest) group."""
    plan = state.get("plan", [])
    grp = _current_group(plan)
    if grp is None:
        # No steps — shouldn't happen after planner, but route a no-op to aggregate.
        return [Send("plan_aggregate", {})]
    query = _last_human(state["messages"])
    return [
        Send("fanout_worker", {"step": s, "query": query})
        for s in plan if s["group"] == grp
    ]


def route_after_replan(state) -> str:
    """Loop back to dispatch while steps remain, else proceed to the critic/hitl tail."""
    return "plan_dispatch" if state.get("plan") else "DONE"
