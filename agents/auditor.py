"""
Auditor agent — AI-readiness and data-hygiene specialist.
Tools: run_full_audit (corpus), plus granular per-document tools.
"""
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import SystemMessage

from .llm import get_llm
from tools import AUDITOR_TOOLS
from domain.prompts import INSPECTOR_SYSTEM

AUDITOR_SYSTEM = SystemMessage(content=(
    "You are the Auditor agent specialising in AI-readiness and data hygiene.\n\n"
    "## Corpus audit (most common)\n"
    "When asked to audit a folder or repository, call run_full_audit(folder_path) ONCE.\n"
    "It runs the full pipeline (crawl → extract → score → aggregate) in a single call\n"
    "and returns a JSON dashboard. Parse the JSON and report:\n"
    "  • Headline: total documents, size, % needing supervision, remediation hours.\n"
    "  • Staleness: stale count, cold count, oldest document.\n"
    "  • Standards: non-standard format count, missing sections, retired-standard hits.\n"
    "  • Governance: no-owner count, unclassified count.\n"
    "  • By document type: counts and flagged per type.\n"
    "  • Top offenders: list the worst trust scores with their top finding.\n\n"
    "## Single-document inspection\n"
    "When asked about one specific file, call inspect_document(path) ONCE.\n"
    "Do NOT use extract_document / score_staleness / check_standards / check_governance\n"
    "separately for single files — passing raw document text as tool arguments is\n"
    "expensive and unnecessary. inspect_document runs all checks internally and\n"
    "returns only structured findings (no raw text, no wasted tokens).\n\n"
    "## Output style\n"
    "Be specific and cite filenames. Do not invent findings beyond what the tools return.\n\n"
    + INSPECTOR_SYSTEM
))


def build_auditor_agent(extra_tools=None):
    tools = AUDITOR_TOOLS + (extra_tools or [])
    return create_react_agent(
        model=get_llm(role="auditor"),
        tools=tools,
        prompt=AUDITOR_SYSTEM,
    )
