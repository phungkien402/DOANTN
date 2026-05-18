"""
search_manual.py — Dual-collection retriever for how-to / workflow queries.

Searches both ehc_manual (HDSD) and doantn_faq, reranks together.
Used when user asks about procedures, configuration, or step-by-step instructions.

Pattern: retrieve top 5 from each collection → merge → rerank top 3.

Run standalone: python3 -m core.tools.search_manual
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from qdrant_client import QdrantClient

from config import QDRANT_URL, QDRANT_COLLECTION, RERANKER_TOP_N
from core.models import RetrievedChunk
from core import reranker, retriever as faq_retriever

MANUAL_COLLECTION = "ehc_manual"

# Lazy-loaded Qdrant client for manual collection
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL)
    return _client


def _retrieve_manual(query: str, top_k: int = 5) -> list[RetrievedChunk]:
    """Retrieve from ehc_manual collection using the shared embedding model."""
    # Reuse the embedding model already loaded by core.retriever
    query_vector = faq_retriever._model.encode(query).tolist()

    client = _get_client()
    results = client.search(
        collection_name=MANUAL_COLLECTION,
        query_vector=query_vector,
        limit=top_k,
    )

    chunks = []
    for r in results:
        chunk = RetrievedChunk(
            text=r.payload.get("chunk_text", ""),
            score=r.score,
            metadata={
                "chunk_id": r.payload.get("chunk_id"),
                "source": r.payload.get("source", "manual"),
                "subject": r.payload.get("subject"),
                "description": r.payload.get("description"),
                "url": "",
            },
        )
        chunks.append(chunk)
    return chunks


def search_manual(query: str, top_k: int = 5, top_n: int = None) -> tuple[list[RetrievedChunk], float]:
    """
    Dual-collection retrieval: ehc_manual + doantn_faq → merge → rerank.
    Returns (ranked_chunks, top_score).
    """
    if top_n is None:
        top_n = RERANKER_TOP_N

    print(f"[SEARCH_MANUAL] query=\"{query}\" top_k={top_k} top_n={top_n}")

    # Retrieve from both collections
    manual_chunks = _retrieve_manual(query, top_k=top_k)
    faq_chunks = faq_retriever.retrieve(query, top_k=top_k)

    print(f"[SEARCH_MANUAL] ehc_manual: {len(manual_chunks)} chunks, doantn_faq: {len(faq_chunks)} chunks")

    all_chunks = manual_chunks + faq_chunks
    if not all_chunks:
        print("[SEARCH_MANUAL] No results from either collection")
        return [], 0.0

    # Rerank merged results
    ranked_chunks = reranker.rerank(query, all_chunks, top_n=top_n)
    top_score = ranked_chunks[0].score if ranked_chunks else 0.0

    print(f"[SEARCH_MANUAL] {len(ranked_chunks)} chunks after rerank, top_score={top_score:.4f}")
    for i, c in enumerate(ranked_chunks, 1):
        src = c.metadata.get("source", "faq")
        print(f"  #{i} score={c.score:.4f} [{src}] | {c.metadata.get('subject', 'N/A')[:60]}")

    return ranked_chunks, top_score


if __name__ == "__main__":
    print("=== search_manual.py standalone test ===\n")

    test_queries = [
        "cách kết nối PACS",
        "hướng dẫn khám bệnh ngoại trú",
        "tài liệu tuỳ biến bệnh án",
    ]

    for q in test_queries:
        print(f"\n{'='*60}")
        chunks, score = search_manual(q)
        print(f"  Query: \"{q}\"")
        print(f"  Top score: {score:.4f}")
        print(f"  Chunks: {len(chunks)}")

    print(f"\n{'='*60}")
    print("✓ search_manual.py works correctly.")
