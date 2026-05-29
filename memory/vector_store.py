"""
RAG memory backed by ChromaDB + local HuggingFace sentence-transformers.
No API key required — embeddings run on CPU/GPU locally.

Provides:
  - ingest_documents()  : add text/PDF files to the vector store
  - retriever()         : returns a LangChain retriever
  - retrieval_tool()    : LangChain @tool wrapping semantic search
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from langchain_chroma import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_community.document_loaders import (
    TextLoader,
    PyPDFLoader,
    UnstructuredMarkdownLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger

from config import settings


# ── Embeddings (lazy singleton) ────────────────────────────────────────────────

_embeddings: HuggingFaceEmbeddings | None = None


def get_embeddings() -> HuggingFaceEmbeddings:
    global _embeddings
    if _embeddings is None:
        logger.info(f"Loading embedding model: {settings.embedding_model}")
        _embeddings = HuggingFaceEmbeddings(
            model_name=settings.embedding_model,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    return _embeddings


# ── Vector store (lazy singleton) ──────────────────────────────────────────────

_vectorstore: Chroma | None = None


def get_vectorstore() -> Chroma:
    global _vectorstore
    if _vectorstore is None:
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Loading ChromaDB from {settings.chroma_persist_dir}")
        _vectorstore = Chroma(
            collection_name="agentic_memory",
            embedding_function=get_embeddings(),
            persist_directory=str(settings.chroma_persist_dir),
        )
    return _vectorstore


# ── Document ingestion ────────────────────────────────────────────────────────

LOADERS = {
    ".txt": TextLoader,
    ".md": UnstructuredMarkdownLoader,
    ".pdf": PyPDFLoader,
}

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=150,
    add_start_index=True,
)


def ingest_documents(paths: List[str | Path], source_tag: str = "user") -> int:
    """
    Load, split, and embed documents into the vector store.

    Returns the number of chunks added.
    """
    docs: List[Document] = []
    for p in paths:
        path = Path(p)
        suffix = path.suffix.lower()
        loader_cls = LOADERS.get(suffix)
        if loader_cls is None:
            logger.warning(f"Unsupported file type skipped: {path}")
            continue
        try:
            loaded = loader_cls(str(path)).load()
            for doc in loaded:
                doc.metadata["source_tag"] = source_tag
                doc.metadata["filename"] = path.name
            docs.extend(loaded)
            logger.info(f"Loaded {len(loaded)} doc(s) from {path.name}")
        except Exception as e:
            logger.error(f"Failed to load {path}: {e}")

    if not docs:
        return 0

    chunks = splitter.split_documents(docs)
    get_vectorstore().add_documents(chunks)
    logger.info(f"Ingested {len(chunks)} chunks into ChromaDB")
    return len(chunks)


# ── Retriever ─────────────────────────────────────────────────────────────────

def get_retriever(k: int | None = None):
    """Return a LangChain retriever with MMR search."""
    return get_vectorstore().as_retriever(
        search_type="mmr",
        search_kwargs={"k": k or settings.top_k_retrieval, "fetch_k": 20},
    )


# ── LangChain tool ────────────────────────────────────────────────────────────

@tool
def retrieve_from_memory(query: str, k: int = 5) -> str:
    """
    Semantic search over the agent's knowledge base (uploaded documents and past notes).
    Returns the most relevant text chunks.

    Args:
        query: Natural language query.
        k: Number of chunks to return (default 5).
    """
    logger.info(f"[retrieve_from_memory] query='{query}' k={k}")
    try:
        retriever = get_retriever(k=k)
        docs = retriever.invoke(query)
        if not docs:
            return "No relevant documents found in memory."

        results = []
        for i, doc in enumerate(docs, 1):
            src = doc.metadata.get("filename", "unknown")
            results.append(f"[{i}] Source: {src}\n{doc.page_content}")
        return "\n\n---\n\n".join(results)
    except Exception as e:
        logger.error(f"[retrieve_from_memory] error: {e}")
        return f"Memory retrieval failed: {e}"
