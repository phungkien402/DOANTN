"""
Confidence Check — determines whether the top reranker score is high enough
to trust the retrieved context for answer generation.

If confidence is below threshold, the pipeline routes to the fallback handler
instead of generating an answer.

Run standalone: python -m core.confidence
"""

from core.models import RetrievedChunk


def is_confident(top_chunk: RetrievedChunk, threshold: float = 0.4) -> bool:
    """
    Return True if the top chunk's reranker score meets the confidence threshold.
    """
    return top_chunk.score >= threshold


if __name__ == "__main__":
    from core.models import RetrievedChunk

    test_cases = [
        RetrievedChunk(text="...", score=0.94, metadata={}),
        RetrievedChunk(text="...", score=0.40, metadata={}),
        RetrievedChunk(text="...", score=0.38, metadata={}),
        RetrievedChunk(text="...", score=0.10, metadata={}),
    ]
    for chunk in test_cases:
        result = is_confident(chunk)
        status = "CONFIDENT" if result else "FALLBACK"
        print(f"  score={chunk.score:.2f} → {status}")
