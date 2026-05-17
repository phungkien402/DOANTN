"""
Telegram Adapter — implements BaseAdapter for the Telegram Bot API.

Handles:
  - Parsing Telegram Update webhook payloads into Message objects
  - Formatting responses (plain text, Telegram supports basic markdown but
    we keep it simple to avoid escaping issues)
  - Sending messages via the Telegram Bot API

Run standalone: python -m adapters.telegram_adapter
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import time
import httpx

from adapters.base_adapter import BaseAdapter
from core.models import Message
from config import TELEGRAM_BOT_TOKEN


class TelegramAdapter(BaseAdapter):

    def __init__(self):
        self._api_base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    def parse_message(self, raw: dict) -> Message | None:
        """
        Parse a Telegram Update object. Only handles text messages.
        Returns None for non-text updates (photos, stickers, edits, etc.)
        """
        msg_data = raw.get("message")
        if not msg_data:
            return None

        text = msg_data.get("text")
        if not text:
            return None

        user_id = str(msg_data.get("from", {}).get("id", ""))
        chat_id = str(msg_data.get("chat", {}).get("id", ""))
        ts = msg_data.get("date", time.time())

        if not user_id:
            return None

        return Message(
            user_id=user_id,
            session_id=f"tg_{chat_id}",
            text=text,
            timestamp=float(ts),
            platform="telegram",
        )

    def format_response(self, answer_text: str, confidence: float) -> str:
        """Format response for Telegram — plain text with confidence footer."""
        if confidence > 0:
            return f"{answer_text}\n\n📊 Độ tin cậy: {confidence:.0%}"
        return answer_text

    async def send_message(self, user_id: str, text: str) -> None:
        """Send message via Telegram Bot API (sendMessage)."""
        if not TELEGRAM_BOT_TOKEN:
            print("[TELEGRAM] No bot token configured, skipping send")
            return

        url = f"{self._api_base}/sendMessage"
        payload = {
            "chat_id": user_id,
            "text": text,
            "parse_mode": "HTML",
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                print(f"[TELEGRAM] Send failed: {resp.status_code} {resp.text}")
            else:
                print(f"[TELEGRAM] Message sent to {user_id}")


if __name__ == "__main__":
    adapter = TelegramAdapter()

    # Test parse
    sample_update = {
        "message": {
            "from": {"id": 12345},
            "chat": {"id": 12345},
            "date": 1700000000,
            "text": "how to merge patient records"
        }
    }
    msg = adapter.parse_message(sample_update)
    print(f"Parsed: {msg}")

    # Test format
    formatted = adapter.format_response("Vào Module Hành chính → Gộp hồ sơ", 0.95)
    print(f"Formatted:\n{formatted}")

    # Test ignore non-text
    non_text = {"message": {"from": {"id": 123}, "chat": {"id": 123}, "photo": []}}
    assert adapter.parse_message(non_text) is None
    print("\n✓ TelegramAdapter works correctly.")
