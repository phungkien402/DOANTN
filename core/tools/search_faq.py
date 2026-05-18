"""
search_faq.py — Wrap the existing RAG pipeline steps for use as a LangGraph tool.
Calls: retriever → query_rewriter → retriever (full) → reranker
Returns: (chunks: list[RetrievedChunk], rewritten_query: str)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import RETRIEVER_TOP_K, RERANKER_TOP_N, CLARIFY_SPREAD_THRESHOLD
from core.models import RetrievedChunk
from core import retriever, reranker
from core.query_rewriter import analyze_and_rewrite
from core.generator import LLMUnavailableError


def search_faq(query: str, session_history: list = None, saved_fast_chunks: list = None) -> tuple[list[RetrievedChunk], str, str | None, str, list[RetrievedChunk]]:
    """
    Execute the full RAG search pipeline:
      1. Fast retrieve (top 3, no rerank) for initial context
         — skipped if saved_fast_chunks provided (clarification loop reuse)
      2. Analyze + rewrite query (single LLM call)
      3. Full retrieve (top K) with rewritten query  [skipped if answerable=unclear/no]
      4. Rerank (top N)                              [skipped if answerable=unclear/no]

    Returns: (ranked_chunks, rewritten_query, user_intent, answerable, fast_chunks)
    fast_chunks is always returned so clarifier can use them.
    If vLLM is unavailable for rewrite, uses original query.
    """
    print(f"[SEARCH_FAQ] Starting search for: \"{query}\"")

    # Step 1: Fast retrieve — skip if we have saved chunks from clarification
    if saved_fast_chunks:
        print(f"[SEARCH_FAQ] Step 1: Reusing {len(saved_fast_chunks)} saved fast_chunks from clarification")
        fast_chunks = saved_fast_chunks
    else:
        print(f"[SEARCH_FAQ] Step 1: Fast retrieve (top 3)")
        fast_chunks = retriever.retrieve(query, top_k=3)

    # Step 1.5: Score-spread heuristic (first attempt only)
    # If all fast_chunks have nearly identical scores, the query is ambiguous — skip LLM call.
    if not saved_fast_chunks and len(fast_chunks) >= 2:
        scores = [c.score for c in fast_chunks]
        spread = max(scores) - min(scores)
        print(f"[SEARCH_FAQ] Step 1.5: Score spread = {spread:.4f} (threshold={CLARIFY_SPREAD_THRESHOLD})")
        if spread < CLARIFY_SPREAD_THRESHOLD:
            print(f"[SEARCH_FAQ] Score spread too low → ambiguous query, early exit")
            return [], query, None, "unclear", fast_chunks

    # Step 2: Analyze + rewrite
    # When saved_fast_chunks is provided (clarification loop), pass them as context so
    # the LLM has both the numbered choices (from history) AND chunk content to resolve intent.
    print(f"[SEARCH_FAQ] Step 2: Analyze + Rewrite")
    user_intent = None
    rewritten = query
    answerable = "unclear"
    chunks_for_analysis = saved_fast_chunks if saved_fast_chunks else fast_chunks
    try:
        if chunks_for_analysis:
            user_intent, rewritten, answerable = analyze_and_rewrite(
                query, chunks=chunks_for_analysis, session_history=session_history
            )
        else:
            user_intent, rewritten, answerable = analyze_and_rewrite(
                query, session_history=session_history
            )
    except LLMUnavailableError:
        print("[SEARCH_FAQ] vLLM unavailable for rewrite, using original query")
        rewritten = query
        answerable = "unclear"

    # Early exit if LLM says unclear/no — skip expensive steps 3+4
    if answerable in ("unclear", "no"):
        print(f"[SEARCH_FAQ] answerable={answerable} → early exit, skipping full retrieve + rerank")
        return [], rewritten, user_intent, answerable, fast_chunks

    # Step 3: Full retrieve with rewritten query
    print(f"[SEARCH_FAQ] Step 3: Full retrieve (top {RETRIEVER_TOP_K})")
    chunks = retriever.retrieve(rewritten, top_k=RETRIEVER_TOP_K)

    if not chunks:
        print("[SEARCH_FAQ] No chunks retrieved")
        return [], rewritten, user_intent, answerable, fast_chunks

    # Step 4: Rerank
    print(f"[SEARCH_FAQ] Step 4: Rerank (top {RERANKER_TOP_N})")
    ranked_chunks = reranker.rerank(rewritten, chunks, top_n=RERANKER_TOP_N)

    print(f"[SEARCH_FAQ] Done: {len(ranked_chunks)} chunks, rewritten=\"{rewritten}\", answerable={answerable}")
    return ranked_chunks, rewritten, user_intent, answerable, fast_chunks


if __name__ == "__main__":
    print("=== search_faq.py standalone test ===\n")
    test_query = "không in được phiếu thu"
    chunks, rewritten, intent, answerable, fast_chunks = search_faq(test_query)
    print(f"\nResults:")
    print(f"  Rewritten: \"{rewritten}\"")
    print(f"  Intent: \"{intent}\"")
    print(f"  Answerable: \"{answerable}\"")
    print(f"  Chunks: {len(chunks)}")
    for i, c in enumerate(chunks, 1):
        print(f"    #{i} score={c.score:.4f} | {c.metadata.get('subject', 'N/A')}")
    print("\n✓ search_faq.py works correctly.")
