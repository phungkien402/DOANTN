"""
FastAPI application routes.

POST /webhook/{platform}  — receive messages from Zalo / Telegram / Web
GET  /health              — health check
GET  /admin/logs          — view unanswered / fallback query logs
POST /admin/reindex       — trigger a fresh data pull from Redmine

Run: uvicorn api.routes:app --host 0.0.0.0 --port 8080
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis
from rq import Queue

from config import SESSION_MAX_TURNS, ADMIN_TOKEN
from core.models import Message
from core.langgraph_agent import run as run_pipeline, set_maintenance_mode, is_maintenance_mode, set_session_manager as set_agent_session_mgr
from api.session import SessionManager
from api.logger import QueryLogger
from adapters.telegram_adapter import TelegramAdapter
from adapters.telegram_adapter import set_session_manager as set_telegram_session_mgr
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
_session_mgr = SessionManager(max_turns=SESSION_MAX_TURNS, ttl_seconds=1800)
set_telegram_session_mgr(_session_mgr)
set_agent_session_mgr(_session_mgr)
_logger = QueryLogger()

# Adapter registry
_adapters = {
    "telegram": TelegramAdapter(),
    "zalo": ZaloAdapter(),
    "web": WebAdapter(),
    "slack": SlackAdapter(),
}

# Redis Queue for async Telegram processing
_redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
_queue = Queue("ehc-queue", connection=_redis_conn)

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

    # --- Slack slash commands (form-encoded) ---
    content_type = request.headers.get("content-type", "")
    if platform == "slack" and "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        form_data = dict(form)
        # Slash commands have a "command" field
        if "command" in form_data:
            response_text = await adapter.handle_slash_command(form_data)
            return JSONResponse(
                content={"response_type": "ephemeral", "text": response_text},
                status_code=200,
            )

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

    # --- Telegram: enqueue to Redis Queue and return immediately ---
    if platform == "telegram":
        chat_id = message.session_id.replace("tg_", "")
        _queue.enqueue(
            "workers.pipeline_worker.process_telegram_query",
            chat_id=chat_id,
            text=message.text,
            session_id=message.session_id,
            history=session_history,
        )
        print(f"[WEBHOOK] Enqueued | chat_id={chat_id} | query=\"{message.text}\"")
        return {"ok": True}

    # --- Other platforms: synchronous processing ---
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

    # Send response back via platform API (async, in background for Zalo/Slack)
    if platform != "web":
        chat_id = message.user_id
        if platform == "slack":
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


@app.post("/admin/maintenance")
async def toggle_maintenance(request: Request):
    """Toggle maintenance mode at runtime. Requires ADMIN_TOKEN."""
    # Auth check
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not configured")

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip()
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="Body must contain {\"enabled\": true/false}")

    set_maintenance_mode(enabled)
    return {
        "status": "ok",
        "maintenance_mode": is_maintenance_mode(),
        "message": f"Maintenance mode {'enabled' if enabled else 'disabled'}.",
    }


@app.get("/tickets")
async def get_tickets():
    """Return all tickets from data/tickets.db."""
    from core.tools.create_ticket import list_tickets
    tickets = list_tickets()
    return {"count": len(tickets), "tickets": tickets}


@app.get("/unanswered")
async def list_unanswered():
    """Return all entries from data/unanswered.jsonl (newest first)."""
    import json as _json
    path = Path(__file__).parent.parent / "data" / "unanswered.jsonl"
    if not path.exists():
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(_json.loads(line))
                except _json.JSONDecodeError:
                    pass
    return list(reversed(entries))


# --- Trace endpoints ---

@app.get("/traces")
async def list_traces():
    """Return recent run traces (last 50)."""
    from core import tracer
    return tracer.get_all()


@app.get("/traces/{run_id}")
async def get_trace(run_id: str):
    """Return a single run trace by ID."""
    from core import tracer
    r = tracer.get_one(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="Run not found")
    return r


@app.get("/traces-ui")
async def traces_ui():
    """Serve the traces dashboard."""
    ui_path = Path(__file__).parent.parent / "static" / "traces.html"
    if ui_path.exists():
        return FileResponse(str(ui_path))
    return JSONResponse({"error": "traces.html not found"}, status_code=404)
