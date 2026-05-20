"""
Zalo OA Adapter — implements BaseAdapter for the Zalo Official Account API.

Handles:
  - Parsing Zalo webhook events (only "user_send_text")
  - HMAC-SHA256 signature verification using ZALO_OA_SECRET
  - Sending messages via Zalo OA API

Run standalone: python -m adapters.zalo_adapter
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import hashlib
import hmac
import time
import httpx

from adapters.base_adapter import BaseAdapter
from core.models import Message
from config import ZALO_OA_SECRET, ZALO_ACCESS_TOKEN


class ZaloAdapter(BaseAdapter):

    ZALO_API_URL = "https://openapi.zalo.me/v3.0/oa/message/cs"

    def parse_message(self, raw: dict) -> Message | None:
        """
        Parse a Zalo webhook event. Only processes 'user_send_text'.
        Returns None for other event types (follow, unfollow, seen, etc.)
        """
        event_name = raw.get("event_name")
        if event_name != "user_send_text":
            return None

        sender = raw.get("sender", {})
        user_id = sender.get("id", "")
        message = raw.get("message", {})
        text = message.get("text", "")
        ts = raw.get("timestamp", str(int(time.time() * 1000)))

        if not user_id or not text:
            return None

        return Message(
            user_id=user_id,
            session_id=f"zalo_{user_id}",
            text=text,
            timestamp=float(ts) / 1000.0 if len(str(ts)) > 10 else float(ts),
            platform="zalo",
        )

    def format_response(self, answer_text: str, confidence: float) -> str:
        """Format response for Zalo — plain text."""
        if confidence > 0:
            return f"{answer_text}\n\n📊 Độ tin cậy: {confidence:.0%}"
        return answer_text

    async def send_message(self, user_id: str, text: str) -> None:
        """Send message via Zalo OA API (cs message)."""
        if not ZALO_ACCESS_TOKEN:
            print("[ZALO] No access token configured, skipping send")
            return

        headers = {
            "Content-Type": "application/json",
            "access_token": ZALO_ACCESS_TOKEN,
        }
        payload = {
            "recipient": {"user_id": user_id},
            "message": {"text": text},
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(self.ZALO_API_URL, json=payload, headers=headers)
            if resp.status_code != 200:
                print(f"[ZALO] Send failed: {resp.status_code} {resp.text}")
            else:
                data = resp.json()
                if data.get("error") != 0:
                    print(f"[ZALO] API error: {data}")
                else:
                    print(f"[ZALO] Message sent to {user_id}")

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify HMAC-SHA256 signature from Zalo webhook.
        Returns True if signature matches, False otherwise.
        """
        if not ZALO_OA_SECRET:
            print("[ZALO] No OA secret configured, skipping verification")
            return True  # Allow in dev mode

        expected = hmac.new(
            ZALO_OA_SECRET.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)


if __name__ == "__main__":
    adapter = ZaloAdapter()

    # Test parse — valid event
    sample_event = {
        "event_name": "user_send_text",
        "sender": {"id": "user_zalo_123"},
        "message": {"text": "in bảng kê khám bệnh ở đâu"},
        "timestamp": "1700000000000"
    }
    msg = adapter.parse_message(sample_event)
    print(f"Parsed: {msg}")

    # Test parse — ignored event
    follow_event = {"event_name": "follow", "follower": {"id": "user_zalo_456"}}
    assert adapter.parse_message(follow_event) is None

    # Test format
    formatted = adapter.format_response("Vào Module Viện phí → In bảng kê", 0.89)
    print(f"Formatted:\n{formatted}")

    print("\n✓ ZaloAdapter works correctly.")
