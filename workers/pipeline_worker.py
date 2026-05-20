"""
pipeline_worker.py — RQ worker that processes queued Telegram queries.
Runs the LangGraph agent and sends the reply back via Telegram Bot API.

Start worker with:
    rtk rq worker ehc-queue --url redis://localhost:6379
"""

import sys
import time
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def process_telegram_query(chat_id: str, text: str, session_id: str, history: list):
    """
    RQ job: run LangGraph agent and send reply to Telegram.
    Called by the RQ worker, not by FastAPI directly.
    """
    from core.models import Message
    from core.langgraph_agent import run

    msg = Message(
        user_id=chat_id,
        session_id=session_id,
        text=text,
        timestamp=time.time(),
        platform="telegram",
    )

    try:
        answer = run(msg, history)
        reply_text = answer.text
        confidence = answer.confidence
    except Exception as e:
        print(f"[WORKER] Agent error: {e}")
        reply_text = "⚠️ Hệ thống đang bận, vui lòng thử lại sau."
        confidence = 0.0

    # Append confidence badge
    if confidence >= 0.4:
        reply_text += f"\n\n🟢 Độ tin cậy: {confidence*100:.0f}%"
    elif reply_text and not reply_text.startswith("⚠️"):
        reply_text += "\n\n🔴 Độ tin cậy: thấp"

    _send_telegram(chat_id, reply_text)
    print(f"[WORKER] Done | chat_id={chat_id} | conf={confidence:.4f}")


def _send_telegram(chat_id: str, text: str):
    """Send a message via Telegram Bot API."""
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if not resp.ok:
            print(f"[WORKER] Telegram send failed: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"[WORKER] Telegram send error: {e}")
