"""
Debug Query — traces a single question through the entire RAG pipeline,
showing intermediate results at each step. The most useful tool for
investigating failing eval cases.

Usage:
    python -m tests.debug_query "record locked what do"
    python -m tests.debug_query "xử trí cứ xoay hoài"
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import RETRIEVER_TOP_K, RERANKER_TOP_N, CONFIDENCE_THRESHOLD
from core.models import Message
from core import query_rewriter, retriever, reranker, generator, confidence, fallback


def debug(question: str) -> None:
    """Run a question through each pipeline step with verbose output."""
    print()
    print("=" * 70)
    print(f"  DEBUG: \"{question}\"")
    print("=" * 70)

    # --- Step 1: Query Rewriter ---
    print()
    print("[REWRITER]")
    start = time.time()
    rewritten = query_rewriter.rewrite(question)
    rewriter_time = time.time() - start
    print(f"  Original : \"{question}\"")
    print(f"  Rewritten: \"{rewritten}\"")
    print(f"  Time     : {rewriter_time:.2f}s")

    # --- Step 2: Retriever ---
    print()
    print(f"[RETRIEVER] Top {RETRIEVER_TOP_K} chunks:")
    start = time.time()
    chunks = retriever.retrieve(rewritten, top_k=RETRIEVER_TOP_K)
    retriever_time = time.time() - start

    if not chunks:
        print("  (no chunks retrieved)")
        print()
        print("[DIAGNOSIS]")
        print("  No chunks were retrieved from Qdrant.")
        print("  Possible causes:")
        print("    1. The collection is empty — run data ingestion first.")
        print("    2. The query is too far from any FAQ content.")
        return

    for i, c in enumerate(chunks, 1):
        subject = c.metadata.get("subject", "N/A")
        text_preview = c.text[:80].replace("\n", " ")
        print(f"  #{i:<3} sim={c.score:.4f} | {subject}")
        print(f"       \"{text_preview}...\"")

    print(f"  Time: {retriever_time:.2f}s")

    # --- Step 3: Reranker ---
    print()
    print(f"[RERANKER] Top {RERANKER_TOP_N} after reranking:")
    start = time.time()
    ranked_chunks = reranker.rerank(rewritten, chunks, top_n=RERANKER_TOP_N)
    reranker_time = time.time() - start

    if not ranked_chunks:
        print("  (no chunks after reranking)")
        return

    for i, c in enumerate(ranked_chunks, 1):
        subject = c.metadata.get("subject", "N/A")
        marker = " ← TOP" if i == 1 else ""
        print(f"  #{i:<3} score={c.score:.4f} | {subject}{marker}")

    print(f"  Time: {reranker_time:.2f}s")

    # --- Step 4: Confidence Check ---
    print()
    top_score = ranked_chunks[0].score
    is_conf = confidence.is_confident(ranked_chunks[0], threshold=CONFIDENCE_THRESHOLD)
    status = "PASS" if is_conf else "FALLBACK"
    print(f"[CONFIDENCE] {top_score:.4f} {'≥' if is_conf else '<'} {CONFIDENCE_THRESHOLD} threshold → {status}")

    # --- Step 5: Generate or Fallback ---
    print()
    if not is_conf:
        print("[FALLBACK]")
        msg = Message(
            user_id="debug", session_id="debug",
            text=question, timestamp=time.time(), platform="web",
        )
        answer = fallback.handle(msg, [])
        print(f"  Response: \"{answer.text}\"")
        print()
        print("[DIAGNOSIS]")
        print(f"  The top chunk scored {top_score:.4f}, below threshold {CONFIDENCE_THRESHOLD}.")
        top_subject = ranked_chunks[0].metadata.get("subject", "unknown")
        print(f"  Top chunk subject: \"{top_subject}\"")
        print("  Possible fixes:")
        print("    1. Expand the FAQ entry description — add more context and keywords.")
        print(f"    2. Lower CONFIDENCE_THRESHOLD to {top_score - 0.05:.2f} and re-evaluate.")
        print("    3. Add synonym mappings in the rewriter prompt.")
    else:
        print("[GENERATOR]")
        start = time.time()
        answer_text = generator.generate(rewritten, ranked_chunks)
        gen_time = time.time() - start
        print(f"  Response: \"{answer_text}\"")
        print(f"  Time: {gen_time:.2f}s")

    # --- Timing Summary ---
    print()
    total = rewriter_time + retriever_time + reranker_time
    print(f"[TIMING] rewriter={rewriter_time:.2f}s  retriever={retriever_time:.2f}s  reranker={reranker_time:.2f}s  total={total:.2f}s")


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m tests.debug_query \"your question here\"")
        print()
        print("Examples:")
        print("  python -m tests.debug_query \"Kiosk bị lỗi màn hình đen\"")
        print("  python -m tests.debug_query \"xử trí cứ xoay hoài không dừng\"")
        print("  python -m tests.debug_query \"cài đặt VPN cho bệnh viện\"")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    debug(question)


if __name__ == "__main__":
    main()
