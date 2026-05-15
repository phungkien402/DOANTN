"""
FastAPI application routes.

POST /webhook/{platform}  — receive messages from Zalo / Telegram / Web
GET  /health              — health check
GET  /admin/logs          — view unanswered / fallback query logs
POST /admin/reindex       — trigger a fresh data pull from Redmine

Run: uvicorn api.routes:app --host 0.0.0.0 --port 8080
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from config import SESSION_MAX_TURNS
from core.models import Message
from core.pipeline import run as run_pipeline
from api.session import SessionManager
from api.logger import QueryLogger
from adapters.telegram_adapter import TelegramAdapter
from adapters.zalo_adapter import ZaloAdapter
from adapters.web_adapter import WebAdapter
from adapters.slack_adapter import SlackAdapter

app = FastAPI(title="EHC AI Helpdesk")

# CORS for web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared instances
_session_mgr = SessionManager(max_turns=SESSION_MAX_TURNS)
_logger = QueryLogger()

# Adapter registry
_adapters = {
    "telegram": TelegramAdapter(),
    "zalo": ZaloAdapter(),
    "web": WebAdapter(),
    "slack": SlackAdapter(),
}

# Slack event deduplication
_processed_slack_events: set[str] = set()


@app.get("/health")
def health():
    """Health check endpoint."""
    from datetime import datetime, timezone
    return {"status": "ok", "service": "ehc-helpdesk", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/")
def serve_ui():
    """Serve the web chat UI."""
    ui_path = Path(__file__).parent.parent / "ui" / "index.html"
    if ui_path.exists():
        return FileResponse(str(ui_path))
    return JSONResponse({"error": "UI not found"}, status_code=404)


@app.post("/webhook/{platform}")
async def handle_webhook(platform: str, request: Request, background_tasks: BackgroundTasks):
    """
    Unified webhook handler for all platforms.
    Selects the appropriate adapter, parses the message, runs the pipeline,
    logs the query, and sends the response back.
    """
    # Validate platform
    adapter = _adapters.get(platform)
    if adapter is None:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")

    # Parse raw payload
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Handle Slack URL verification challenge
    if platform == "slack" and raw.get("type") == "url_verification":
        return {"challenge": raw.get("challenge")}

    # Slack deduplication — ignore retried events
    if platform == "slack":
        event_id = raw.get("event_id", "")
        if event_id and event_id in _processed_slack_events:
            return {"status": "duplicate"}
        if event_id:
            _processed_slack_events.add(event_id)
            if len(_processed_slack_events) > 1000:
                _processed_slack_events.clear()

    # Parse into Message
    message = adapter.parse_message(raw)
    if message is None:
        # Non-actionable event (delivery receipt, typing, etc.) — acknowledge
        return {"status": "ignored"}

    # Get session history
    session_history = _session_mgr.get_history(message.session_id)

    # Run RAG pipeline
    answer = run_pipeline(message, session_history)

    # Store turns in session
    _session_mgr.add_turn(message.session_id, "user", message.text)
    _session_mgr.add_turn(message.session_id, "bot", answer.text)

    # Log the query
    _logger.log(message, answer)

    # Format response for platform
    response_text = adapter.format_response(
        answer.text,
        confidence=0.0 if answer.is_fallback else answer.confidence,
    )

    # Send response back via platform API (async, in background for Telegram/Zalo/Slack)
    if platform != "web":
        # For Telegram/Zalo/Slack, send via their API in background
        chat_id = message.user_id
        if platform == "telegram":
            # Use chat_id from session_id (tg_{chat_id})
            chat_id = message.session_id.replace("tg_", "")
        elif platform == "slack":
            # Pass full session_id; adapter will extract channel and thread_ts
            chat_id = message.session_id
        background_tasks.add_task(adapter.send_message, chat_id, response_text)

    # Return response (used directly by web adapter)
    return {
        "status": "ok",
        "answer": answer.text,
        "confidence": answer.confidence,
        "is_fallback": answer.is_fallback,
        "rewritten_question": answer.rewritten_question,
        "sources": [
            {
                "subject": c.metadata.get("subject", ""),
                "score": round(c.score, 4),
                "url": c.metadata.get("url", ""),
            }
            for c in answer.source_chunks
        ],
    }


@app.get("/admin/logs")
async def get_logs(limit: int = 50, fallback_only: bool = False):
    """Return query logs as JSON. Optionally filter to fallback-only."""
    logs = _logger.read_logs(limit=limit, fallback_only=fallback_only)
    return {"count": len(logs), "logs": logs}


@app.post("/admin/reindex")
async def trigger_reindex(background_tasks: BackgroundTasks):
    """Trigger a full reindex from Redmine (runs in background)."""
    from data.reindex import full_reindex

    background_tasks.add_task(full_reindex)
    return {"status": "reindex_started", "message": "Full reindex triggered in background."}
