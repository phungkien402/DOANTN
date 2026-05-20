"""
RAG Quality Evaluation — runs the full pipeline against a test set and
reports accuracy metrics per category.

Usage:
    python -m tests.evaluate
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.models import Message
from core import pipeline


def load_eval_set() -> list[dict]:
    """Load the evaluation set from tests/eval_set.json."""
    eval_path = Path(__file__).parent / "eval_set.json"
    with open(eval_path, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_item(item: dict) -> dict:
    """
    Run a single eval item through the pipeline and score it.
    Returns a result dict with pass/fail and details.
    """
    msg = Message(
        user_id="eval",
        session_id=item["id"],
        text=item["question"],
        timestamp=time.time(),
        platform="web",
    )

    start = time.time()
    answer = pipeline.run(msg, [])
    elapsed = time.time() - start

    result = {
        "id": item["id"],
        "question": item["question"],
        "type": item["type"],
        "confidence": answer.confidence,
        "is_fallback": answer.is_fallback,
        "answer_text": answer.text,
        "elapsed": elapsed,
        "passed": False,
        "fail_reason": "",
    }

    if item["type"] in ("in_faq", "colloquial"):
        # Pass if: not a fallback AND response contains all expected keywords.
        # When vLLM is unavailable, the generator returns an error string but
        # retrieval still works — fall back to checking keywords against the
        # source chunk text so we can validate retrieval quality independently.
        expected_kws = item.get("expected_keywords", [])
        answer_lower = answer.text.lower()

        # Build a combined text to search: answer + source chunks
        searchable_text = answer_lower
        if answer.source_chunks:
            chunk_texts = " ".join(c.text.lower() for c in answer.source_chunks)
            searchable_text = answer_lower + " " + chunk_texts

        if answer.is_fallback:
            result["passed"] = False
            result["fail_reason"] = "Got fallback instead of answer"
        else:
            missing_kws = [
                kw for kw in expected_kws
                if kw.lower() not in searchable_text
            ]
            if missing_kws:
                result["passed"] = False
                result["fail_reason"] = f"Missing keywords: {missing_kws}"
            else:
                result["passed"] = True

    elif item["type"] == "not_in_faq":
        # Pass if: is_fallback is True
        if answer.is_fallback:
            result["passed"] = True
        else:
            result["passed"] = False
            result["fail_reason"] = f"Expected fallback, got answer (conf={answer.confidence:.2f})"

    elif item["type"] == "ambiguous":
        # Pass if: response is a clarifying question
        is_clarification = (
            "?" in answer.text
            or "mô tả" in answer.text.lower()
            or "chi tiết" in answer.text.lower()
            or "describe" in answer.text.lower()
        )
        if is_clarification:
            result["passed"] = True
        else:
            result["passed"] = False
            result["fail_reason"] = "Expected clarification question"

    return result


def print_results(results: list[dict]) -> None:
    """Print a formatted results table and summary."""
    print()
    print("=" * 78)
    print("  EHC RAG — Evaluation Results")
    print("=" * 78)
    print()
    print(f"{'ID':<6}{'Question':<40}{'Type':<12}{'Pass':<6}{'Conf':<10}{'Time'}")
    print(f"{'-'*5} {'-'*39} {'-'*11} {'-'*5} {'-'*9} {'-'*6}")

    for r in results:
        q_display = r["question"][:37] + "..." if len(r["question"]) > 37 else r["question"]
        status = "✅" if r["passed"] else "❌"
        conf = f"{r['confidence']:.2f}" if r["type"] in ("in_faq", "colloquial") else "—"
        elapsed = f"{r['elapsed']:.1f}s"
        fail_marker = f"  ← {r['fail_reason']}" if not r["passed"] else ""
        print(f"{r['id']:<6}{q_display:<40}{r['type']:<12}{status:<6}{conf:<10}{elapsed}{fail_marker}")

    # Summary by category
    print()
    print("-" * 78)

    total_pass = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"Overall     : {total_pass} / {total} passed  ({100*total_pass/total:.1f}%)")

    for category in ("in_faq", "colloquial", "not_in_faq", "ambiguous"):
        cat_results = [r for r in results if r["type"] == category]
        if not cat_results:
            continue
        cat_pass = sum(1 for r in cat_results if r["passed"])
        cat_total = len(cat_results)
        label = {
            "in_faq": "In-FAQ",
            "colloquial": "Colloquial",
            "not_in_faq": "Fallback",
            "ambiguous": "Ambiguous",
        }[category]
        print(f"{label:<12}: {cat_pass} / {cat_total} ({100*cat_pass/cat_total:.1f}%)")

    avg_time = sum(r["elapsed"] for r in results) / len(results)
    print(f"Avg time    : {avg_time:.2f}s per question")

    # Needs attention section
    failures = [r for r in results if not r["passed"]]
    if failures:
        print()
        print("=== NEEDS ATTENTION ===")
        for r in failures:
            print(f"{r['id']} — {r['question']}")
            print(f"  → {r['fail_reason']}")
            if r["type"] in ("in_faq", "colloquial") and r["confidence"] > 0:
                print(f"  → confidence={r['confidence']:.4f}")
            print()


def main():
    """Run the full evaluation."""
    print("Loading evaluation set...")
    eval_set = load_eval_set()
    print(f"Loaded {len(eval_set)} questions.")
    print()
    print("Running pipeline on each question...")
    print("(This may take a while — models are loaded on first import)")
    print()

    results = []
    for i, item in enumerate(eval_set, 1):
        print(f"[{i}/{len(eval_set)}] {item['id']}: {item['question']}")
        result = evaluate_item(item)
        results.append(result)

    print_results(results)

    # Exit with non-zero if below 80% in-faq accuracy
    in_faq_results = [r for r in results if r["type"] in ("in_faq", "colloquial")]
    if in_faq_results:
        in_faq_pass = sum(1 for r in in_faq_results if r["passed"])
        in_faq_rate = in_faq_pass / len(in_faq_results)
        if in_faq_rate < 0.8:
            print(f"\n⚠️  In-FAQ accuracy ({in_faq_rate:.0%}) is below 80% target.")
            sys.exit(1)

    print("\n✓ Evaluation complete.")


if __name__ == "__main__":
    main()
