"""
domain/semantic.py — optional semantic compliance layer.

Compares a document excerpt against the company standards stored in ChromaDB
(collection `agentic_memory`, chunks tagged source_tag="standard") and returns
compliance findings from a SINGLE Claude Haiku call.

Design constraints
------------------
- LOCAL embeddings only (sentence-transformers/all-MiniLM-L6-v2, CPU, free).
  The same model that loaded the standards is used here — vectors are comparable.
- Document text is TRUNCATED to MAX_CHARS (6000) before any embedding or LLM call.
  A full 100-page PDF is never embedded or sent to the LLM.
- Exactly ONE LLM call per document. Token count is logged.
- Gated behind SEMANTIC_ENABLED env var (default false).
  If the flag is off, or if anything fails, returns [] and logs a warning.
  Never raises, never blocks the deterministic engine.
- For single documents on demand only — never call this in a loop over a folder.

Usage
-----
    from domain.semantic import semantic_check
    findings = semantic_check(doc_text, doc_type="policy")
"""
from __future__ import annotations

import json
import os
import re
from loguru import logger

# Max chars of document text to embed / send to LLM (approx. 1–2 pages)
MAX_CHARS = 6000
# Number of standard chunks to retrieve
TOP_K = 3


def semantic_check(doc_text: str, doc_type: str = "unknown") -> list[dict]:
    """
    Compare a document excerpt against company standards in ChromaDB.

    Returns a list of finding dicts:
        [{
            "standard_matched": str,   # filename of the matched standard
            "compliance":       str,   # "compliant" | "partial" | "violation"
            "detail":           str,   # 1-2 sentence explanation
            "severity":         str,   # "high" | "medium" | "low"
        }]

    Returns [] (silently) if:
        - SEMANTIC_ENABLED env var is not "true"
        - ChromaDB / collection is unavailable or empty
        - Embedding fails
        - LLM call fails
        - LLM response cannot be parsed as JSON
    """
    # ── Gate ──────────────────────────────────────────────────────────────────
    if os.getenv("SEMANTIC_ENABLED", "false").lower() != "true":
        return []

    # ── Truncate ──────────────────────────────────────────────────────────────
    excerpt = (doc_text or "").strip()[:MAX_CHARS]
    if not excerpt:
        logger.warning("[semantic] doc_text is empty — skipping")
        return []

    try:
        return _run(excerpt, doc_type)
    except Exception as exc:
        logger.warning(f"[semantic] unexpected error — returning []: {exc}")
        return []


# ── Internal implementation ───────────────────────────────────────────────────

def _run(excerpt: str, doc_type: str) -> list[dict]:
    # ── 1. Retrieve top-K standard chunks ────────────────────────────────────
    try:
        from memory.vector_store import get_vectorstore
        vs = get_vectorstore()
    except Exception as exc:
        logger.warning(f"[semantic] ChromaDB unavailable: {exc}")
        return []

    try:
        hits = vs.similarity_search_with_relevance_scores(
            query=excerpt,
            k=TOP_K,
            filter={"source_tag": "standard"},
        )
    except Exception as exc:
        logger.warning(f"[semantic] similarity search failed: {exc}")
        return []

    if not hits:
        logger.warning("[semantic] no standard chunks found — ChromaDB may be empty")
        return []

    # Log what was retrieved
    for doc, score in hits:
        fname = doc.metadata.get("filename", "?")
        logger.info(f"[semantic] retrieved {fname!r}  relevance={score:.3f}")

    # ── 2. Build prompt ───────────────────────────────────────────────────────
    standards_block = ""
    for i, (doc, score) in enumerate(hits, 1):
        fname = doc.metadata.get("filename", f"standard_{i}")
        standards_block += (
            f"[{i}] {fname}  (relevance {score:.2f})\n"
            f"{doc.page_content.strip()}\n\n"
        )

    prompt = (
        "You are a document compliance checker. "
        "Evaluate the document excerpt against the retrieved company standards.\n\n"
        f"DOCUMENT TYPE: {doc_type}\n\n"
        "RETRIEVED COMPANY STANDARDS (top 3 most relevant):\n"
        f"{standards_block}"
        f"DOCUMENT EXCERPT (first {len(excerpt)} chars):\n"
        "---\n"
        f"{excerpt}\n"
        "---\n\n"
        "Return ONLY a JSON array — no markdown, no explanation, no other text.\n"
        "One entry per retrieved standard:\n"
        '[\n'
        '  {\n'
        '    "standard_matched": "<filename from the list above>",\n'
        '    "compliance": "compliant|partial|violation",\n'
        '    "detail": "<specific 1-2 sentence finding>",\n'
        '    "severity": "high|medium|low"\n'
        '  }\n'
        ']\n\n'
        "Rules:\n"
        "- Base findings ONLY on what the retrieved standard text actually says.\n"
        "- severity: high=clear violation, medium=partial gap, low=minor/cosmetic.\n"
        "- If no issue found, set compliance=compliant and severity=low."
    )

    # ── 3. ONE LLM call ───────────────────────────────────────────────────────
    try:
        from agents.llm import get_llm
        from langchain_core.messages import HumanMessage

        llm = get_llm(role="general")
        response = llm.invoke([HumanMessage(content=prompt)])
    except Exception as exc:
        logger.warning(f"[semantic] LLM call failed: {exc}")
        return []

    # Log token usage
    usage = getattr(response, "usage_metadata", None) or {}
    in_tok  = usage.get("input_tokens",  "?")
    out_tok = usage.get("output_tokens", "?")
    total   = (in_tok + out_tok) if isinstance(in_tok, int) and isinstance(out_tok, int) else "?"
    logger.info(f"[semantic] LLM tokens: total={total} (in={in_tok} out={out_tok})")

    # ── 4. Parse JSON ─────────────────────────────────────────────────────────
    raw = (response.content or "").strip()
    try:
        # Find the JSON array even if the model adds surrounding text
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            raise ValueError("no JSON array found in response")
        findings: list[dict] = json.loads(m.group())
    except Exception as exc:
        logger.warning(f"[semantic] JSON parse failed: {exc}\nraw={raw[:300]}")
        return []

    # Validate shape and normalise
    valid = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        valid.append({
            "standard_matched": str(f.get("standard_matched", "?")),
            "compliance":       str(f.get("compliance", "unknown")).lower(),
            "detail":           str(f.get("detail", "")),
            "severity":         str(f.get("severity", "medium")).lower(),
        })

    logger.info(f"[semantic] returned {len(valid)} finding(s)")
    return valid
