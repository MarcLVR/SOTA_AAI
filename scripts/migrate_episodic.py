"""
Migrate episodic memory from the Mem0 backend into the LangMem backend.

Episodic backends store facts in different places (Mem0 → Chroma at
``data/chroma_db/mem0``; LangMem → a LangGraph BaseStore), so switching
``EPISODIC_BACKEND`` does NOT carry history across automatically. This script
reads every fact out of the Mem0 store and re-adds it through the LangMem
backend (which re-extracts and consolidates).

Usage:
    # Migrate the default user
    python -m scripts.migrate_episodic

    # Migrate specific users
    python -m scripts.migrate_episodic --users default alice bob

    # Preview without writing
    python -m scripts.migrate_episodic --dry-run

Notes:
  * Requires both backends installed: ``pip install mem0ai langmem``.
  * LangMem's local store is process-lifetime (InMemoryStore) unless backed by a
    persistent Postgres/pgvector store — see ``memory/episodic.py``. For a durable
    migration, run this inside the same long-lived process/store you serve from,
    or wire a persistent store first. This script is best-effort and idempotent
    at the fact level (LangMem dedups on consolidation).
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger


def _read_mem0(users: list[str]) -> dict[str, list[str]]:
    from memory.episodic import _Mem0Backend
    backend = _Mem0Backend()
    out: dict[str, list[str]] = {}
    for u in users:
        facts = backend.get_all(u)
        out[u] = facts
        logger.info(f"[migrate] mem0: {len(facts)} fact(s) for user={u}")
    return out


def _write_langmem(facts_by_user: dict[str, list[str]], dry_run: bool) -> int:
    if dry_run:
        for u, facts in facts_by_user.items():
            for f in facts:
                logger.info(f"[dry-run] would store for {u}: {f[:100]}")
        return sum(len(v) for v in facts_by_user.values())

    from memory.episodic import _LangMemBackend
    backend = _LangMemBackend()
    written = 0
    for u, facts in facts_by_user.items():
        for f in facts:
            try:
                backend.add(f, user_id=u)
                written += 1
            except Exception as e:
                logger.error(f"[migrate] failed to store fact for {u}: {e}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate episodic memory Mem0 → LangMem")
    parser.add_argument("--users", nargs="+", default=["default"],
                        help="user ids to migrate (default: 'default')")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be migrated without writing")
    args = parser.parse_args()

    try:
        facts = _read_mem0(args.users)
    except ImportError:
        logger.error("mem0ai not installed — nothing to migrate from. pip install mem0ai")
        return 1
    except Exception as e:
        logger.error(f"failed to read Mem0 store: {e}")
        return 1

    total = sum(len(v) for v in facts.values())
    if total == 0:
        logger.info("No Mem0 facts found — nothing to migrate.")
        return 0

    try:
        written = _write_langmem(facts, args.dry_run)
    except ImportError:
        logger.error("langmem not installed — cannot migrate into it. pip install langmem")
        return 1

    verb = "would migrate" if args.dry_run else "migrated"
    logger.info(f"[migrate] {verb} {written}/{total} fact(s) across {len(facts)} user(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
