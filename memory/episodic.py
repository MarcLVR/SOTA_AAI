"""
Episodic / procedural memory via Mem0.

Mem0 maintains a structured memory graph of user facts that persists
across sessions — separate from the semantic vector store (ChromaDB).

Semantic memory  → "what do my documents say about X?"
Episodic memory  → "what do I know about this user's preferences/history?"

Mem0 local mode (no API key):
  - Uses its own embedded vector store
  - Facts are extracted + deduplicated automatically by the LLM
  - Stored in ~/.mem0/ by default (configurable)

Example facts Mem0 stores:
  "User prefers pandas over polars for data manipulation"
  "User is working on a credit risk model using XGBoost"
  "User's preferred language for responses is English"
"""
from __future__ import annotations

from langchain_core.tools import tool
from loguru import logger


# ── Mem0 client (lazy singleton) ──────────────────────────────────────────────

_mem0_client = None


def _get_mem0():
    global _mem0_client
    if _mem0_client is None:
        try:
            from mem0 import Memory  # type: ignore
            from config import settings as _s
            config = {
                "llm": {
                    "provider": "ollama",
                    "config": {
                        "model": _s.ollama_model,
                        "ollama_base_url": _s.ollama_base_url,
                    }
                },
                "embedder": {
                    "provider": "ollama",
                    "config": {
                        "model": _s.ollama_embed_model,
                        "ollama_base_url": _s.ollama_base_url,
                    }
                },
                "vector_store": {
                    "provider": "chroma",
                    "config": {
                        "collection_name": "mem0_episodic",
                        "path": "./data/chroma_db/mem0",
                    }
                }
            }
            _mem0_client = Memory.from_config(config)
            logger.info("Mem0 episodic memory initialised (Ollama local mode)")
        except ImportError:
            logger.warning(
                "mem0ai not installed — episodic memory disabled. "
                "Run: pip install mem0ai"
            )
            _mem0_client = _NoOpMemory()
    return _mem0_client


class _NoOpMemory:
    """Fallback when mem0ai is not installed."""
    def add(self, *args, **kwargs):
        return {"results": []}
    def search(self, *args, **kwargs):
        return {"results": []}
    def get_all(self, *args, **kwargs):
        return {"results": []}


# ── Public helpers ────────────────────────────────────────────────────────────

def add_memory(text: str, user_id: str = "default") -> str:
    """
    Extract facts from `text` and store them in episodic memory for this user.
    Mem0 handles deduplication and merging automatically.
    """
    client = _get_mem0()
    try:
        result = client.add(text, user_id=user_id)
        n = len(result.get("results", []))
        logger.info(f"[episodic_memory] stored {n} fact(s) for user={user_id}")
        return f"Stored {n} memory fact(s)."
    except Exception as e:
        logger.error(f"[episodic_memory] add failed: {e}")
        return f"Memory storage failed: {e}"


def search_memories(query: str, user_id: str = "default", limit: int = 5) -> list[str]:
    """Return relevant episodic memory facts as a list of strings."""
    client = _get_mem0()
    try:
        result = client.search(query, filters={"user_id": user_id}, limit=limit)
        memories = result.get("results", [])
        return [m.get("memory", str(m)) for m in memories]
    except Exception as e:
        logger.error(f"[episodic_memory] search failed: {e}")
        return []


def get_all_memories(user_id: str = "default") -> list[str]:
    """Return all stored facts for a user."""
    client = _get_mem0()
    try:
        result = client.get_all(filters={"user_id": user_id})
        memories = result.get("results", [])
        return [m.get("memory", str(m)) for m in memories]
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
