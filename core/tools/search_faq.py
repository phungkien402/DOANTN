"""
search_faq.py — Full retrieve + rerank helper.

Used by node_full_retriever in the LangGraph agent.
Simple: retrieve(top K) → rerank(top N) → return ranked chunks.

Run standalone: python3 -m core.tools.search_faq
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import RETRIEVER_TOP_K, RERANKER_TOP_N
from core.models import RetrievedChunk
from core import retriever, reranker


def full_retrieve_and_rerank(query: str, top_k: int = None, top_n: int = None) -> tuple[list[RetrievedChunk], float]:
    """
    Full retrieve (top K) → Rerank (top N) → return (ranked_chunks, top_score).
    """
    top_k = top_k or RETRIEVER_TOP_K
    top_n = top_n or RERANKER_TOP_N

    print(f"[SEARCH_FAQ] full_retrieve_and_rerank: query=\"{query}\"")

    # Full retrieve
    chunks = retriever.retrieve(query, top_k=top_k)
    if not chunks:
        print("[SEARCH_FAQ] full_retrieve_and_rerank: no chunks retrieved")
        return [], 0.0

    # Rerank
    ranked_chunks = reranker.rerank(query, chunks, top_n=top_n)
    top_score = ranked_chunks[0].score if ranked_chunks else 0.0

    print(f"[SEARCH_FAQ] full_retrieve_and_rerank: {len(ranked_chunks)} chunks, top_score={top_score:.4f}")
    return ranked_chunks, top_score


if __name__ == "__main__":
    print("=== search_faq.py standalone test ===\n")
    test_query = "không in được phiếu thu"
    chunks, top_score = full_retrieve_and_rerank(test_query)
    print(f"\nResults:")
    print(f"  Query: \"{test_query}\"")
    print(f"  Top score: {top_score:.4f}")
    print(f"  Chunks: {len(chunks)}")
    for i, c in enumerate(chunks, 1):
        print(f"    #{i} score={c.score:.4f} | {c.metadata.get('subject', 'N/A')}")
    print("\n✓ search_faq.py works correctly.")
