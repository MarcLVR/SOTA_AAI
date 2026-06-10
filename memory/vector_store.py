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

from pydantic import BaseModel, Field
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


# ── Agentic retrieval: grade → (rewrite + re-retrieve) → cite ──────────────────

def _citation(doc: Document, score: float | None) -> str:
    """Stable, structured citation header for a chunk."""
    src   = doc.metadata.get("filename", "unknown")
    start = doc.metadata.get("start_index")
    loc   = f"@{start}" if start is not None else ""
    sc    = f" dist={score:.3f}" if score is not None else ""   # vector distance, lower = closer
    return f"Source: {src}{loc}{sc}"


class _GradeResult(BaseModel):
    """CRAG-style grading of a retrieved candidate set."""
    relevant_indices: list[int] = Field(
        default_factory=list,
        description="1-based indices of chunks that actually help answer the query",
    )
    sufficient: bool = Field(
        default=True,
        description="True if the relevant chunks are enough to answer the query",
    )
    rewrite: str = Field(
        default="",
        description="A better search query to try if the chunks are insufficient (else empty)",
    )


_GRADER_PROMPT = (
    "You grade retrieved document chunks for a RAG system. Given the user query and "
    "numbered chunks, return: the 1-based indices of chunks that genuinely help answer "
    "the query (drop off-topic ones), whether they are SUFFICIENT to answer, and — only "
    "if insufficient — a single improved search query.\n\nQuery: {query}\n\nChunks:\n{chunks}"
)


def _grade(query: str, docs: list[Document]) -> _GradeResult:
    """Best-effort LLM relevance grading. Degrades to 'keep all' on any failure."""
    from agents.llm import get_llm
    from agents.resilience import resilient_invoke

    numbered = "\n\n".join(f"[{i}] {d.page_content[:600]}" for i, d in enumerate(docs, 1))
    prompt = _GRADER_PROMPT.format(query=query, chunks=numbered)
    try:
        grader = get_llm(role="critic").with_structured_output(_GradeResult)
        res: _GradeResult = resilient_invoke(grader, prompt, label="rag-grader")
        # Keep only valid indices; if the grader returned nothing usable, keep all.
        res.relevant_indices = [i for i in res.relevant_indices if 1 <= i <= len(docs)]
        if not res.relevant_indices:
            res.relevant_indices = list(range(1, len(docs) + 1))
        return res
    except Exception as e:
        logger.warning(f"[retrieve_from_memory] grading skipped: {e}")
        return _GradeResult(relevant_indices=list(range(1, len(docs) + 1)), sufficient=True)


def _search(query: str, k: int) -> list[tuple[Document, float | None]]:
    """Similarity search returning (doc, score). Falls back to MMR if scores unavailable."""
    try:
        pairs = get_vectorstore().similarity_search_with_score(query, k=k)
        return [(d, s) for d, s in pairs]
    except Exception:
        return [(d, None) for d in get_retriever(k=k).invoke(query)]


# ── LangChain tool ────────────────────────────────────────────────────────────

@tool
def retrieve_from_memory(query: str, k: int = 5) -> str:
    """
    Semantic search over the agent's knowledge base (uploaded documents and past notes).
    Retrieved chunks are relevance-graded (off-topic ones dropped); if they are
    insufficient the query is rewritten and retrieval is retried once. Returns the
    relevant chunks with structured citations (source file, offset, relevance).

    Args:
        query: Natural language query.
        k: Number of chunks to fetch (default 5).
    """
    logger.info(f"[retrieve_from_memory] query='{query}' k={k}")
    try:
        scored = _search(query, k)
        if not scored:
            return "No relevant documents found in memory."

        docs = [d for d, _ in scored]
        grade = _grade(query, docs) if settings.rag_grade else _GradeResult(
            relevant_indices=list(range(1, len(docs) + 1)), sufficient=True
        )

        # CRAG corrective step: rewrite query + re-retrieve once if still insufficient.
        if settings.rag_query_rewrite and not grade.sufficient and grade.rewrite.strip():
            logger.info(f"[retrieve_from_memory] insufficient → rewriting to '{grade.rewrite}'")
            rescored = _search(grade.rewrite, k)
            if rescored:
                scored = rescored
                docs = [d for d, _ in scored]
                grade = _grade(grade.rewrite, docs) if settings.rag_grade else _GradeResult(
                    relevant_indices=list(range(1, len(docs) + 1)), sufficient=True
                )

        keep = set(grade.relevant_indices)
        kept = [(i, scored[i - 1]) for i in sorted(keep) if 1 <= i <= len(scored)]
        if not kept:
            return "No relevant documents found in memory."

        results = []
        for out_i, (_, (doc, score)) in enumerate(kept, 1):
            results.append(f"[{out_i}] {_citation(doc, score)}\n{doc.page_content}")
        return "\n\n---\n\n".join(results)
    except Exception as e:
        logger.error(f"[retrieve_from_memory] error: {e}")
        return f"Memory retrieval failed: {e}"
