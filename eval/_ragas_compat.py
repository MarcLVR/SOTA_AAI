"""Compatibility shim so RAGAS imports under langchain-community 0.4.x.

ragas 0.4.3 (`ragas/llms/base.py`) hard-imports
``langchain_community.chat_models.vertexai.ChatVertexAI`` at module load. That
submodule was removed when ``langchain-community`` was sunset (0.4.x), so the
import raises ``ModuleNotFoundError`` and the whole RAG eval cannot start — even
though this project never uses Vertex AI.

We register a lightweight stub module for that path *before* ragas is imported.
The stub only raises if someone actually instantiates ``ChatVertexAI`` (which this
project never does). Real Vertex usage should install ``langchain-google-vertexai``.
"""
from __future__ import annotations

import sys
import types

_MOD = "langchain_community.chat_models.vertexai"


def patch_ragas_vertexai() -> None:
    """Register a stub for the removed Vertex submodule if it's absent. Idempotent."""
    if _MOD in sys.modules:
        return
    try:
        __import__(_MOD)
        return  # real module present (older langchain-community) — leave it
    except Exception:
        pass

    shim = types.ModuleType(_MOD)

    class ChatVertexAI:  # noqa: D401 - minimal stub
        """Placeholder; this project does not use Vertex AI."""
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "ChatVertexAI is unavailable under langchain-community 0.4.x. "
                "Install langchain-google-vertexai to use Vertex AI with RAGAS."
            )

    shim.ChatVertexAI = ChatVertexAI
    sys.modules[_MOD] = shim
    # Also attach to the parent package so `from ...vertexai import ChatVertexAI` resolves.
    try:
        parent = __import__("langchain_community.chat_models", fromlist=["chat_models"])
        setattr(parent, "vertexai", shim)
    except Exception:
        pass
