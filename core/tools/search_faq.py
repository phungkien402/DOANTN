"""
search_faq.py — Wrap the existing RAG pipeline steps for use as a LangGraph tool.
Calls: retriever → query_rewriter → retriever (full) → reranker
Returns: (chunks: list[RetrievedChunk], rewritten_query: str)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import RETRIEVER_TOP_K, RERANKER_TOP_N
from core.models import RetrievedChunk
from core import retriever, reranker
from core.query_rewriter import analyze_and_rewrite
from core.generator import LLMUnavailableError


def search_faq(query: str) -> tuple[list[RetrievedChunk], str, str | None]:
    """
    Execute the full RAG search pipeline:
      1. Fast retrieve (top 3, no rerank) for initial context
      2. Analyze + rewrite query (single LLM call)
      3. Full retrieve (top K) with rewritten query
      4. Rerank (top N)

    Returns: (ranked_chunks, rewritten_query, user_intent)
    If vLLM is unavailable for rewrite, uses original query.
    """
    print(f"[SEARCH_FAQ] Starting search for: \"{query}\"")

    # Step 1: Fast retrieve — top 3 for context
    print(f"[SEARCH_FAQ] Step 1: Fast retrieve (top 3)")
    fast_chunks = retriever.retrieve(query, top_k=3)

    # Step 2: Analyze + rewrite
    print(f"[SEARCH_FAQ] Step 2: Analyze + Rewrite")
    user_intent = None
    rewritten = query
    try:
        if fast_chunks:
            user_intent, rewritten = analyze_and_rewrite(query, chunks=fast_chunks)
        else:
            user_intent, rewritten = analyze_and_rewrite(query)
    except LLMUnavailableError:
        print("[SEARCH_FAQ] vLLM unavailable for rewrite, using original query")
        rewritten = query

    # Step 3: Full retrieve with rewritten query
    print(f"[SEARCH_FAQ] Step 3: Full retrieve (top {RETRIEVER_TOP_K})")
    chunks = retriever.retrieve(rewritten, top_k=RETRIEVER_TOP_K)

    if not chunks:
        print("[SEARCH_FAQ] No chunks retrieved")
        return [], rewritten, user_intent

    # Step 4: Rerank
    print(f"[SEARCH_FAQ] Step 4: Rerank (top {RERANKER_TOP_N})")
    ranked_chunks = reranker.rerank(rewritten, chunks, top_n=RERANKER_TOP_N)

    print(f"[SEARCH_FAQ] Done: {len(ranked_chunks)} chunks, rewritten=\"{rewritten}\"")
    return ranked_chunks, rewritten, user_intent


if __name__ == "__main__":
    print("=== search_faq.py standalone test ===\n")
    test_query = "không in được phiếu thu"
    chunks, rewritten, intent = search_faq(test_query)
    print(f"\nResults:")
    print(f"  Rewritten: \"{rewritten}\"")
    print(f"  Intent: \"{intent}\"")
    print(f"  Chunks: {len(chunks)}")
    for i, c in enumerate(chunks, 1):
        print(f"    #{i} score={c.score:.4f} | {c.metadata.get('subject', 'N/A')}")
    print("\n✓ search_faq.py works correctly.")
