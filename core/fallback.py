"""
Fallback Handler — handles cases where the RAG pipeline cannot confidently
answer the user's question.

Three cases:
1. Question is ambiguous (< 5 words or unclear intent) → ask for clarification
2. Already asked for clarification in this session → escalate to helpdesk
3. Question is clear but not found in FAQ → escalate to helpdesk

Run standalone: python -m core.fallback
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.models import Message, Answer, RetrievedChunk


CLARIFICATION_RESPONSE = (
    "Bạn có thể mô tả chi tiết hơn vấn đề không?\n"
    "Ví dụ: màn hình hiển thị thông báo gì, "
    "và bạn đang thao tác ở module nào?"
)

ESCALATION_RESPONSE = (
    "Vấn đề này chưa có trong tài liệu hướng dẫn hiện tại. "
    "Tôi đã ghi nhận câu hỏi của bạn và sẽ chuyển đến "
    "đội hỗ trợ kỹ thuật để xử lý."
)


def handle(message: Message, session_history: list) -> Answer:
    """
    Determine the appropriate fallback response based on the message
    and conversation history.
    Returns an Answer with is_fallback=True.
    """
    # Check if question is ambiguous (less than 5 words)
    word_count = len(message.text.strip().split())
    is_ambiguous = word_count < 5

    # Check if we already asked for clarification in this session
    already_clarified = any(
        isinstance(h, dict) and h.get("text") == CLARIFICATION_RESPONSE
        for h in session_history
    )

    # Case 1: Ambiguous and haven't asked for clarification yet
    if is_ambiguous and not already_clarified:
        print(f"[FALLBACK] Case 1: Ambiguous question ({word_count} words) → asking for clarification")
        return Answer(
            text=CLARIFICATION_RESPONSE,
            confidence=0.0,
            source_chunks=[],
            is_fallback=True,
            rewritten_question="",
        )

    # Case 2 & 3: Already clarified or question is clear but not in FAQ → escalate
    if already_clarified:
        print(f"[FALLBACK] Case 2: Already clarified, still no match → escalating")
    else:
        print(f"[FALLBACK] Case 3: Clear question but not in FAQ → escalating")

    return Answer(
        text=ESCALATION_RESPONSE,
        confidence=0.0,
        source_chunks=[],
        is_fallback=True,
        rewritten_question="",
    )


if __name__ == "__main__":
    import time

    # Test Case 1: ambiguous question, no history
    msg1 = Message(user_id="test", session_id="s1", text="huh??", timestamp=time.time(), platform="web")
    answer1 = handle(msg1, [])
    print(f"[TEST] Input: {msg1.text!r}")
    print(f"[TEST] Response: {answer1.text}")
    print()

    # Test Case 2: already clarified
    msg2 = Message(user_id="test", session_id="s1", text="lỗi gì đó", timestamp=time.time(), platform="web")
    history = [{"text": CLARIFICATION_RESPONSE, "is_fallback": True}]
    answer2 = handle(msg2, history)  # history contains the clarification
    print(f"[TEST] Input: {msg2.text!r} (with clarification in history)")
    print(f"[TEST] Response: {answer2.text}")
    print()

    # Test Case 3: clear question, not in FAQ
    msg3 = Message(user_id="test", session_id="s1", text="làm sao để cài đặt máy in mã vạch cho phòng khám", timestamp=time.time(), platform="web")
    answer3 = handle(msg3, [])
    print(f"[TEST] Input: {msg3.text!r}")
    print(f"[TEST] Response: {answer3.text}")
