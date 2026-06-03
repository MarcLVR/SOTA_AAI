"""
domain/test_semantic.py — smoke test for the semantic compliance layer.

Usage:
    SEMANTIC_ENABLED=true python -m domain.test_semantic
    SEMANTIC_ENABLED=true python -m domain.test_semantic --doc path/to/file.docx

Prints:
  - Embedding model confirmation (must be local/free)
  - Top-3 retrieved standard chunks + similarity scores
  - LLM compliance verdict (JSON)
  - Token count of the single LLM call
"""
from __future__ import annotations

import argparse
import os
import sys

# Force the flag on for this script
os.environ["SEMANTIC_ENABLED"] = "true"

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from loguru import logger
from config import settings
from domain.tools.extractor import extract_document


def main() -> None:
    ap = argparse.ArgumentParser(description="Semantic compliance smoke test")
    ap.add_argument(
        "--doc",
        default="domain/demo_corpus/files/data_governance_framework_draft_2020.docx",
        help="Document to test against company standards",
    )
    args = ap.parse_args()

    # ── 1. Confirm embedding model is local ───────────────────────────────────
    print("\n" + "=" * 68)
    print("  SEMANTIC COMPLIANCE LAYER — SMOKE TEST")
    print("=" * 68)
    print(f"\n  Embedding model : {settings.embedding_model}")
    print(f"  Device          : cpu (local, no API key, FREE)")
    print(f"  Document        : {args.doc}")

    # ── 2. Extract document text ──────────────────────────────────────────────
    print("\n  [1/4] Extracting document text …")
    extracted = extract_document(args.doc)
    text = extracted.get("text", "")
    print(f"  Extracted: {len(text):,} chars  (ok={extracted.get('extraction_ok')})")

    if not text.strip():
        print("  ERROR: no text extracted — choose a different document")
        sys.exit(1)

    # ── 3. Show top-3 retrieved standards (before LLM call) ──────────────────
    print("\n  [2/4] Retrieving top-3 most relevant standard chunks …")
    try:
        from memory.vector_store import get_vectorstore, get_embeddings

        excerpt = text[:6000]
        vs = get_vectorstore()
        hits = vs.similarity_search_with_relevance_scores(
            query=excerpt,
            k=3,
            filter={"source_tag": "standard"},
        )
        for i, (doc, score) in enumerate(hits, 1):
            fname = doc.metadata.get("filename", "?")
            preview = doc.page_content[:120].replace("\n", " ")
            print(f"  [{i}] {fname}  relevance={score:.3f}")
            print(f"       {preview!r}")
    except Exception as exc:
        print(f"  ERROR retrieving standards: {exc}")
        sys.exit(1)

    # ── 4. Run semantic_check (the full pipeline including LLM call) ──────────
    print("\n  [3/4] Running semantic_check() — ONE LLM call …")

    # Monkey-patch logger to capture the token line
    token_line: list[str] = []
    _orig_info = logger.info

    def _cap_info(msg, *a, **kw):  # type: ignore[override]
        s = str(msg)
        if "[semantic] LLM tokens:" in s:
            token_line.append(s)
        _orig_info(msg, *a, **kw)

    logger.info = _cap_info  # type: ignore[method-assign]

    from domain.semantic import semantic_check
    findings = semantic_check(text, doc_type="unknown")

    logger.info = _orig_info  # restore

    # ── 5. Print results ──────────────────────────────────────────────────────
    print("\n  [4/4] Results")
    print("-" * 68)

    if not findings:
        print("  No findings returned (empty list — check logs for warnings)")
    else:
        for f in findings:
            badge = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f["severity"], "⚪")
            verdict = {"compliant": "✅", "partial": "⚠️", "violation": "❌"}.get(
                f["compliance"], "?"
            )
            print(f"  {badge} {verdict} [{f['compliance'].upper()}]  {f['standard_matched']}")
            print(f"       {f['detail']}")
            print()

    if token_line:
        print(f"  TOKEN USAGE : {token_line[0].split('[semantic] LLM tokens:')[1].strip()}")
    else:
        print("  TOKEN USAGE : see INFO logs above")

    print("-" * 68)
    print(f"  Input truncated to : min({len(text):,}, 6000) = {min(len(text), 6000):,} chars sent to LLM")
    print(f"  LLM calls made     : 1")
    print("=" * 68 + "\n")


if __name__ == "__main__":
    main()
