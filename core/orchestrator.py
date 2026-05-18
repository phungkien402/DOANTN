"""
orchestrator.py — LLM Orchestrator node.

Takes: query + fast_chunks (top 3) + session_history
Returns: {
    "action": "answer" | "clarify" | "ticket",
    "reasoning": str,
    "search_query": str,      # if action=answer: use this for full retrieve
    "clarify_message": str,   # if action=clarify: send this to user
}

Replaces: score-spread heuristic, clarification_count routing, Block X node.

Run standalone: python3 -m core.orchestrator
"""

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI, APIConnectionError

from config import VLLM_BASE_URL, VLLM_MODEL

# Module-level client — same pattern as query_rewriter.py
_client = OpenAI(base_url=f"{VLLM_BASE_URL}/v1", api_key="not-needed")

ORCHESTRATOR_PROMPT = """Bạn là bộ não của hệ thống hỗ trợ phần mềm EHC (quản lý bệnh viện).

Nhiệm vụ: Đọc câu hỏi của người dùng, lịch sử hội thoại, và 3 đoạn FAQ tìm được. Quyết định hành động tiếp theo.

---
LỊCH SỬ HỘI THOẠI:
{history}

---
CÂU HỎI HIỆN TẠI: {query}

---
3 ĐOẠN FAQ TÌM ĐƯỢC (theo thứ tự liên quan):
{chunks}

---
HƯỚNG DẪN QUYẾT ĐỊNH:

1. Nếu từ câu hỏi + lịch sử + FAQ, bạn đủ tự tin xác định được vấn đề cụ thể người dùng gặp phải:
   → action = "answer"
   → search_query = câu truy vấn tối ưu để tìm kiếm câu trả lời (tiếng Việt, cụ thể, bỏ từ thừa)

2. Nếu câu hỏi mơ hồ, nhiều FAQ có thể phù hợp, cần xác nhận thêm từ người dùng:
   → action = "clarify"
   → clarify_message = câu hỏi lại ngắn gọn, liệt kê các trường hợp có thể (dùng danh sách đánh số), hỏi người dùng thuộc trường hợp nào hoặc mô tả thêm

3. Nếu FAQ không liên quan đến vấn đề người dùng đang hỏi, hoặc vấn đề quá đặc thù không có trong tài liệu:
   → action = "ticket"

LƯU Ý QUAN TRỌNG:
- Nếu lịch sử cho thấy đã hỏi lại 1 lần rồi, ưu tiên action="answer" hoặc "ticket", KHÔNG hỏi lại lần 3.
- search_query phải bằng tiếng Việt, cụ thể, phản ánh đúng vấn đề người dùng.
- clarify_message phải ngắn, thân thiện, kết thúc bằng "Nếu không có trường hợp nào phù hợp, bạn có thể mô tả chi tiết vấn đề bằng lời của mình."

---
TRẢ LỜI THEO ĐỊNH DẠNG JSON (không giải thích thêm):
{{
  "action": "answer" | "clarify" | "ticket",
  "reasoning": "lý do ngắn gọn",
  "search_query": "...",
  "clarify_message": "..."
}}"""


def _format_chunks(chunks) -> str:
    """Format fast_chunks for the orchestrator prompt."""
    if not chunks:
        return "(không có)"
    lines = []
    for i, c in enumerate(chunks, 1):
        subject = c.metadata.get("subject", "") if hasattr(c, "metadata") else ""
        text_preview = (c.text or "")[:100] if hasattr(c, "text") else str(c)[:100]
        title = subject or text_preview
        lines.append(f"{i}. {title}")
    return "\n".join(lines)


def _format_history(session_history: list) -> str:
    """Format session history for the orchestrator prompt."""
    if not session_history:
        return "(không có)"
    lines = []
    for turn in session_history[-4:]:  # last 4 turns max
        role = "Người dùng" if turn.get("role") == "user" else "Bot"
        text = turn.get("text", turn.get("content", ""))[:150]
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def orchestrate(query: str, fast_chunks: list, session_history: list = None) -> dict:
    """
    Call the LLM to decide the next action.

    Returns dict with keys: action, reasoning, search_query, clarify_message.
    Fallback to {"action": "answer", "search_query": query} on any error.
    """
    prompt = ORCHESTRATOR_PROMPT.format(
        query=query,
        chunks=_format_chunks(fast_chunks),
        history=_format_history(session_history or []),
    )

    print(f"[ORCHESTRATOR] Query: \"{query}\"")

    messages = [{"role": "user", "content": prompt}]

    try:
        response = _client.chat.completions.create(
            model=VLLM_MODEL,
            messages=messages,
            max_tokens=300,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
    except APIConnectionError:
        # Retry once after 1s
        print("[ORCHESTRATOR] Connection error, retrying in 1s...")
        time.sleep(1)
        try:
            response = _client.chat.completions.create(
                model=VLLM_MODEL,
                messages=messages,
                max_tokens=300,
                temperature=0.1,
            )
            raw = response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[ORCHESTRATOR] Retry failed: {e} → fallback to answer")
            return _fallback_result(query)
    except Exception as e:
        print(f"[ORCHESTRATOR] LLM failed: {e} → fallback to answer")
        return _fallback_result(query)

    print(f"[ORCHESTRATOR] Raw output: {raw[:200]}")

    # Parse JSON — extract from markdown code block if wrapped
    return _parse_response(raw, query)


def _parse_response(raw: str, query: str) -> dict:
    """Parse the LLM JSON response into a structured dict."""
    try:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON found in response")
        result = json.loads(match.group())
        action = result.get("action", "answer")
        if action not in ("answer", "clarify", "ticket"):
            action = "answer"
        result["action"] = action
        result.setdefault("search_query", query)
        result.setdefault("clarify_message", "")
        result.setdefault("reasoning", "")
        print(f"[ORCHESTRATOR] Action={action} | reasoning=\"{result['reasoning'][:80]}\"")
        return result
    except Exception as e:
        print(f"[ORCHESTRATOR] Parse error: {e} → fallback to answer")
        return _fallback_result(query)


def _fallback_result(query: str) -> dict:
    """Return a safe fallback when orchestrator fails."""
    return {
        "action": "answer",
        "reasoning": "orchestrator fallback",
        "search_query": query,
        "clarify_message": "",
    }


if __name__ == "__main__":
    print("=== Orchestrator standalone test ===\n")

    # Simulate a RetrievedChunk-like object for testing
    class FakeChunk:
        def __init__(self, text, subject):
            self.text = text
            self.metadata = {"subject": subject}
            self.score = 0.5

    fake_chunks = [
        FakeChunk("Lỗi in phiếu thu...", "Lỗi in phiếu thu không hiển thị"),
        FakeChunk("Cách in phiếu thu...", "Cách in phiếu thu từ module viện phí"),
        FakeChunk("Lỗi máy in...", "Lỗi máy in không kết nối"),
    ]

    # Test 1: ambiguous query
    print("--- Test 1: Ambiguous query ---")
    result = orchestrate("không in được", fake_chunks, [])
    print(f"Result: {json.dumps(result, ensure_ascii=False, indent=2)}\n")

    # Test 2: clear query
    print("--- Test 2: Clear query ---")
    result = orchestrate("lỗi in phiếu thu không hiển thị form view", fake_chunks, [])
    print(f"Result: {json.dumps(result, ensure_ascii=False, indent=2)}\n")

    # Test 3: with history (already clarified once)
    print("--- Test 3: With clarification history ---")
    history = [
        {"role": "user", "text": "không in được"},
        {"role": "bot", "text": "Bạn đang gặp vấn đề nào?\n1. Lỗi in phiếu thu\n2. Cách in phiếu thu\n3. Lỗi máy in"},
        {"role": "user", "text": "1"},
    ]
    result = orchestrate("1", fake_chunks, history)
    print(f"Result: {json.dumps(result, ensure_ascii=False, indent=2)}\n")

    print("✓ Orchestrator tests completed.")
