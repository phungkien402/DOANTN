# PHASE 4 — Web Chat UI + Admin Panel

## Goal

A single HTML file (`ui/index.html`) that serves two purposes:
1. **Chat UI** — test the chatbot quickly without needing Telegram or Zalo
2. **Admin panel** — view query logs, especially unanswered fallback questions
   so the helpdesk team knows which FAQ entries need to be added

No React, Vue, or build tools. Plain HTML + CSS + JavaScript `fetch`.
Open the file and it works immediately.

## Prerequisites

- Phase 3 complete — server running at `http://localhost:8080`

---

## Layout

Two tabs:

```
[ 💬 Chat ]   [ 📋 Admin Logs ]
```

---

## Tab 1 — Chat

### Wireframe

```
┌─────────────────────────────────────────┐
│  EHC AI Helpdesk                        │
├─────────────────────────────────────────┤
│                                         │
│  Bot: Hello! How can I help you with    │
│  the EHC software today?                │
│                                         │
│  You: how do I merge patient records    │
│                                         │
│  Bot: To merge duplicate patient        │
│  records, follow these steps:           │
│  1. Go to the Administration module...  │
│  [confidence: 0.94]                     │
│                                         │
├─────────────────────────────────────────┤
│  [ Type your question...    ] [ Send ]  │
└─────────────────────────────────────────┘
```

### Features

- Show confidence score below each bot message
- Color-code confidence: green (≥ 0.7), yellow (0.4–0.7), red (< 0.4 or fallback)
- "Clear history" button to reset the session
- Press Enter to send
- Disable input while waiting for response; show a loading indicator
- Generate a random `sessionId` (UUID) on page load and keep it in memory

### API call

```javascript
const response = await fetch('/webhook/web', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        user_id: 'web_tester',
        session_id: sessionId,
        text: inputText
    })
});
const data = await response.json();
// data = { answer: "...", confidence: 0.94, is_fallback: false }
```

---

## Tab 2 — Admin Logs

### Wireframe

```
┌──────────────────────────────────────────────────────────────┐
│  Admin Logs                    [ 🔄 Refresh ]  [ Re-index ]  │
├────────┬─────────────────────────┬──────────┬────────────────┤
│  Time  │  Question               │ Platform │  Result        │
├────────┼─────────────────────────┼──────────┼────────────────┤
│ 14:32  │ merge patient records   │ telegram │ ✅  0.94       │
│ 14:28  │ print medication order  │ web      │ ✅  0.81       │
│ 14:15  │ asdkjh???               │ telegram │ ❌  fallback   │
│ 14:10  │ record is locked        │ zalo     │ ✅  0.76       │
└────────┴─────────────────────────┴──────────┴────────────────┘

Filter: [ All ▼ ]   [ ❌ Fallback only ]
```

### Features

- Load from `GET /admin/logs?limit=100`
- Filter toggle: show only fallback entries — this is the main actionable view
  for the helpdesk team to identify missing FAQ entries
- **Re-index** button: calls `POST /admin/reindex`, shows success/error toast
- Auto-refresh every 30 seconds

### Detail panel on row click

```
Original question  : "asdkjh???"
Rewritten question : "asdkjh???"
Top chunk          : (none found)
Confidence         : 0.08
Outcome            : FALLBACK — logged, awaiting helpdesk follow-up
```

---

## FastAPI changes needed in `api/routes.py`

Serve the UI as a static file:

```python
from fastapi.responses import FileResponse

@app.get("/")
def serve_ui():
    return FileResponse("ui/index.html")
```

Ensure `/webhook/web` returns JSON directly (not via `send_message`):

```python
@app.post("/webhook/web")
async def handle_web(request: Request):
    raw = await request.json()
    message = web_adapter.parse_message(raw)
    history = session_manager.get_history(message.session_id)
    answer = pipeline.run(message, history)
    session_manager.add_turn(message.session_id, "user", message.text)
    session_manager.add_turn(message.session_id, "bot", answer.text)
    query_logger.log(message, answer)
    return {
        "answer": answer.text,
        "confidence": answer.confidence,
        "is_fallback": answer.is_fallback
    }
```

---

## Done Criteria

- [ ] `http://localhost:8080` loads the Chat UI
- [ ] Sending a question returns an answer with a color-coded confidence score
- [ ] Admin Logs tab shows the query log table
- [ ] "Fallback only" filter works correctly
- [ ] Re-index button calls the API and shows feedback
- [ ] UI is usable on a mobile screen (basic responsive layout)
- [ ] Everything is in a single `ui/index.html` file — no separate CSS/JS files
