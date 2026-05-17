# PHASE 3 — Adapter Layer + FastAPI Gateway

## Goal

By the end of this phase, the server is running and can receive messages from
Telegram, process them through the RAG Core, and send back answers.

```bash
uvicorn api.routes:app --host 0.0.0.0 --port 8080
# send a message to the Telegram bot → receive a reply
```

## Prerequisites

- Phase 2 complete — `core/pipeline.py` works correctly
- Telegram bot token present in `.env`

---

## 1. `adapters/base_adapter.py`

### Abstract interface

```python
from abc import ABC, abstractmethod
from core.models import Message

class BaseAdapter(ABC):

    @abstractmethod
    def parse_message(self, raw: dict) -> Message | None:
        """
        Accept a raw webhook payload from the platform.
        Return a standard Message object, or None if the event should
        be ignored (e.g. delivery receipts, typing indicators, etc.).
        """
        ...

    @abstractmethod
    def format_response(self, answer_text: str, confidence: float) -> str:
        """
        Format the answer text according to the platform's conventions.
        Example: Zalo supports different markdown syntax than Telegram.
        """
        ...

    @abstractmethod
    async def send_message(self, user_id: str, text: str) -> None:
        """
        Send a message back to the user via the platform's API.
        """
        ...
```

> **Rule:** nothing inside `core/` may import from `adapters/`.
> Only `api/routes.py` is allowed to use adapters.

---

## 2. `adapters/telegram_adapter.py`

Implement `BaseAdapter` for the Telegram Bot API.

### `parse_message`

```python
# raw = Telegram Update object
# Only handle text messages; ignore stickers, files, etc.
update = raw.get("message", {})
text = update.get("text", "")
if not text:
    return None
return Message(
    user_id=str(update["from"]["id"]),
    session_id=str(update["chat"]["id"]),
    text=text,
    timestamp=float(update["date"]),
    platform="telegram"
)
```

### `format_response`

Telegram supports MarkdownV2. Format multi-step answers as a numbered list
when the response contains sequential instructions.

### `send_message`

```
POST https://api.telegram.org/bot{TOKEN}/sendMessage
Body: {chat_id, text, parse_mode: "MarkdownV2"}
```

---

## 3. `adapters/zalo_adapter.py`

Implement `BaseAdapter` for the Zalo OA API.

### `parse_message`

```python
# Zalo sends events via webhook
# Only process event_name == "user_send_text"
if raw.get("event_name") != "user_send_text":
    return None
return Message(
    user_id=raw["sender"]["id"],
    session_id=raw["sender"]["id"],   # Zalo: session = user
    text=raw["message"]["text"],
    timestamp=float(raw["timestamp"]),
    platform="zalo"
)
```

### Webhook signature verification

Zalo requires HMAC-SHA256 signature verification using `ZALO_OA_SECRET`.
Implement a FastAPI middleware or dependency that verifies the signature
before any message is processed. Reject requests with invalid signatures
with HTTP 403.

---

## 4. `adapters/web_adapter.py`

The simplest adapter — used by the Web Chat UI and for testing.

```python
# raw = {"user_id": "...", "text": "...", "session_id": "..."}
# parse_message: wrap into Message directly
# format_response: return plain text, no special formatting needed
# send_message: no-op (Web uses the HTTP response directly)
```

---

## 5. `api/session.py`

### Responsibility

Store per-user conversation history so that:
- The Fallback Handler knows whether the bot has already asked for
  clarification in this session (avoids asking twice)
- Context is available if needed for multi-turn conversations

### Implementation

In-memory dict. Limit to `SESSION_MAX_TURNS` most recent turns (default: 10).

```python
class SessionManager:
    def __init__(self):
        self._sessions: dict[str, list[dict]] = {}

    def get_history(self, session_id: str) -> list[dict]:
        return self._sessions.get(session_id, [])

    def add_turn(self, session_id: str, role: str, text: str) -> None:
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append({"role": role, "text": text})
        # Keep only the most recent N turns
        self._sessions[session_id] = \
            self._sessions[session_id][-SESSION_MAX_TURNS:]

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
```

---

## 6. `api/logger.py`

### Responsibility

Log every query and its outcome. Fallback entries are especially important —
they tell the helpdesk team which questions need new FAQ entries.

```python
@dataclass
class QueryLog:
    timestamp: float
    user_id: str
    platform: str
    question: str
    rewritten_question: str
    answer: str
    confidence: float
    is_fallback: bool
    top_chunk_subject: str    # FAQ title used (empty string if fallback)
```

Write each entry as a JSON line to `logs/queries.jsonl`.
The admin panel reads this file.

---

## 7. `api/routes.py`

### Endpoints

```python
POST /webhook/telegram
POST /webhook/zalo
POST /webhook/web          # returns answer directly in HTTP response

GET  /health
GET  /admin/logs?limit=50&fallback_only=true
POST /admin/reindex        # triggers python -m data.reindex
```

### Webhook processing flow

```python
@app.post("/webhook/{platform}")
async def handle_webhook(platform: str, request: Request):
    raw = await request.json()

    # 1. Select the right adapter
    adapter = get_adapter(platform)  # raises 404 if platform unknown

    # 2. Parse raw payload into a standard Message
    message = adapter.parse_message(raw)
    if message is None:
        return {"ok": True}          # ignore non-message events

    # 3. Load conversation history
    history = session_manager.get_history(message.session_id)

    # 4. Run RAG Core — has no knowledge of the platform
    answer = pipeline.run(message, history)

    # 5. Update session history
    session_manager.add_turn(message.session_id, "user", message.text)
    session_manager.add_turn(message.session_id, "bot", answer.text)

    # 6. Log the query
    query_logger.log(message, answer)

    # 7. Format and send the response back
    formatted = adapter.format_response(answer.text, answer.confidence)
    await adapter.send_message(message.user_id, formatted)

    return {"ok": True}
```

### Special case: Web adapter

`/webhook/web` should return the answer directly in the HTTP response body
instead of calling `send_message`:

```python
return {
    "answer": answer.text,
    "confidence": answer.confidence,
    "is_fallback": answer.is_fallback
}
```

---

## Done Criteria

- [ ] Server starts cleanly: `uvicorn api.routes:app --port 8080`
- [ ] `GET /health` returns `{"status": "ok"}`
- [ ] Sending a message to the Telegram bot returns a correct answer
- [ ] Unanswered questions return the fallback message without crashing
- [ ] `GET /admin/logs` returns the log list as JSON
- [ ] Web adapter works via curl:
  ```bash
  curl -X POST http://localhost:8080/webhook/web \
    -H "Content-Type: application/json" \
    -d '{"user_id":"test","session_id":"s1","text":"how do I merge patient records"}'
  ```
- [ ] Adding a new adapter only requires creating a new file — no changes to
  `core/` or existing adapters
