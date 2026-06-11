"""
Episodic / procedural memory — pluggable backend behind a stable interface.

Episodic memory persists user facts across sessions, separate from the semantic
vector store (ChromaDB):

  Semantic memory  → "what do my documents say about X?"
  Episodic memory  → "what do I know about this user's preferences/history?"

Two backends, selected by ``EPISODIC_BACKEND`` (default ``mem0``):

  * **mem0** (default) — Mem0 in fully-local mode (Ollama LLM + embeddings, Chroma
    store under ``data/chroma_db/mem0``). Persistent, no API key. Unchanged from
    before; the shipped default for one release.
  * **langmem** (opt-in) — LangChain-native LangMem. Adds background *consolidation*
    (dedup / merge / update of overlapping facts) and procedural memory on top of a
    LangGraph ``BaseStore``. Runs fully local with Ollama embeddings + a local LLM.
    Requires ``pip install langmem``. NOTE: the local store is a process-lifetime
    ``InMemoryStore`` (semantic search via Ollama embeddings) — persistent LangMem
    needs a Postgres/pgvector ``BaseStore`` (there is no SQLite vector store). So on
    the no-Postgres path the langmem backend resets between restarts; Mem0 remains
    the persistent default. Switching backends does not migrate data automatically —
    see ``scripts/migrate_episodic.py``.

The public surface (``add_memory``, ``search_memories``, ``get_all_memories`` and the
``remember`` / ``recall`` tools) is identical regardless of backend, so callers in
the graph and agents never change.
"""
from __future__ import annotations

from typing import Protocol

from langchain_core.tools import tool
from loguru import logger

from config import settings


# ── Backend protocol ──────────────────────────────────────────────────────────

class _EpisodicBackend(Protocol):
    def add(self, text: str, user_id: str) -> int: ...
    def search(self, query: str, user_id: str, limit: int) -> list[str]: ...
    def get_all(self, user_id: str) -> list[str]: ...


# ── Mem0 backend (default) ──────────────────────────────────────────────────────

class _NoOpBackend:
    """Fallback when the selected backend's package is not installed."""
    def __init__(self, reason: str = ""):
        self._reason = reason
    def add(self, text: str, user_id: str) -> int:
        return 0
    def search(self, query: str, user_id: str, limit: int) -> list[str]:
        return []
    def get_all(self, user_id: str) -> list[str]:
        return []


class _Mem0Backend:
    """Mem0 in local mode (Ollama LLM + embeddings + Chroma)."""

    def __init__(self):
        from mem0 import Memory  # type: ignore
        config = {
            "llm": {
                "provider": "ollama",
                "config": {
                    "model": settings.ollama_model,
                    "ollama_base_url": settings.ollama_base_url,
                },
            },
            "embedder": {
                "provider": "ollama",
                "config": {
                    "model": settings.ollama_embed_model,
                    "ollama_base_url": settings.ollama_base_url,
                },
            },
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": "mem0_episodic",
                    "path": "./data/chroma_db/mem0",
                },
            },
        }
        self._client = Memory.from_config(config)
        logger.info("Episodic memory backend = mem0 (Ollama local mode)")

    def add(self, text: str, user_id: str) -> int:
        result = self._client.add(text, user_id=user_id)
        return len(result.get("results", []))

    def search(self, query: str, user_id: str, limit: int) -> list[str]:
        result = self._client.search(query, filters={"user_id": user_id}, limit=limit)
        return [m.get("memory", str(m)) for m in result.get("results", [])]

    def get_all(self, user_id: str) -> list[str]:
        result = self._client.get_all(filters={"user_id": user_id})
        return [m.get("memory", str(m)) for m in result.get("results", [])]


# ── LangMem backend (opt-in) ────────────────────────────────────────────────────

class _LangMemBackend:
    """LangMem over a LangGraph BaseStore with background consolidation.

    Uses a process-lifetime ``InMemoryStore`` with Ollama embeddings for semantic
    search (fully local, no service). The store manager extracts + consolidates
    facts on every ``add`` (dedup / merge / update). For durable cross-restart
    storage, back this with a Postgres/pgvector store (future enhancement); today
    Mem0 is the persistent default.
    """

    _NS = ("memories", "{langgraph_user_id}")

    def __init__(self):
        from langgraph.store.memory import InMemoryStore
        from langchain_ollama import OllamaEmbeddings
        from langmem import create_memory_store_manager
        from agents.llm import get_llm

        embeddings = OllamaEmbeddings(
            model=settings.ollama_embed_model, base_url=settings.ollama_base_url
        )
        # nomic-embed-text is 768-dim (verified); keep dims in sync with the model.
        self._store = InMemoryStore(index={"dims": 768, "embed": embeddings})
        self._manager = create_memory_store_manager(
            get_llm(),
            namespace=self._NS,
            store=self._store,
            enable_inserts=True,
            enable_deletes=False,
        )
        logger.info("Episodic memory backend = langmem (InMemoryStore + Ollama, consolidation on)")

    @staticmethod
    def _fmt(item) -> str:
        # langmem stores {'kind': 'Memory', 'content': {'content': '<text>'}}; unwrap
        # to the plain string, tolerating either the nested or flat shape.
        v = getattr(item, "value", item)
        for _ in range(3):
            if isinstance(v, dict):
                v = v.get("content") or v.get("memory") or v
                if not isinstance(v, dict):
                    break
            else:
                break
        return v if isinstance(v, str) else str(v)

    def add(self, text: str, user_id: str) -> int:
        cfg = {"configurable": {"langgraph_user_id": user_id}}
        # The manager extracts salient facts and consolidates against existing ones.
        out = self._manager.invoke({"messages": [{"role": "user", "content": text}]}, config=cfg)
        return len(out) if isinstance(out, list) else 1

    def search(self, query: str, user_id: str, limit: int) -> list[str]:
        items = self._store.search(("memories", user_id), query=query, limit=limit)
        return [self._fmt(i) for i in items]

    def get_all(self, user_id: str) -> list[str]:
        items = self._store.search(("memories", user_id), limit=1000)
        return [self._fmt(i) for i in items]


# ── Backend selection (lazy singleton) ────────────────────────────────────────

_backend: _EpisodicBackend | None = None


def _get_backend() -> _EpisodicBackend:
    global _backend
    if _backend is not None:
        return _backend
    choice = (settings.episodic_backend or "mem0").lower()
    try:
        if choice == "langmem":
            _backend = _LangMemBackend()
        else:
            _backend = _Mem0Backend()
    except ImportError as e:
        pkg = "langmem" if choice == "langmem" else "mem0ai"
        logger.warning(f"episodic backend '{choice}' unavailable ({e}); "
                       f"run: pip install {pkg}. Episodic memory disabled.")
        _backend = _NoOpBackend(str(e))
    except Exception as e:
        logger.error(f"episodic backend '{choice}' init failed: {e}. Episodic memory disabled.")
        _backend = _NoOpBackend(str(e))
    return _backend


# ── Public helpers (stable interface — callers never change) ──────────────────

def add_memory(text: str, user_id: str = "default") -> str:
    """Extract + store facts from `text` for this user (backend handles dedup/merge)."""
    try:
        n = _get_backend().add(text, user_id=user_id)
        logger.info(f"[episodic_memory] stored {n} fact(s) for user={user_id}")
        return f"Stored {n} memory fact(s)."
    except Exception as e:
        logger.error(f"[episodic_memory] add failed: {e}")
        return f"Memory storage failed: {e}"


def search_memories(query: str, user_id: str = "default", limit: int = 5) -> list[str]:
    """Return relevant episodic memory facts as a list of strings."""
    try:
        return _get_backend().search(query, user_id=user_id, limit=limit)
    except Exception as e:
        logger.error(f"[episodic_memory] search failed: {e}")
        return []


def get_all_memories(user_id: str = "default") -> list[str]:
    """Return all stored facts for a user."""
    try:
        return _get_backend().get_all(user_id=user_id)
    except Exception as e:
        logger.error(f"[episodic_memory] get_all failed: {e}")
        return []


# ── LangChain tools ───────────────────────────────────────────────────────────

@tool
def remember(text: str) -> str:
    """
    Store a fact or preference about the user in long-term episodic memory.
    Use this when the user shares something about themselves, their preferences,
    ongoing projects, or constraints that should be remembered across sessions.

    Args:
        text: The fact or preference to remember (e.g. 'User prefers Python 3.11').
    """
    return add_memory(text)


@tool
def recall(query: str) -> str:
    """
    Search episodic memory for facts relevant to a query.
    Use this at the start of a conversation to personalise the response,
    or when you need context about the user's preferences or past work.

    Args:
        query: What you want to recall (e.g. 'user coding preferences').
    """
    facts = search_memories(query)
    if not facts:
        return "No relevant memories found."
    return "Remembered facts:\n" + "\n".join(f"- {f}" for f in facts)
