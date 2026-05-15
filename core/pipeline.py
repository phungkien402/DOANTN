"""
RAG Pipeline orchestrator.
Accepts a standard Message object, runs it through 5 steps:
  Query Rewriter -> Retriever -> Reranker -> Generator -> Confidence Check
Returns an Answer object or delegates to the Fallback Handler.

Run standalone: python -m core.pipeline
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import RETRIEVER_TOP_K, RERANKER_TOP_N, CONFIDENCE_THRESHOLD, MAINTENANCE_MODE
from core.models import Message, Answer
from core import query_rewriter, retriever, reranker, generator, confidence, fallback
from core.query_rewriter import analyze_intent
from core.generator import GeneratorError

# --- Maintenance mode (toggled at runtime via /admin/maintenance) ---
_maintenance_mode: bool = MAINTENANCE_MODE

MAINTENANCE_MESSAGE = (
    "⚙️ Hệ thống đang bảo trì, vui lòng thử lại sau ít phút. "
    "Xin lỗi vì sự bất tiện này 🙏"
)


def set_maintenance_mode(enabled: bool):
    """Toggle maintenance mode at runtime."""
    global _maintenance_mode
    _maintenance_mode = enabled
    print(f"[PIPELINE] Maintenance mode: {'ON' if enabled else 'OFF'}")


def is_maintenance_mode() -> bool:
    """Check if maintenance mode is active."""
    return _maintenance_mode


def run(message: Message, session_history: list) -> Answer:
    """
    Execute the full RAG pipeline on a message.
    Returns an Answer (either generated or fallback).
    """
    # Short-circuit if maintenance mode is active
    if _maintenance_mode:
        print(f"[PIPELINE] Maintenance mode active — returning maintenance message")
        return Answer(
            text=MAINTENANCE_MESSAGE,
            confidence=0.0,
            source_chunks=[],
            is_fallback=True,
            rewritten_question="",
        )

    print(f"\n{'='*60}")
    print(f"[PIPELINE] Input: \"{message.text}\"")
    print(f"{'='*60}")

    # Step 0: Analyze user intent (silently skip if vLLM unavailable)
    user_intent = analyze_intent(message.text)

    # Step 1: Rewrite the query
    rewritten = query_rewriter.rewrite(message.text)

    # Step 2: Retrieve candidate chunks
    chunks = retriever.retrieve(rewritten, top_k=RETRIEVER_TOP_K)

    if not chunks:
        print("[PIPELINE] No chunks retrieved → fallback")
        return fallback.handle(message, session_history)

    # Step 3: Rerank candidates
    ranked_chunks = reranker.rerank(rewritten, chunks, top_n=RERANKER_TOP_N)

    if not ranked_chunks:
        print("[PIPELINE] No chunks after reranking → fallback")
        return fallback.handle(message, session_history)

    # Step 4: Check confidence
    if not confidence.is_confident(ranked_chunks[0], threshold=CONFIDENCE_THRESHOLD):
        print(f"[PIPELINE] Low confidence ({ranked_chunks[0].score:.4f} < {CONFIDENCE_THRESHOLD}) → fallback")
        return fallback.handle(message, session_history)

    # Step 5: Generate grounded answer
    try:
        answer_text = generator.generate(rewritten, ranked_chunks, session_history, user_intent=user_intent)
    except GeneratorError:
        print("[PIPELINE] Generator failed (vLLM unavailable) → fallback")
        return fallback.handle(message, session_history)

    answer = Answer(
        text=answer_text,
        confidence=ranked_chunks[0].score,
        source_chunks=ranked_chunks,
        is_fallback=False,
        rewritten_question=rewritten,
    )

    print(f"\n[PIPELINE] Done. confidence={answer.confidence:.4f} fallback={answer.is_fallback}")
    return answer


if __name__ == "__main__":
    print("=== EHC RAG Pipeline — Interactive Test ===")
    print("Type a question (or 'quit' to exit):\n")
    while True:
        try:
            q = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q == "quit":
            break
        msg = Message(
            user_id="test", session_id="s1",
            text=q, timestamp=time.time(), platform="web"
        )
        answer = run(msg, [])
        print(f"\nBot: {answer.text}")
        print(f"     [confidence={answer.confidence:.2f}  fallback={answer.is_fallback}]\n")
