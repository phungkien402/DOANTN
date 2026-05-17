"""
Shared data models used across the entire pipeline.
All modules import from here — this is the single source of truth for data shapes.
"""

from dataclasses import dataclass, field


@dataclass
class Message:
    """A normalized incoming message from any platform."""
    user_id: str
    session_id: str
    text: str
    timestamp: float
    platform: str  # "zalo", "telegram", "web"


@dataclass
class RetrievedChunk:
    """A single chunk retrieved from Qdrant, scored by the reranker."""
    text: str
    score: float  # reranker score (final relevance score)
    metadata: dict = field(default_factory=dict)  # {issue_id, subject, project, url}


@dataclass
class Answer:
    """The final response returned by the pipeline."""
    text: str
    confidence: float  # 0.0 – 1.0, from top reranker score
    source_chunks: list[RetrievedChunk] = field(default_factory=list)
    is_fallback: bool = False
    rewritten_question: str = ""  # what the query rewriter produced


if __name__ == "__main__":
    # Quick sanity check
    msg = Message(
        user_id="test", session_id="s1",
        text="how to merge patient records",
        timestamp=0.0, platform="web"
    )
    chunk = RetrievedChunk(
        text="Merge patient records: Go to Administration...",
        score=0.94,
        metadata={"issue_id": 123, "subject": "Merge patients"}
    )
    answer = Answer(
        text="To merge records, go to...",
        confidence=0.94,
        source_chunks=[chunk],
        is_fallback=False
    )
    print(f"Message : {msg}")
    print(f"Chunk   : {chunk}")
    print(f"Answer  : {answer}")
    print("\n✓ All models instantiate correctly.")
