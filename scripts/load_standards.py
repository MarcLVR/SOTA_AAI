"""
Load the company standards (standards/*.md) into the RAG vector store so the
Researcher agent ("Ask the agent" tab) can cite them.

Design notes
------------
- The Researcher queries the SINGLE existing collection `agentic_memory` via
  `memory.retrieve_from_memory`. So standards must land in THAT collection, not a
  separate one — otherwise they would be orphaned and never retrieved.
- We reuse `memory.vector_store.ingest_documents(..., source_tag="standard")` so the
  chunking/embedding path is identical to user-uploaded docs. No changes to the
  retriever or researcher are needed.
- Idempotent: existing chunks tagged `source_tag="standard"` are deleted before
  re-ingesting, so re-running does not duplicate content.

Usage (inside docker-dave):
    python -m scripts.load_standards
    python -m scripts.load_standards --check   # ingest, then run a sample query
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from memory.vector_store import ingest_documents, get_vectorstore

STANDARDS_DIR = Path(__file__).resolve().parent.parent / "standards"
SOURCE_TAG = "standard"


def _purge_existing_standards() -> int:
    """Delete previously ingested standard chunks so re-runs don't duplicate."""
    col = get_vectorstore()._collection  # langchain_chroma exposes the raw collection
    before = col.count()
    col.delete(where={"source_tag": SOURCE_TAG})
    after = col.count()
    removed = before - after
    if removed:
        logger.info(f"Purged {removed} existing '{SOURCE_TAG}' chunks before reload")
    return removed


def load() -> int:
    md_files = sorted(STANDARDS_DIR.glob("*.md"))
    if not md_files:
        logger.error(f"No .md files found in {STANDARDS_DIR}")
        return 0

    _purge_existing_standards()
    n = ingest_documents(md_files, source_tag=SOURCE_TAG)
    logger.info(f"Ingested {n} chunks from {len(md_files)} standards files")
    for f in md_files:
        logger.info(f"  - {f.name}")
    return n


def _check() -> None:
    """Smoke test: query the store and show what the Researcher would retrieve."""
    from memory import retrieve_from_memory

    for q in [
        "How old can a contract be before it must be reviewed?",
        "Is ISO 27001:2013 still valid?",
        "What metadata does a policy need?",
    ]:
        print(f"\n=== QUERY: {q}")
        print(retrieve_from_memory.invoke({"query": q, "k": 2}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="After loading, run sample retrieval queries.")
    args = ap.parse_args()

    count = load()
    if count == 0:
        sys.exit(1)
    if args.check:
        _check()
