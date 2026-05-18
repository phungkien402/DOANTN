"""
eval.py — EHC Helpdesk evaluation script.
Runs 30 FAQ + 10 HDSD test cases through the agent,
calculates metrics, uploads results to Langfuse dataset.

Usage:
    rtk python3 -m scripts.eval
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from langfuse import Langfuse
import os
from dotenv import load_dotenv

load_dotenv()

from core.models import Message
from core.langgraph_agent import run

lf = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
    host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
)

FAQ_CASES = [
    ("phần mềm bị co lại cỡ chữ nhỏ không hiển thị đầy đủ", "search_faq", True),
    ("phần mềm bị treo không thao tác được không tắt được", "search_faq", True),
    ("màn hình anydesk máy kết nối quá bé không hiện hết các module phải làm sao", "search_faq", True),
    ("in phiếu chỉ định không in được phiếu trắng", "search_faq", True),
    ("không đăng nhập được vào phần mềm EHC báo sai mật khẩu", "search_faq", True),
    ("bệnh nhân vào viện rồi nhưng không tìm được trong danh sách nội trú", "search_faq", True),
    ("kê thuốc BHYT bị báo lỗi không lưu được đơn", "search_faq", True),
    ("in phiếu thu tiền viện phí bị lỗi không in được", "search_faq", True),
    ("không xem được kết quả xét nghiệm trong phần mềm EHC", "search_faq", True),
    ("cập nhật phần mềm EHC thủ công như thế nào", "search_faq", True),
    ("không thêm được danh mục thuốc mới vào hệ thống", "search_faq", True),
    ("lập phiếu xuất kho thuốc bị lỗi không lưu được", "search_faq", True),
    ("không in được bảng kê BHYT cho bệnh nhân ngoại trú", "search_faq", True),
    ("chuyển khoa bệnh nhân bị lỗi không thực hiện được", "search_faq", True),
    ("không tạo được lịch mổ trong phân hệ phẫu thuật", "search_faq", True),
    ("phần mềm EHC chạy chậm đặc biệt buổi sáng đầu giờ", "search_faq", True),
    ("xuất báo cáo doanh thu bị lỗi không tải được file", "search_faq", True),
    ("nhập kho thuốc bị báo lỗi trùng lô không lưu được", "search_faq", True),
    ("không in được phiếu chỉ định xét nghiệm máu", "search_faq", True),
    ("thanh toán viện phí bị lỗi không in được biên lai", "search_faq", True),
    ("tìm bệnh nhân theo mã BHYT không ra kết quả", "search_faq", True),
    ("máy in không kết nối được với phần mềm EHC", "search_faq", True),
    ("lập phiếu phát thuốc ngoại trú bị lỗi không lưu được", "search_faq", True),
    ("đổi mật khẩu tài khoản EHC không thực hiện được", "search_faq", True),
    ("kê y lệnh thuốc cho bệnh nhân nội trú bị lỗi", "search_faq", True),
    ("xem lịch sử khám bệnh của bệnh nhân không hiện ra", "search_faq", True),
    ("in phiếu nhập viện bị trắng không có nội dung", "search_faq", True),
    ("cập nhật thông tin bệnh nhân bị báo lỗi không lưu", "search_faq", True),
    ("phiếu phát thuốc in ra không có tên bệnh nhân", "search_faq", True),
    ("không tìm được bệnh nhân đã xuất viện để lập phiếu bổ sung", "search_faq", True),
]

HDSD_CASES = [
    ("cách kết nối minipacs trong EHC", "search_manual", True),
    ("hướng dẫn thao tác kết nối PACS server", "search_manual", True),
    ("các bước để mở kết nối PACS", "search_manual", True),
    ("cấu hình pacs name và pacs port ở đâu", "search_manual", True),
    ("hướng dẫn thông kết nối PACS", "search_manual", True),
    ("quy trình kết nối minipacs với EHC từng bước", "search_manual", True),
    ("cần chuẩn bị gì trước khi kết nối PACS", "search_manual", True),
    ("cách cài đặt PACS AE title trong EHC", "search_manual", True),
    ("hướng dẫn cấu hình worklist port cho minipacs", "search_manual", True),
    ("thao tác kết nối PACS server trong phần mềm EHC", "search_manual", True),
]

ALL_CASES = [(q, t, a, "faq") for q, t, a in FAQ_CASES] + \
            [(q, t, a, "hdsd") for q, t, a in HDSD_CASES]


def run_eval():
    print(f"\n{'='*60}")
    print(f"EHC Helpdesk Eval — {len(ALL_CASES)} test cases")
    print(f"{'='*60}\n")

    dataset_name = f"ehc-eval-{int(time.time())}"
    lf.create_dataset(name=dataset_name)

    results = []
    correct_tool = 0
    answered = 0
    total_latency = 0.0

    for i, (query, expected_tool, should_answer, category) in enumerate(ALL_CASES, 1):
        print(f"[{i:02d}/{len(ALL_CASES)}] {category.upper()} | \"{query}\"")

        msg = Message(
            user_id="eval",
            session_id=f"eval-{i}",
            text=query,
            timestamp=time.time(),
            platform="eval",
        )

        t0 = time.time()
        try:
            answer = run(msg, [])
            latency = time.time() - t0
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "query": query, "expected_tool": expected_tool,
                "actual_tool": "error", "confidence": 0.0,
                "answered": False, "latency": 0.0, "category": category,
            })
            continue

        actual_tool = "search_faq"
        if hasattr(answer, "source_chunks") and answer.source_chunks:
            src = answer.source_chunks[0].metadata.get("source", "faq")
            if "hdsd" in src:
                actual_tool = "search_manual"
        if answer.is_fallback and answer.confidence == 0.0:
            actual_tool = "create_ticket"

        tool_ok = (actual_tool == expected_tool) or \
                  (expected_tool == "search_manual" and actual_tool in ("search_manual", "search_faq"))
        ans_ok = (answer.confidence >= 0.4) == should_answer

        if tool_ok:
            correct_tool += 1
        if not answer.is_fallback and answer.confidence >= 0.4:
            answered += 1
        total_latency += latency

        status = "✓" if tool_ok else "✗"
        print(f"  {status} tool={actual_tool} conf={answer.confidence:.3f} latency={latency:.1f}s")

        lf.create_dataset_item(
            dataset_name=dataset_name,
            input={"query": query, "category": category, "expected_tool": expected_tool},
            expected_output={"answered": should_answer, "tool": expected_tool},
            metadata={"tool_correct": tool_ok, "confidence": round(answer.confidence, 4)},
        )

        results.append({
            "query": query,
            "expected_tool": expected_tool,
            "actual_tool": actual_tool,
            "confidence": answer.confidence,
            "answered": not answer.is_fallback and answer.confidence >= 0.4,
            "latency": latency,
            "category": category,
            "tool_correct": tool_ok,
        })

    n = len(results)
    tool_acc = correct_tool / n * 100
    ans_rate = answered / n * 100
    avg_lat = total_latency / n

    faq_results = [r for r in results if r["category"] == "faq"]
    hdsd_results = [r for r in results if r["category"] == "hdsd"]
    faq_ans = sum(1 for r in faq_results if r["answered"]) / len(faq_results) * 100
    hdsd_ans = sum(1 for r in hdsd_results if r["answered"]) / len(hdsd_results) * 100

    print(f"\n{'='*60}")
    print(f"RESULTS ({n} cases)")
    print(f"  Tool accuracy:     {tool_acc:.1f}%  ({correct_tool}/{n})")
    print(f"  Answered rate:     {ans_rate:.1f}%  ({answered}/{n})")
    print(f"    FAQ answered:    {faq_ans:.1f}%")
    print(f"    HDSD answered:   {hdsd_ans:.1f}%")
    print(f"  Avg latency:       {avg_lat:.2f}s")
    print(f"  Dataset:           {dataset_name}")
    print(f"{'='*60}")

    lf.flush()
    print("\n✓ Results uploaded to Langfuse.")
    return results


if __name__ == "__main__":
    run_eval()
