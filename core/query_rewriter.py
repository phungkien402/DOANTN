"""
Query Rewriter — extracts the core technical problem from colloquial user messages.

Uses the LLM (via vLLM OpenAI-compatible API) to bridge the gap between how
doctors ask questions (short, informal) and how the FAQ is written (formal).

Run standalone: python -m core.query_rewriter
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI, APIConnectionError

from config import VLLM_BASE_URL, VLLM_MODEL
from core.generator import LLMUnavailableError

# Module-level client — created once when module is first imported
_client = OpenAI(base_url=f"{VLLM_BASE_URL}/v1", api_key="not-needed")

SYSTEM_PROMPT = (
    "You are a query understanding assistant for EHC electronic medical record software support. "
    "Your job is to read a user's message and extract the core technical problem they are experiencing, "
    "expressed as a short, clear issue statement that matches how FAQ entries are written.\n\n"
    "Rules:\n"
    "1. Focus on WHAT the problem is, not HOW the user described it.\n"
    "2. Output a short statement (1 sentence), not a question.\n"
    "3. Use technical terms that would appear in a FAQ title.\n"
    "4. If the message is already a clear technical question, keep it as-is.\n"
    "5. Output in Vietnamese. Return only the rewritten query — no explanation.\n"
    "6. Prefix selection:\n"
    "   - Use \"Lỗi...\" ONLY when the input describes a system error, crash, malfunction, or "
    "unexpected behavior (e.g. \"bị lỗi\", \"không lên\", \"xoay hoài\", \"bắt update\").\n"
    "   - Use \"Cách...\" when the input is a how-to or navigation question "
    "(e.g. \"bấm vào đâu\", \"làm sao\", \"ở đâu\", \"muốn làm\").\n"
    "   - Use \"Vị trí...\" when asking where something is located in the UI."
)

FEW_SHOT_EXAMPLES = [
    ("merge patient records how?", "Cách gộp mã bệnh nhân"),
    ("xử trí cứ xoay hoài không dừng", "Lỗi màn hình xoay mãi khi xử trí bệnh nhân"),
    ("phần mềm bắt update mới vô được", "Lỗi phần mềm bắt buộc cập nhật mới đăng nhập được"),
    ("in phiếu không lên form view làm sao", "Lỗi in phiếu không hiển thị form view"),
    ("in giấy ra viện lại ở đâu", "Vị trí in lại giấy ra viện trong hệ thống"),
    ("xem giúp tôi hẹn bệnh nhân nhưng hệ thống tự nhảy vào thứ 7 chủ nhật", "Lỗi module đặt hẹn tự chọn sai ngày trong tuần"),
    ("BN ra viện rồi muốn sửa thông tin", "Cách sửa thông tin bệnh nhân sau khi đã xử trí ra viện"),
    ("muốn hủy nhập viện thì bấm vào đâu", "Cách hủy nhập viện trong hệ thống EHC"),
]


def _build_messages(text: str) -> list[dict]:
    """Build the message list with system prompt and few-shot examples."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for user_ex, assistant_ex in FEW_SHOT_EXAMPLES:
        messages.append({"role": "user", "content": user_ex})
        messages.append({"role": "assistant", "content": assistant_ex})
    messages.append({"role": "user", "content": text})
    return messages


INTENT_PROMPT = (
    "Bạn là trợ lý phân tích ý định người dùng cho phần mềm bệnh án điện tử EHC.\n"
    "Đọc tin nhắn của người dùng và mô tả ngắn gọn (1 câu) vấn đề họ đang gặp phải.\n"
    "Viết ở ngôi thứ 3, ví dụ: \"Bác sĩ đang gặp lỗi màn hình xoay liên tục khi xử trí bệnh nhân.\"\n"
    "Chỉ trả về 1 câu mô tả — không giải thích thêm."
)

CONTEXTUAL_INTENT_PROMPT = (
    "Bạn là trợ lý hiểu phần mềm EHC. Dựa vào tài liệu tham khảo, "
    "hãy mô tả ngắn gọn vấn đề người dùng đang gặp (1 câu).\n"
    "Viết ở ngôi thứ 3, ví dụ: \"Bác sĩ đang gặp lỗi màn hình xoay liên tục khi xử trí bệnh nhân.\"\n"
    "Chỉ trả về 1 câu mô tả — không giải thích thêm."
)


def analyze_intent(query: str, chunks: list = None) -> str | None:
    """
    Analyze user intent — returns a 1-sentence Vietnamese description of the
    user's problem for internal use (injected into generator prompt).

    If chunks are provided, uses them as context for grounded intent analysis.
    If not, falls back to blind analysis (no context).
    Returns None if vLLM is unavailable.
    """
    if chunks:
        print(f"[INTENT] Contextual analysis with {len(chunks)} chunks: \"{query}\"")
        # Build context from chunks
        chunk_texts = []
        for i, c in enumerate(chunks, 1):
            subject = c.metadata.get("subject", "")
            text = c.text or c.metadata.get("description", "")
            chunk_texts.append(f"{i}. {subject}: {text}")
        context_block = "\n".join(chunk_texts)

        user_content = f"Tài liệu tham khảo:\n{context_block}\n\nCâu hỏi: {query}"
        system_prompt = CONTEXTUAL_INTENT_PROMPT
    else:
        print(f"[INTENT] Blind analysis (no chunks): \"{query}\"")
        user_content = query
        system_prompt = INTENT_PROMPT

    try:
        response = _client.chat.completions.create(
            model=VLLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=100,
            temperature=0.1,
        )

        intent = response.choices[0].message.content.strip()
        print(f"[INTENT] Result: \"{intent}\"")
        return intent

    except APIConnectionError as e:
        # Retry once after 1s — vLLM may be busy
        print(f"[INTENT] Connection error, retrying in 1s...")
        time.sleep(1)
        try:
            response = _client.chat.completions.create(
                model=VLLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=100,
                temperature=0.1,
            )
            intent = response.choices[0].message.content.strip()
            print(f"[INTENT] Result (retry): \"{intent}\"")
            return intent
        except Exception:
            print(f"[INTENT] Retry failed, skipping intent analysis")
            return None

    except Exception as e:
        print(f"[INTENT] vLLM unavailable ({type(e).__name__}), skipping intent analysis")
        return None


def rewrite(text: str) -> str:
    """
    Rewrite a colloquial question into a clear intent statement.
    Returns the rewritten query string.
    If vLLM is unavailable, returns the original text as-is.
    """
    print(f"[REWRITER] Original : \"{text}\"")

    try:
        response = _client.chat.completions.create(
            model=VLLM_MODEL,
            messages=_build_messages(text),
            max_tokens=150,
            temperature=0.1,
        )

        rewritten = response.choices[0].message.content.strip()
        print(f"[REWRITER] Rewritten: \"{rewritten}\"")
        return rewritten

    except APIConnectionError as e:
        # Retry once after 1s — vLLM may be busy
        print(f"[REWRITER] Connection error, retrying in 1s...")
        time.sleep(1)
        try:
            response = _client.chat.completions.create(
                model=VLLM_MODEL,
                messages=_build_messages(text),
                max_tokens=150,
                temperature=0.1,
            )
            rewritten = response.choices[0].message.content.strip()
            print(f"[REWRITER] Rewritten (retry): \"{rewritten}\"")
            return rewritten
        except Exception as retry_e:
            print(f"[REWRITER] Retry failed ({type(retry_e).__name__}), raising LLMUnavailableError")
            raise LLMUnavailableError(str(retry_e)) from retry_e

    except Exception as e:
        print(f"[REWRITER] vLLM unavailable ({type(e).__name__}), using original query")
        return text


ANALYZE_AND_REWRITE_PROMPT = (
    "Bạn là trợ lý EHC. Dựa vào tài liệu tham khảo (nếu có), hãy thực hiện 2 việc:\n"
    "1. Mô tả ngắn gọn vấn đề người dùng đang gặp (1 câu, ngôi thứ 3)\n"
    "2. Viết lại câu hỏi thành query tìm kiếm ngắn gọn, formal tiếng Việt\n"
    "Trả về đúng 2 dòng theo format:\n"
    "INTENT: <mô tả vấn đề>\n"
    "QUERY: <query tìm kiếm>"
)


def _parse_intent_and_query(response_text: str, original_query: str) -> tuple[str | None, str]:
    """Parse the INTENT:/QUERY: response format. Returns (intent, rewritten_query)."""
    intent = None
    rewritten = original_query

    for line in response_text.strip().splitlines():
        line = line.strip()
        if line.upper().startswith("INTENT:"):
            intent = line[len("INTENT:"):].strip()
        elif line.upper().startswith("QUERY:"):
            rewritten = line[len("QUERY:"):].strip()

    # If rewritten is empty after parsing, fall back to original
    if not rewritten:
        rewritten = original_query

    return intent, rewritten


def analyze_and_rewrite(query: str, chunks: list = None) -> tuple[str | None, str]:
    """
    Combined intent analysis + query rewrite in a single vLLM call.
    Returns (intent, rewritten_query).

    If chunks are provided, injects them as context for grounded analysis.
    Graceful degradation: returns (None, original_query) if vLLM is unavailable.
    """
    print(f"[ANALYZE+REWRITE] Query: \"{query}\"")

    # Build user content with optional chunk context
    if chunks:
        print(f"[ANALYZE+REWRITE] Using {len(chunks)} chunks as context")
        chunk_texts = []
        for i, c in enumerate(chunks, 1):
            subject = c.metadata.get("subject", "")
            text = c.text or c.metadata.get("description", "")
            chunk_texts.append(f"{i}. {subject}: {text}")
        context_block = "\n".join(chunk_texts)
        user_content = f"Tài liệu tham khảo:\n{context_block}\n\nCâu hỏi: {query}"
    else:
        print(f"[ANALYZE+REWRITE] No chunks (blind mode)")
        user_content = f"Câu hỏi: {query}"

    messages = [
        {"role": "system", "content": ANALYZE_AND_REWRITE_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        response = _client.chat.completions.create(
            model=VLLM_MODEL,
            messages=messages,
            max_tokens=200,
            temperature=0.1,
        )

        raw = response.choices[0].message.content.strip()
        intent, rewritten = _parse_intent_and_query(raw, query)
        print(f"[ANALYZE+REWRITE] Intent: \"{intent}\"")
        print(f"[ANALYZE+REWRITE] Rewritten: \"{rewritten}\"")
        return intent, rewritten

    except APIConnectionError:
        # Retry once after 1s — vLLM may be busy
        print(f"[ANALYZE+REWRITE] Connection error, retrying in 1s...")
        time.sleep(1)
        try:
            response = _client.chat.completions.create(
                model=VLLM_MODEL,
                messages=messages,
                max_tokens=200,
                temperature=0.1,
            )
            raw = response.choices[0].message.content.strip()
            intent, rewritten = _parse_intent_and_query(raw, query)
            print(f"[ANALYZE+REWRITE] Intent (retry): \"{intent}\"")
            print(f"[ANALYZE+REWRITE] Rewritten (retry): \"{rewritten}\"")
            return intent, rewritten
        except Exception as retry_e:
            print(f"[ANALYZE+REWRITE] Retry failed ({type(retry_e).__name__}), raising LLMUnavailableError")
            raise LLMUnavailableError(str(retry_e)) from retry_e

    except Exception as e:
        print(f"[ANALYZE+REWRITE] vLLM unavailable ({type(e).__name__}), using original query")
        return None, query


if __name__ == "__main__":
    test_queries = [
        "merge patient records how?",
        "in phiếu khám ở đâu",
        "thuốc hết tồn kho làm sao",
        "xử trí cứ xoay hoài không dừng",
        "phần mềm bắt update mới vô được",
    ]
    for q in test_queries:
        result = rewrite(q)
        print()
