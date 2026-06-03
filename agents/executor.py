"""
ReAct Executor — applies document fixes upon owner approval.

Pattern: Thought → Action → Observation (max 3 attempts per finding).
Triggered by telegram_bot when owner taps "Que DAVE lo corrija".
"""

import os
import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "chase"))

import requests

from db import get_run, update_run_status, update_finding_status

log = logging.getLogger("dave-executor")

LLM_PROVIDER  = os.getenv("LLM_PROVIDER", "groq")
GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# LLM call (Groq or Anthropic)
# ---------------------------------------------------------------------------

def _call_llm(prompt: str) -> str:
    if LLM_PROVIDER == "groq" and GROQ_API_KEY:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    elif ANTHROPIC_KEY:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]

    raise RuntimeError("No LLM provider configured (set GROQ_API_KEY or ANTHROPIC_API_KEY)")


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

def _react_step(document: str, finding: dict, attempt: int) -> dict:
    """
    One ReAct iteration. Returns {"thought": ..., "action": ..., "observation": ..., "done": bool}.
    """
    prompt = f"""You are DAVE, a document compliance assistant. Apply the following fix to a document.

Document: {document}
Finding: {finding['title']}
Location: {finding['location']}
Suggested fix: {finding['suggestion']}
Attempt: {attempt}/{MAX_ATTEMPTS}

Respond in JSON with this exact structure:
{{
  "thought": "reasoning about how to apply the fix",
  "action": "specific action to take (e.g. replace text, anonymize field, rename section)",
  "observation": "result of applying the action — success or issue found",
  "done": true or false
}}"""

    raw = _call_llm(prompt)

    # Extract JSON from response
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return {
            "thought": "Could not parse LLM response",
            "action": "none",
            "observation": raw[:200],
            "done": False,
        }


def execute_fix(run_id: int) -> dict:
    """
    Run the ReAct loop for pending findings in a validation run.
    Returns a summary dict with results per finding.
    """
    run = get_run(run_id)
    if not run:
        return {"success": False, "error": f"Run #{run_id} not found"}

    # Support both old schema (document) and real DB schema (doc_name)
    document = run.get("doc_name") or run.get("document", "unknown")
    results = []

    pending_findings = [f for f in run["findings"] if f.get("status", "pending") == "pending"]

    for finding in pending_findings:
        # Support both old schema (title/suggestion) and real DB schema (rule_code/detail/proposed_fix)
        finding_label = finding.get("rule_code") or finding.get("title", "finding")
        finding_fix = finding.get("proposed_fix") or finding.get("suggestion", "")

        log.info("Fixing: %s", finding_label)
        steps = []
        success = False

        for attempt in range(1, MAX_ATTEMPTS + 1):
            step = _react_step(document, {
                "title":      finding_label,
                "location":   finding.get("location", ""),
                "suggestion": finding_fix,
                "severity":   finding.get("severity", "medium"),
            }, attempt)
            steps.append(step)
            log.info("  Attempt %d — done=%s", attempt, step.get("done"))

            if step.get("done"):
                success = True
                break

        if success and finding.get("id"):
            resolution = steps[-1].get("observation", "")[:500]
            update_finding_status(finding["id"], "fixed", resolution=resolution)

        results.append({
            "finding": finding_label,
            "success": success,
            "attempts": len(steps),
            "steps": steps,
        })

    all_ok = all(r["success"] for r in results) if results else True
    update_run_status(run_id, "fixed" if all_ok else "partial_fix")

    return {
        "run_id":   run_id,
        "document": document,
        "success":  all_ok,
        "results":  results,
    }


# ---------------------------------------------------------------------------
# CLI for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python3 executor.py <run_id>")
        sys.exit(1)

    run_id = int(sys.argv[1])
    result = execute_fix(run_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))
