"""
Agent eval harness — tests routing accuracy and end-to-end response quality.

Two test modes:
  1. Routing eval  : given a query, does the supervisor route to the expected agent?
  2. E2E eval      : run a query through the full graph and score the response.

Usage:
    python -m eval.agent_eval               # runs all tests
    python -m eval.agent_eval --routing     # routing tests only
    python -m eval.agent_eval --e2e         # e2e tests only

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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Agent eval harness")
    parser.add_argument("--routing", action="store_true")
    parser.add_argument("--e2e", action="store_true")
    parser.add_argument("--output", default="eval/results/agent_eval.json")
    args = parser.parse_args()

    run_all = not args.routing and not args.e2e

    report: dict = {}

    if args.routing or run_all:
        logger.info("=== Routing eval ===")
        report["routing"] = run_routing_eval()

    if args.e2e or run_all:
        logger.info("=== E2E eval ===")
        report["e2e"] = run_e2e_eval()

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info(f"Report saved → {args.output}")


if __name__ == "__main__":
    main()
