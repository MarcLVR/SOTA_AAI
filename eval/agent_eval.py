"""
Agent eval harness — tests routing, end-to-end answers, and agent trajectory.

Three test modes:
  1. Routing eval     : given a query, does the supervisor route to the expected agent?
  2. E2E eval         : run a query through the full graph and score the response.
  3. Trajectory eval  : run the full graph and assert on the *process*, not just the
                        answer — which nodes executed and which tools were called.
                        (Answer-quality metrics alone can't catch an agent that
                        guesses correctly without using the tool it should have.)

Usage:
    python -m eval.agent_eval                  # runs all tests
    python -m eval.agent_eval --routing        # routing tests only
    python -m eval.agent_eval --e2e            # e2e tests only
    python -m eval.agent_eval --trajectory     # trajectory + tool-call tests only

Output: eval/results/agent_eval.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from loguru import logger

# ── Test case definitions ─────────────────────────────────────────────────────

@dataclass
class RoutingTestCase:
    query: str
    expected_agent: str       # "researcher" | "coder" | "general"
    description: str = ""


@dataclass
class E2ETestCase:
    query: str
    expected_keywords: list[str]   # response must contain ALL of these
    forbidden_keywords: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class TrajectoryTestCase:
    query: str
    expected_nodes: list[str]      # graph nodes that MUST appear (in order, as a subsequence)
    expected_tools: list[str] = field(default_factory=list)   # tools that MUST be called
    forbidden_tools: list[str] = field(default_factory=list)  # tools that must NOT be called
    description: str = ""


@dataclass
class PlanTestCase:
    query: str
    expected_specialists: list[str]   # specialists the plan should dispatch to (coverage)
    min_steps: int = 1                # plan must have at least this many steps
    expect_parallel: bool = False     # at least one group should fan out >1 step in parallel
    description: str = ""


ROUTING_TESTS: list[RoutingTestCase] = [
    RoutingTestCase(
        query="Search the web for the latest news on open source LLMs",
        expected_agent="researcher",
        description="Explicit web search → researcher",
    ),
    RoutingTestCase(
        query="Write a Python function to compute Gini coefficient",
        expected_agent="coder",
        description="Explicit coding task → coder",
    ),
    RoutingTestCase(
        query="What are the pros and cons of LangGraph vs CrewAI?",
        expected_agent="general",
        description="Comparison / reasoning → general",
    ),
    RoutingTestCase(
        query="Find the documentation for LangChain tool use",
        expected_agent="researcher",
        description="Documentation lookup → researcher",
    ),
    RoutingTestCase(
        query="Execute this code and tell me the output: print(sum(range(100)))",
        expected_agent="coder",
        description="Code execution → coder",
    ),
    RoutingTestCase(
        query="Explain the Reflexion paper in simple terms",
        expected_agent="general",
        description="Explanation / synthesis → general",
    ),
]


E2E_TESTS: list[E2ETestCase] = [
    E2ETestCase(
        query="What is 2 + 2?",
        expected_keywords=["4"],
        description="Basic arithmetic",
    ),
    E2ETestCase(
        query="Write Python code to print 'hello world'",
        expected_keywords=["print", "hello world"],
        description="Simple code generation",
    ),
    E2ETestCase(
        query="List the files in the uploads directory",
        expected_keywords=[],   # just check it doesn't error
        description="File listing tool call",
    ),
]


# ── Routing eval ──────────────────────────────────────────────────────────────

def run_routing_eval() -> dict:
    """Test supervisor routing accuracy without running full specialist agents."""
    from agents.supervisor import supervisor_node
    from langchain_core.messages import HumanMessage

    results = []
    correct = 0

    for tc in ROUTING_TESTS:
        state = {
            "messages": [HumanMessage(content=tc.query)],
            "next_agent": "",
            "reasoning": "",
            "retrieved_context": "",
            "supervisor_rounds": 0,
            "revision_count": 0,
            "critique": "",
            "critique_score": 0.0,
            "should_revise": False,
            "hitl_required": False,
            "last_specialist": "",
        }
        try:
            out = supervisor_node(state)
            actual = out.get("next_agent", "?")
            passed = actual == tc.expected_agent
            if passed:
                correct += 1
            logger.info(
                f"[routing_eval] {'✓' if passed else '✗'} "
                f"expected={tc.expected_agent} actual={actual} | {tc.description}"
            )
            results.append({
                "query": tc.query,
                "expected": tc.expected_agent,
                "actual": actual,
                "passed": passed,
                "reasoning": out.get("reasoning", ""),
                "description": tc.description,
            })
        except Exception as e:
            logger.error(f"[routing_eval] error on '{tc.query[:40]}…': {e}")
            results.append({
                "query": tc.query,
                "expected": tc.expected_agent,
                "actual": "ERROR",
                "passed": False,
                "error": str(e),
                "description": tc.description,
            })

    accuracy = correct / len(ROUTING_TESTS) if ROUTING_TESTS else 0.0
    logger.info(f"[routing_eval] accuracy = {accuracy:.0%} ({correct}/{len(ROUTING_TESTS)})")

    return {
        "accuracy": accuracy,
        "correct": correct,
        "total": len(ROUTING_TESTS),
        "cases": results,
    }


# ── E2E eval ──────────────────────────────────────────────────────────────────

def run_e2e_eval() -> dict:
    """Run full graph queries and check response keywords."""
    from graph.workflow import build_graph, run_query

    graph = build_graph()
    results = []
    correct = 0

    for tc in E2E_TESTS:
        try:
            response = run_query(graph, tc.query, thread_id=f"eval-{hash(tc.query)}")
            lower = response.lower()

            kw_pass = all(kw.lower() in lower for kw in tc.expected_keywords)
            kw_fail = any(kw.lower() in lower for kw in tc.forbidden_keywords)
            passed = kw_pass and not kw_fail

            if passed:
                correct += 1

            logger.info(
                f"[e2e_eval] {'✓' if passed else '✗'} {tc.description}"
            )
            results.append({
                "query": tc.query,
                "passed": passed,
                "response_preview": response[:200],
                "expected_keywords": tc.expected_keywords,
                "description": tc.description,
            })
        except Exception as e:
            logger.error(f"[e2e_eval] error: {e}")
            results.append({
                "query": tc.query,
                "passed": False,
                "error": str(e),
                "description": tc.description,
            })

    accuracy = correct / len(E2E_TESTS) if E2E_TESTS else 0.0
    logger.info(f"[e2e_eval] accuracy = {accuracy:.0%} ({correct}/{len(E2E_TESTS)})")

    return {
        "accuracy": accuracy,
        "correct": correct,
        "total": len(E2E_TESTS),
        "cases": results,
    }


# ── Trajectory + tool-call eval ─────────────────────────────────────────────────

TRAJECTORY_TESTS: list[TrajectoryTestCase] = [
    TrajectoryTestCase(
        query="Execute this code and tell me the output: print(sum(range(100)))",
        expected_nodes=["supervisor", "coder"],
        expected_tools=["python_repl"],
        description="Code execution must route to coder AND actually run python_repl",
    ),
    TrajectoryTestCase(
        query="Search the web for the current stable version of Python",
        expected_nodes=["supervisor", "researcher"],
        expected_tools=["web_search"],
        description="Web lookup must route to researcher AND call web_search",
    ),
    TrajectoryTestCase(
        query="List the files in the uploads directory",
        expected_nodes=["supervisor"],
        expected_tools=["list_files"],
        description="File listing must invoke the list_files tool",
    ),
]


def _is_subsequence(needles: list[str], haystack: list[str]) -> bool:
    """True if `needles` appears in `haystack` in order (gaps allowed)."""
    it = iter(haystack)
    return all(n in it for n in needles)


def _capture_run(graph, query: str, thread_id: str) -> tuple[list[str], set[str], list]:
    """Stream the graph and capture (node trajectory, tools called, message list).

    Tool calls happen inside the ReAct specialist subgraphs and are dropped from
    top-level state (the node persists only the final message). subgraphs=True
    surfaces those inner steps so we see the actual tool invocations. The ordered
    message list is fed to agentevals for trajectory matching.
    """
    from langchain_core.messages import HumanMessage, ToolMessage
    from observability import get_callbacks

    initial_state = {
        "messages": [HumanMessage(content=query)],
        "next_agent": "", "reasoning": "", "last_specialist": "",
        "supervisor_rounds": 0, "critique": "", "critique_score": 0.0,
        "should_revise": False, "revision_count": 0, "retrieved_context": "",
        "hitl_required": False,
    }
    config = {"configurable": {"thread_id": thread_id}, "callbacks": get_callbacks()}

    trajectory: list[str] = []
    tools_called: set[str] = set()
    messages: list = [HumanMessage(content=query)]

    for ns, update in graph.stream(
        initial_state, config=config, stream_mode="updates", subgraphs=True
    ):
        is_top = len(ns) == 0
        if not isinstance(update, dict):
            continue
        for node, delta in update.items():
            if is_top:
                trajectory.append(node)
            if not isinstance(delta, dict):
                continue
            msgs = delta.get("messages", [])
            if not isinstance(msgs, list):
                msgs = [msgs]
            for m in msgs:
                messages.append(m)
                for tc in (getattr(m, "tool_calls", None) or []):
                    name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                    if name:
                        tools_called.add(name)
                if isinstance(m, ToolMessage) and getattr(m, "name", None):
                    tools_called.add(m.name)

    return trajectory, tools_called, messages


def _tool_call_score(messages: list, expected_tools: list[str]) -> bool:
    """Use agentevals' trajectory subset-match to verify expected tools were called.

    subset mode = the reference tool-calls must all appear in the run (order/extras
    ignored); tool_args_match_mode='ignore' checks the call happened, not its args.
    """
    if not expected_tools:
        return True
    from agentevals.trajectory.match import create_trajectory_match_evaluator
    from langchain_core.messages import AIMessage

    evaluator = create_trajectory_match_evaluator(
        trajectory_match_mode="subset", tool_args_match_mode="ignore"
    )
    reference = [
        AIMessage(content="", tool_calls=[{"name": t, "args": {}, "id": t}])
        for t in expected_tools
    ]
    result = evaluator(outputs=messages, reference_outputs=reference)
    return bool(result.get("score"))


def run_trajectory_eval() -> dict:
    """Assert on the agent's process: node trajectory and tool-call correctness."""
    from graph.workflow import build_graph

    graph = build_graph()
    results = []
    correct = 0

    for tc in TRAJECTORY_TESTS:
        try:
            trajectory, tools, messages = _capture_run(
                graph, tc.query, thread_id=f"traj-{hash(tc.query)}"
            )

            nodes_ok = _is_subsequence(tc.expected_nodes, trajectory)
            tools_ok = _tool_call_score(messages, tc.expected_tools)   # agentevals subset match
            no_forbidden = not any(t in tools for t in tc.forbidden_tools)
            passed = nodes_ok and tools_ok and no_forbidden
            if passed:
                correct += 1

            logger.info(
                f"[trajectory_eval] {'✓' if passed else '✗'} {tc.description} | "
                f"nodes={trajectory} tools={sorted(tools)}"
            )
            results.append({
                "query": tc.query,
                "passed": passed,
                "trajectory": trajectory,
                "tools_called": sorted(tools),
                "expected_nodes": tc.expected_nodes,
                "expected_tools": tc.expected_tools,
                "nodes_ok": nodes_ok,
                "tools_ok": tools_ok,
                "no_forbidden": no_forbidden,
                "description": tc.description,
            })
        except Exception as e:
            logger.error(f"[trajectory_eval] error: {e}")
            results.append({
                "query": tc.query, "passed": False, "error": str(e),
                "description": tc.description,
            })

    accuracy = correct / len(TRAJECTORY_TESTS) if TRAJECTORY_TESTS else 0.0
    logger.info(f"[trajectory_eval] accuracy = {accuracy:.0%} ({correct}/{len(TRAJECTORY_TESTS)})")

    return {
        "accuracy": accuracy,
        "correct": correct,
        "total": len(TRAJECTORY_TESTS),
        "cases": results,
    }


# ── Planner eval (plan quality, step adherence, fan-in correctness) ─────────────

PLAN_TESTS: list[PlanTestCase] = [
    PlanTestCase(
        query="Do two things: (1) compute 17*23 in Python, and (2) explain in one "
              "sentence what a Gini coefficient is.",
        expected_specialists=["coder", "general"],
        min_steps=2,
        expect_parallel=True,
        description="Two independent sub-tasks → parallel coder + general, fan-in merges both",
    ),
    PlanTestCase(
        query="What is the capital of France?",
        expected_specialists=["general"],
        min_steps=1,
        expect_parallel=False,
        description="Trivial single-step plan → one general step",
    ),
]


def _capture_planner_run(graph, query: str, thread_id: str):
    """Stream the planner graph; return (top-level node trajectory, final state)."""
    from langchain_core.messages import HumanMessage
    from observability import get_callbacks

    initial_state = {
        "messages": [HumanMessage(content=query)],
        "next_agent": "", "reasoning": "", "last_specialist": "",
        "supervisor_rounds": 0, "critique": "", "critique_score": 0.0,
        "should_revise": False, "revision_count": 0, "retrieved_context": "",
        "plan": [], "step_results": [], "plan_start": 0, "processed_count": 0,
        "replan_count": 0, "hitl_required": False,
    }
    config = {"configurable": {"thread_id": thread_id}, "callbacks": get_callbacks()}
    trajectory: list[str] = []
    for ns, update in graph.stream(
        initial_state, config=config, stream_mode="updates", subgraphs=True
    ):
        if len(ns) != 0 or not isinstance(update, dict):
            continue
        trajectory.extend(update.keys())
    final = graph.get_state(config).values
    return trajectory, final


def run_planner_eval() -> dict:
    """Assert on the planner: plan quality, step adherence, and fan-in correctness.

    Forces PLANNER_ENABLED on for the duration regardless of the ambient setting so
    the eval is deterministic about which topology it exercises.
    """
    from langchain_core.messages import AIMessage
    from config import settings
    from graph.workflow import build_graph

    prev = settings.planner_enabled
    settings.planner_enabled = True
    results, correct = [], 0
    try:
        graph = build_graph()
        for tc in PLAN_TESTS:
            try:
                trajectory, final = _capture_planner_run(
                    graph, tc.query, thread_id=f"plan-{hash(tc.query)}"
                )
                step_results = final.get("step_results", [])
                used = [r["specialist"] for r in step_results]

                # Plan quality: expected specialists are covered by the executed steps.
                plan_quality = all(s in used for s in tc.expected_specialists) and \
                    len(step_results) >= tc.min_steps
                # Step adherence: every planned step ran a worker (one result each) and
                # the planner→dispatch→worker→aggregate→replan path appears.
                worker_runs = trajectory.count("fanout_worker")
                step_adherence = (
                    worker_runs == len(step_results) and len(step_results) >= 1
                    and _is_subsequence(
                        ["planner", "plan_dispatch", "fanout_worker", "plan_aggregate", "replan"],
                        trajectory,
                    )
                )
                # Fan-in correctness: aggregate ran, every step produced a result with
                # no unmerged leftovers, the plan drained, and a final answer exists.
                agg_runs = trajectory.count("plan_aggregate")
                last_ai = next((m for m in reversed(final.get("messages", []))
                                if isinstance(m, AIMessage)), None)
                fanin_ok = (
                    agg_runs >= 1
                    and all(r.get("ok") for r in step_results)
                    and not final.get("plan")
                    and last_ai is not None and bool(last_ai.content)
                )
                # Parallelism expectation (a group fanned out >1 worker in one superstep).
                parallel_ok = (not tc.expect_parallel) or (len(step_results) >= 2)

                passed = plan_quality and step_adherence and fanin_ok and parallel_ok
                if passed:
                    correct += 1
                logger.info(
                    f"[planner_eval] {'✓' if passed else '✗'} {tc.description} | "
                    f"steps={len(step_results)} used={used} workers={worker_runs} agg={agg_runs}"
                )
                results.append({
                    "query": tc.query, "passed": passed,
                    "plan_quality": plan_quality, "step_adherence": step_adherence,
                    "fanin_ok": fanin_ok, "parallel_ok": parallel_ok,
                    "specialists_used": used, "worker_runs": worker_runs,
                    "aggregate_runs": agg_runs, "trajectory": trajectory,
                    "description": tc.description,
                })
            except Exception as e:
                logger.error(f"[planner_eval] error: {e}")
                results.append({"query": tc.query, "passed": False, "error": str(e),
                                "description": tc.description})
    finally:
        settings.planner_enabled = prev

    accuracy = correct / len(PLAN_TESTS) if PLAN_TESTS else 0.0
    logger.info(f"[planner_eval] accuracy = {accuracy:.0%} ({correct}/{len(PLAN_TESTS)})")
    return {"accuracy": accuracy, "correct": correct, "total": len(PLAN_TESTS), "cases": results}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Agent eval harness")
    parser.add_argument("--routing", action="store_true")
    parser.add_argument("--e2e", action="store_true")
    parser.add_argument("--trajectory", action="store_true")
    parser.add_argument("--planner", action="store_true")
    parser.add_argument("--output", default="eval/results/agent_eval.json")
    args = parser.parse_args()

    run_all = not args.routing and not args.e2e and not args.trajectory and not args.planner

    report: dict = {}

    if args.routing or run_all:
        logger.info("=== Routing eval ===")
        report["routing"] = run_routing_eval()

    if args.e2e or run_all:
        logger.info("=== E2E eval ===")
        report["e2e"] = run_e2e_eval()

    if args.trajectory or run_all:
        logger.info("=== Trajectory eval ===")
        report["trajectory"] = run_trajectory_eval()

    if args.planner or run_all:
        logger.info("=== Planner eval ===")
        report["planner"] = run_planner_eval()

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info(f"Report saved → {args.output}")


if __name__ == "__main__":
    main()
