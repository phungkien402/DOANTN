"""
Web Adapter — the simplest adapter, used by the Web Chat UI and for testing.

- parse_message: wraps raw JSON directly into a Message
- format_response: returns plain text (no special formatting)
- send_message: no-op (Web uses the HTTP response directly)

Run standalone: python -m adapters.web_adapter
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import time

from adapters.base_adapter import BaseAdapter
from core.models import Message


class WebAdapter(BaseAdapter):

    def parse_message(self, raw: dict) -> Message | None:
        """
        Parse a web chat JSON payload into a Message.
        Expected fields: user_id, text. Optional: session_id.
        """
        user_id = raw.get("user_id", "")
        text = raw.get("text", "")

        if not user_id or not text:
            return None

        session_id = raw.get("session_id", f"web_{user_id}")

        return Message(
            user_id=user_id,
            session_id=session_id,
            text=text,
            timestamp=time.time(),
            platform="web",
        )

    def format_response(self, answer_text: str, confidence: float) -> str:
        """Return plain text — no special formatting for web."""
        return answer_text

    async def send_message(self, user_id: str, text: str) -> None:
        """No-op — web uses HTTP response directly."""
        pass


if __name__ == "__main__":
    adapter = WebAdapter()

    # Test parse — valid
    sample = {
        "user_id": "web_tester",
        "session_id": "s1",
        "text": "how to merge patient records"
    }
    msg = adapter.parse_message(sample)
    print(f"Parsed: {msg}")

    # Test parse — missing text
    assert adapter.parse_message({"user_id": "x"}) is None

    # Test format
    formatted = adapter.format_response("Vào Module Hành chính → Gộp hồ sơ", 0.95)
    print(f"Formatted: {formatted}")

    print("\n✓ WebAdapter works correctly.")
