"""
Intent guard:
1. LLM classifier: is query EHC-related? (YES/NO, max_tokens=5)
2. If NO → LLM chat fallback (short, polite, scoped to avoid going off-rail)

Uses the same synchronous OpenAI client pattern as the rest of the codebase.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI, APIConnectionError

from config import VLLM_BASE_URL, VLLM_MODEL

# Module-level client — same pattern as generator.py / query_rewriter.py
_client = OpenAI(base_url=f"{VLLM_BASE_URL}/v1", api_key="not-needed")

CLASSIFY_PROMPT = """Bạn là bộ phân loại câu hỏi. Nhiệm vụ: xác định câu hỏi sau có liên quan đến phần mềm quản lý bệnh viện (HIS/EMR/EHC) hay không.

Trả lời CHỈ bằng một từ: YES hoặc NO.
- YES: câu hỏi về phần mềm, tính năng, lỗi, hướng dẫn sử dụng HIS/EHC
- NO: chào hỏi, cảm ơn, hỏi thăm, câu hỏi không liên quan phần mềm

Câu hỏi: "{query}"
"""

CHAT_SYSTEM_PROMPT = """Bạn là trợ lý ảo của phần mềm EHC — hệ thống quản lý bệnh viện.
Người dùng đang nhắn tin ngoài phạm vi hỗ trợ phần mềm.
Hãy trả lời ngắn gọn, lịch sự, thân thiện.
Không tư vấn y tế. Không giải thích kỹ thuật ngoài phần mềm EHC.
Nếu phù hợp, nhắc nhẹ rằng bạn chuyên hỗ trợ phần mềm EHC.
Giới hạn tối đa 2-3 câu."""

# Fallback response if LLM chat fails
_FALLBACK_RESPONSE = "Xin chào! Mình là trợ lý hỗ trợ phần mềm EHC. Bạn có câu hỏi gì về phần mềm không?"


def classify(query: str) -> bool:
    """Return True if query is off-topic (not EHC-related)."""
    try:
        response = _client.chat.completions.create(
            model=VLLM_MODEL,
            messages=[{"role": "user", "content": CLASSIFY_PROMPT.format(query=query.strip())}],
            max_tokens=5,
            temperature=0.0,
        )
        answer = response.choices[0].message.content.strip().upper()
        print(f"[INTENT_GUARD] Classify: \"{query}\" → {answer}")
        return answer.startswith("NO")

    except APIConnectionError:
        # Retry once after 1s
        print("[INTENT_GUARD] Classifier connection error, retrying in 1s...")
        time.sleep(1)
        try:
            response = _client.chat.completions.create(
                model=VLLM_MODEL,
                messages=[{"role": "user", "content": CLASSIFY_PROMPT.format(query=query.strip())}],
                max_tokens=5,
                temperature=0.0,
            )
            answer = response.choices[0].message.content.strip().upper()
            print(f"[INTENT_GUARD] Classify (retry): \"{query}\" → {answer}")
            return answer.startswith("NO")
        except Exception as e:
            print(f"[INTENT_GUARD] Classifier retry failed: {e} — allowing query through")
            return False  # fail open

    except Exception as e:
        print(f"[INTENT_GUARD] Classifier failed: {e} — allowing query through")
        return False  # fail open


def chat_fallback(query: str) -> str:
    """Generate a short, scoped chat response for off-topic queries."""
    try:
        response = _client.chat.completions.create(
            model=VLLM_MODEL,
            messages=[
                {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            max_tokens=150,
            temperature=0.7,
        )
        result = response.choices[0].message.content.strip()
        print(f"[INTENT_GUARD] Chat fallback: \"{result}\"")
        return result

    except APIConnectionError:
        # Retry once after 1s
        print("[INTENT_GUARD] Chat fallback connection error, retrying in 1s...")
        time.sleep(1)
        try:
            response = _client.chat.completions.create(
                model=VLLM_MODEL,
                messages=[
                    {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ],
                max_tokens=150,
                temperature=0.7,
            )
            result = response.choices[0].message.content.strip()
            print(f"[INTENT_GUARD] Chat fallback (retry): \"{result}\"")
            return result
        except Exception as e:
            print(f"[INTENT_GUARD] Chat fallback retry failed: {e}")
            return _FALLBACK_RESPONSE

    except Exception as e:
        print(f"[INTENT_GUARD] Chat fallback failed: {e}")
        return _FALLBACK_RESPONSE
