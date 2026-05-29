"""
RAG pipeline evaluation using RAGAS.

Metrics computed:
  - faithfulness        : are claims in the answer supported by retrieved context?
  - answer_relevancy    : how relevant is the answer to the question?
  - context_recall      : does the retrieved context cover the ground-truth answer?
  - context_precision   : how much of the retrieved context is actually relevant?

Usage:
    python -m eval.rag_eval --dataset eval/data/rag_testset.json

Dataset format (JSON list):
    [
      {
        "question": "What is LangGraph?",
        "ground_truth": "LangGraph is a library for building stateful multi-actor applications...",
        "contexts": ["...retrieved chunk 1...", "...retrieved chunk 2..."],
        "answer": "...agent's answer..."
      },
      ...
    ]

If no dataset is provided, a small synthetic one is generated from the vector store.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger


def _load_dataset(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _generate_synthetic_dataset(n: int = 5) -> list[dict[str, Any]]:
    """Generate a minimal synthetic test set from the vector store for smoke testing."""
    from memory import get_retriever
    from agents import get_llm
    from langchain_core.messages import HumanMessage

    questions = [
        "What is LangGraph and how does it work?",
        "How does RAG retrieval work in this system?",
        "What tools does the researcher agent have access to?",
        "How are MCP servers integrated?",
        "What is the role of the supervisor agent?",
    ][:n]

    retriever = get_retriever(k=3)
    llm = get_llm()
    dataset = []

    for q in questions:
        docs = retriever.invoke(q)
        contexts = [d.page_content for d in docs]
        if not contexts:
            continue
        answer_msg = llm.invoke([HumanMessage(content=q)])
        dataset.append({
            "question": q,
            "ground_truth": "",   # no ground truth for synthetic
            "contexts": contexts,
            "answer": answer_msg.content,
        })

    return dataset


def run_ragas_eval(dataset: list[dict], output_path: str | None = None) -> dict:
    """
    Run RAGAS evaluation on a dataset.

    Args:
        dataset: List of {question, ground_truth, contexts, answer} dicts.
        output_path: If set, save results JSON here.

    Returns:
        Dict of metric_name → score.
    """
    try:
        from ragas import evaluate  # type: ignore
        from ragas.metrics import (  # type: ignore
            faithfulness,
            answer_relevancy,
            context_recall,
            context_precision,
        )
        from datasets import Dataset  # type: ignore
    except ImportError as e:
        logger.error(f"RAGAS not installed: {e}. Run: pip install ragas datasets")
        return {}

    # RAGAS expects a HuggingFace Dataset
    hf_dataset = Dataset.from_list(dataset)

    metrics = [faithfulness, answer_relevancy]
    # context_recall and context_precision require ground_truth
    if any(row.get("ground_truth") for row in dataset):
        metrics += [context_recall, context_precision]

    logger.info(f"Running RAGAS on {len(dataset)} samples with {len(metrics)} metrics…")

    try:
        result = evaluate(hf_dataset, metrics=metrics)
        scores = {k: float(v) for k, v in result.items() if isinstance(v, (int, float))}
    except Exception as e:
        logger.error(f"RAGAS evaluation failed: {e}")
        return {}

    logger.info("RAGAS results:")
    for metric, score in scores.items():
        logger.info(f"  {metric}: {score:.3f}")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(scores, f, indent=2)
        logger.info(f"Results saved to {output_path}")

    return scores


def main():
    import argparse

    parser = argparse.ArgumentParser(description="RAGAS RAG evaluation")
    parser.add_argument("--dataset", type=str, help="Path to test dataset JSON")
    parser.add_argument("--output", type=str, default="eval/results/rag_eval.json")
    parser.add_argument("--synthetic-n", type=int, default=5,
                        help="Number of synthetic samples if no dataset provided")
    args = parser.parse_args()

    if args.dataset:
        dataset = _load_dataset(args.dataset)
        logger.info(f"Loaded {len(dataset)} samples from {args.dataset}")
    else:
        logger.info(f"No dataset provided — generating {args.synthetic_n} synthetic samples")
        dataset = _generate_synthetic_dataset(n=args.synthetic_n)

    if not dataset:
        logger.warning("Dataset is empty — nothing to evaluate.")
        return

    run_ragas_eval(dataset, output_path=args.output)


if __name__ == "__main__":
    main()
