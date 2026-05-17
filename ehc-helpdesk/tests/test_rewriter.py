"""
Rewriter test suite — evaluates query rewriter quality independently from the full pipeline.

Sends test inputs through the rewriter and checks that expected keywords
appear in the output. This validates that the LLM is extracting the correct
technical intent from colloquial user messages.

Usage:
    python -m tests.test_rewriter
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.query_rewriter import rewrite

# Test cases: input, expected keywords (all must appear, case-insensitive), description
TEST_CASES = [
    {
        "input": "xử trí cứ xoay hoài không dừng",
        "expected_keywords": ["xử trí", "xoay"],
        "description": "Colloquial: spinning screen during discharge",
    },
    {
        "input": "phần mềm bắt update mới vô được",
        "expected_keywords": ["cập nhật"],
        "description": "Colloquial: forced update before login",
    },
    {
        "input": "in phiếu không lên form view làm sao",
        "expected_keywords": ["in", "form view"],
        "description": "Colloquial: print form not showing",
    },
    {
        "input": "in giấy ra viện lại ở đâu",
        "expected_keywords": ["in", "ra viện"],
        "description": "Colloquial: where to reprint discharge paper",
    },
    {
        "input": "BN ra viện rồi muốn sửa thông tin",
        "expected_keywords": ["sửa", "ra viện"],
        "description": "Colloquial: edit info after discharge",
    },
    {
        "input": "thuốc đã kê muốn sửa liều",
        "expected_keywords": ["sửa", "liều"],
        "description": "Colloquial: modify prescribed dosage",
    },
    {
        "input": "Kiosk bị lỗi màn hình đen phải làm sao",
        "expected_keywords": ["kiosk", "màn hình đen"],
        "description": "Bug description: kiosk black screen",
    },
    {
        "input": "máy xét nghiệm không đổ kết quả về phần mềm",
        "expected_keywords": ["xét nghiệm", "kết quả"],
        "description": "Bug description: lab results not syncing",
    },
    {
        "input": "không xóa được phiếu đã chỉ định",
        "expected_keywords": ["xóa", "phiếu"],
        "description": "Formal: already clear technical question — should stay similar",
    },
    {
        "input": "lỗi cập nhật viện phí khi chuyển đối tượng bệnh nhân",
        "expected_keywords": ["viện phí", "chuyển đối tượng"],
        "description": "Formal: fee update error on patient type change",
    },
    {
        "input": "xem giúp tôi hẹn bệnh nhân nhưng hệ thống tự nhảy vào thứ 7 chủ nhật",
        "expected_keywords": ["đặt hẹn", "ngày"],
        "description": "Multi-clause: appointment scheduling picks wrong day",
    },
    {
        "input": "merge patient records how?",
        "expected_keywords": ["gộp"],
        "description": "English input: merge patient records",
    },
]


def run_tests() -> None:
    """Run all rewriter test cases and print results."""
    print("=" * 70)
    print("  QUERY REWRITER TEST SUITE")
    print("=" * 70)
    print(f"  Test cases: {len(TEST_CASES)}")
    print(f"  Model: vLLM (via query_rewriter.rewrite)")
    print("=" * 70)
    print()

    passed = 0
    failed = 0
    results = []

    for i, case in enumerate(TEST_CASES, 1):
        input_text = case["input"]
        expected = case["expected_keywords"]
        description = case["description"]

        print(f"[{i:02d}] {description}")
        print(f"     Input:    \"{input_text}\"")

        rewritten = rewrite(input_text)
        rewritten_lower = rewritten.lower()

        # Check all expected keywords appear (case-insensitive)
        missing = [kw for kw in expected if kw.lower() not in rewritten_lower]

        if not missing:
            status = "✓ PASS"
            passed += 1
        else:
            status = "✗ FAIL"
            failed += 1

        print(f"     Output:   \"{rewritten}\"")
        print(f"     Expected: {expected}")
        if missing:
            print(f"     Missing:  {missing}")
        print(f"     Result:   {status}")
        print()

        results.append({
            "case": i,
            "description": description,
            "input": input_text,
            "output": rewritten,
            "passed": not missing,
            "missing": missing,
        })

    # Summary
    total = passed + failed
    score = passed / total * 100 if total else 0

    print("=" * 70)
    print(f"  RESULTS: {passed}/{total} passed ({score:.0f}%)")
    print(f"  Passed: {passed}  |  Failed: {failed}")
    print("=" * 70)

    if failed:
        print("\n  Failed cases:")
        for r in results:
            if not r["passed"]:
                print(f"    [{r['case']:02d}] {r['description']}")
                print(f"         Input:   \"{r['input']}\"")
                print(f"         Output:  \"{r['output']}\"")
                print(f"         Missing: {r['missing']}")


if __name__ == "__main__":
    run_tests()
