"""
search_manual.py — Retrieve from ehc_manual collection (HDSD docs).

Pattern: retrieve top 10 → rerank top 3 → return (chunks, top_score).
No clarification loop — straightforward retrieval.

Run standalone: python3 -m core.tools.search_manual
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient

from config import QDRANT_URL, EMBED_MODEL
from core.models import RetrievedChunk
from core import reranker

MANUAL_COLLECTION = "ehc_manual"

# Module-level singletons
_model = None
_client = None


def _get_model():
    global _model
    if _model is None:
        print(f"[SEARCH_MANUAL] Loading embedding model: {EMBED_MODEL}")
        _model = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _model


def _get_client():
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL)
    return _client


def search_manual(query: str, top_k: int = 10, top_n: int = 3) -> tuple[list[RetrievedChunk], float]:
    """
    Retrieve from ehc_manual collection: top_k retrieve → top_n rerank.
    Returns (ranked_chunks, top_score).
    """
    print(f"[SEARCH_MANUAL] query=\"{query}\" top_k={top_k} top_n={top_n}")

    model = _get_model()
    client = _get_client()

    # Embed query
    query_vector = model.encode(query).tolist()

    # Search Qdrant
    results = client.search(
        collection_name=MANUAL_COLLECTION,
        query_vector=query_vector,
        limit=top_k,
    )

    if not results:
        print("[SEARCH_MANUAL] No results found")
        return [], 0.0

    # Convert to RetrievedChunk
    chunks = []
    for r in results:
        chunk = RetrievedChunk(
            text=r.payload.get("chunk_text", ""),
            score=r.score,
            metadata={
                "chunk_id": r.payload.get("chunk_id"),
                "source": r.payload.get("source"),
                "subject": r.payload.get("subject"),
                "description": r.payload.get("description"),
                "url": "",
            },
        )
        chunks.append(chunk)

    print(f"[SEARCH_MANUAL] Retrieved {len(chunks)} chunks, reranking to top {top_n}...")

    # Rerank
    ranked_chunks = reranker.rerank(query, chunks, top_n=top_n)
    top_score = ranked_chunks[0].score if ranked_chunks else 0.0

    print(f"[SEARCH_MANUAL] {len(ranked_chunks)} chunks after rerank, top_score={top_score:.4f}")
    for i, c in enumerate(ranked_chunks, 1):
        print(f"  #{i} score={c.score:.4f} | {c.metadata.get('subject', 'N/A')}")

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
