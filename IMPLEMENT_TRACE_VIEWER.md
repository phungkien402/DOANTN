# TASK: Implement Pipeline Trace Viewer

## Goal

Build a real-time + historical trace viewer at `/admin/traces`.

Each query gets a full execution trace showing every pipeline node, what the
LLM decided, how long each step took, and what data flowed through. Useful for
debugging false negatives, slow queries, and wrong routing decisions.

**No dependency on Langfuse.** Data lives in `logs/traces.jsonl` inside the app.

---

## Architecture overview

```
Query arrives
    ↓
tracer.start_trace(trace_id, query)      ← new trace created
    ↓
Each node calls:
  tracer.log_event(trace_id, event)      ← appends to in-memory trace
    ↓
Pipeline complete
tracer.finish_trace(trace_id, answer)   ← writes to logs/traces.jsonl
    ↓
/admin/traces          — lists recent traces (HTML page)
/admin/traces/list     — JSON list of recent traces
/admin/traces/<id>     — full trace detail (JSON)
/admin/traces/stream/<id>  — SSE stream for live trace
```

---

## 1. `core/trace_logger.py` — new file

```python
"""
Lightweight pipeline tracer — independent from Langfuse.

Each query run gets a unique trace_id (same as session_id + timestamp).
Events are buffered in memory while running, then persisted to
logs/traces.jsonl when the trace is finished.

SSE clients (browser) subscribe to a trace_id and receive events
as they are logged — enabling real-time pipeline visualization.
"""

import json
import time
import threading
import uuid
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any

TRACES_FILE = Path("logs/traces.jsonl")
TRACES_FILE.parent.mkdir(parents=True, exist_ok=True)

# In-memory store: trace_id → {meta, events, subscribers}
_store: dict[str, dict] = {}
_lock = threading.Lock()


# ──────────────────────────────────────────────
# Event schema
# ──────────────────────────────────────────────

@dataclass
class TraceEvent:
    node: str                    # e.g. "IntentGuard", "Orchestrator"
    type: str                    # "start" | "end" | "llm" | "decision" | "error" | "info"
    ts: float = field(default_factory=time.time)
    duration_ms: float = 0.0     # filled on "end" events
    data: dict = field(default_factory=dict)  # arbitrary payload


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def start_trace(trace_id: str, query: str, user_id: str = "", platform: str = "") -> None:
    """Create a new in-memory trace entry."""
    with _lock:
        _store[trace_id] = {
            "trace_id": trace_id,
            "query": query,
            "user_id": user_id,
            "platform": platform,
            "started_at": time.time(),
            "finished_at": None,
            "total_ms": None,
            "answer": "",
            "is_fallback": False,
            "events": [],
            "subscribers": [],   # list of asyncio.Queue for SSE
        }


def log_event(trace_id: str, node: str, type: str, data: dict = None,
              duration_ms: float = 0.0) -> None:
    """Append an event to the trace and notify SSE subscribers."""
    if trace_id not in _store:
        return
    event = TraceEvent(
        node=node,
        type=type,
        duration_ms=duration_ms,
        data=data or {},
    )
    with _lock:
        entry = _store[trace_id]
        entry["events"].append(asdict(event))
        # Notify all SSE subscribers
        for q in entry["subscribers"]:
            try:
                q.put_nowait(asdict(event))
            except Exception:
                pass


def finish_trace(trace_id: str, answer: str, is_fallback: bool = False) -> None:
    """Mark trace as complete, persist to logs/traces.jsonl."""
    if trace_id not in _store:
        return
    with _lock:
        entry = _store[trace_id]
        entry["finished_at"] = time.time()
        entry["total_ms"] = round((entry["finished_at"] - entry["started_at"]) * 1000, 1)
        entry["answer"] = answer
        entry["is_fallback"] = is_fallback
        # Notify subscribers of completion
        for q in entry["subscribers"]:
            try:
                q.put_nowait({"node": "__done__", "type": "done",
                              "data": {"total_ms": entry["total_ms"]}})
            except Exception:
                pass
    # Persist (without subscribers list)
    record = {k: v for k, v in _store[trace_id].items() if k != "subscribers"}
    with open(TRACES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def get_trace(trace_id: str) -> dict | None:
    """Return in-memory trace (running or finished) or load from file."""
    if trace_id in _store:
        entry = dict(_store[trace_id])
        entry.pop("subscribers", None)
        return entry
    # Fall back to file search
    if TRACES_FILE.exists():
        for line in reversed(TRACES_FILE.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if record.get("trace_id") == trace_id:
                    return record
            except Exception:
                continue
    return None


def list_traces(limit: int = 50) -> list[dict]:
    """Return recent traces (summary only, no events list)."""
    results = []
    if TRACES_FILE.exists():
        lines = [l for l in TRACES_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
        for line in reversed(lines[-200:]):
            try:
                record = json.loads(line)
                results.append({
                    "trace_id": record["trace_id"],
                    "query": record["query"][:80],
                    "user_id": record.get("user_id", ""),
                    "platform": record.get("platform", ""),
                    "started_at": record["started_at"],
                    "total_ms": record.get("total_ms"),
                    "is_fallback": record.get("is_fallback", False),
                    "node_count": len(record.get("events", [])),
                })
            except Exception:
                continue
    # Also include active in-memory traces
    for tid, entry in _store.items():
        if not entry.get("finished_at"):
            results.insert(0, {
                "trace_id": tid,
                "query": entry["query"][:80],
                "user_id": entry.get("user_id", ""),
                "platform": entry.get("platform", ""),
                "started_at": entry["started_at"],
                "total_ms": None,
                "is_fallback": False,
                "node_count": len(entry["events"]),
                "running": True,
            })
    return results[:limit]


def subscribe_sse(trace_id: str):
    """Return an asyncio.Queue that receives events for this trace.
    If the trace is already finished, returns None.
    """
    import asyncio
    if trace_id not in _store:
        return None
    q = asyncio.Queue()
    with _lock:
        _store[trace_id]["subscribers"].append(q)
    return q
```

---

## 2. Instrument the pipeline nodes

### Where to add `log_event` calls

For each node, add timing and data logging. Use the same `trace_id` that flows
through `AgentState`. Add `trace_id: str` to `AgentState`.

**In `core/langgraph_agent.py`** — add to `AgentState`:
```python
"trace_id": str      # unique per query run
```

**In the `run()` function** — generate trace_id and start trace:
```python
import uuid
from core.trace_logger import start_trace, finish_trace

trace_id = f"{message.user_id}-{int(time.time()*1000)}"
start_trace(trace_id, message.text, user_id=message.user_id,
            platform=message.platform)
initial_state["trace_id"] = trace_id
```

**After the graph finishes** — call `finish_trace`:
```python
finish_trace(trace_id, answer.text, is_fallback=answer.is_fallback)
```

### Node instrumentation

Add these calls to each node. Import at the top of `langgraph_agent.py`:
```python
from core.trace_logger import log_event
```

**`node_intent_guard`** — after LLM classify:
```python
log_event(trace_id, "IntentGuard", "decision", {
    "result": is_ehc,           # True/False
    "duration_ms": round(elapsed * 1000, 1),
    "query": query,
})
```

**`node_fast_retriever`** — after fast retrieve:
```python
log_event(trace_id, "FastRetriever", "end", {
    "chunks": [{"subject": c.subject[:60], "score": round(c.score, 4)}
               for c in fast_chunks],
    "duration_ms": round(elapsed * 1000, 1),
})
```

**`node_orchestrator`** — after LLM decision:
```python
log_event(trace_id, "Orchestrator", "decision", {
    "action": result["action"],
    "tool": result["tool"],
    "knowledge_topic": result.get("knowledge_topic", ""),
    "reasoning": result.get("reasoning", ""),
    "search_query": result.get("search_query", ""),
    "duration_ms": round(elapsed * 1000, 1),
})
```

**`node_full_retriever`** — after rerank:
```python
log_event(trace_id, "FullRetriever", "end", {
    "top_score": round(top_score, 4),
    "chunks": [{"subject": c.subject[:60], "score": round(c.score, 4)}
               for c in ranked_chunks],
    "knowledge_loaded": bool(knowledge_content),
    "knowledge_topic": knowledge_topic,
    "duration_ms": round(elapsed * 1000, 1),
})
```

**`node_synthesizer`** — confidence decision:
```python
log_event(trace_id, "Synthesizer", "decision", {
    "confidence": round(confidence, 4),
    "threshold": CONFIDENCE_THRESHOLD,
    "route": "generator" if confidence >= CONFIDENCE_THRESHOLD else "fallback",
})
```

**`node_generator`** — after LLM generate:
```python
log_event(trace_id, "Generator", "end", {
    "prompt_chars": len(user_prompt),
    "answer_chars": len(answer_text),
    "duration_ms": round(elapsed * 1000, 1),
})
```

**`node_fallback`** — when fallback triggers:
```python
log_event(trace_id, "Fallback", "info", {
    "reason": "confidence_below_threshold",
    "confidence": round(confidence, 4),
})
```

---

## 3. API endpoints — add to `api/routes.py`

```python
from fastapi.responses import HTMLResponse, StreamingResponse
from core.trace_logger import list_traces, get_trace, subscribe_sse
import asyncio

@app.get("/admin/traces", response_class=HTMLResponse)
async def traces_page():
    html = Path("ui/traces.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)

@app.get("/admin/traces/list")
async def traces_list(limit: int = 50):
    return list_traces(limit=limit)

@app.get("/admin/traces/{trace_id}")
async def trace_detail(trace_id: str):
    trace = get_trace(trace_id)
    if not trace:
        return {"error": "trace not found"}
    return trace

@app.get("/admin/traces/stream/{trace_id}")
async def trace_stream(trace_id: str):
    """SSE endpoint — streams events for a running trace."""
    async def event_generator():
        # First, send already-buffered events
        trace = get_trace(trace_id)
        if trace:
            for event in trace.get("events", []):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if trace.get("finished_at"):
                yield f"data: {json.dumps({'node': '__done__', 'type': 'done'})}\n\n"
                return
        # Then subscribe for future events
        q = subscribe_sse(trace_id)
        if not q:
            yield f"data: {json.dumps({'node': '__done__', 'type': 'not_found'})}\n\n"
            return
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("node") == "__done__":
                    break
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

---

## 4. `ui/traces.html` — single-page trace viewer

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│  🔍 EHC — Pipeline Trace Viewer          [Auto-refresh ●]│
├──────────────────────────────────────────────────────────┤
│  RECENT QUERIES (left panel, ~300px)                     │
│  ┌────────────────────────────────────────────────────┐  │
│  │ ● [LIVE] "xử trí cứ xoay hoài..."   0.0s  telegram│  │
│  │   "in bảng kê không ra"             4.2s  telegram│  │
│  │   "đăng nhập không được"            3.8s  telegram│  │
│  │   "cách cài oracle cho ehc"  FALL   5.1s  telegram│  │
│  └────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────┤
│  TRACE DETAIL (right panel)                              │
│                                                          │
│  Query: "in bảng kê không ra"   Total: 4.2s             │
│                                                          │
│  ┌─ IntentGuard ──────────────── 210ms ─────────────┐   │
│  │  ✅ EHC-related: YES                              │   │
│  └───────────────────────────────────────────────────┘   │
│  ┌─ FastRetriever ─────────────── 380ms ────────────┐   │
│  │  #1 score=0.71 "In bảng kê thanh toán..."        │   │
│  │  #2 score=0.65 "Cấu hình máy in..."              │   │
│  └───────────────────────────────────────────────────┘   │
│  ┌─ Orchestrator ─────────────── 820ms ────────────┐    │
│  │  action=answer | tool=search_faq                 │   │
│  │  knowledge_topic=printing                        │   │
│  │  reasoning: "fast_chunks cho thấy lỗi in ấn..." │   │
│  │  search_query: "không in được bảng kê EHC"      │   │
│  └───────────────────────────────────────────────────┘   │
│  ┌─ FullRetriever ─────────────── 650ms ────────────┐   │
│  │  top_score=0.91 | knowledge=printing.md loaded   │   │
│  │  #1 score=0.91 "In bảng kê: vào menu In..."     │   │
│  └───────────────────────────────────────────────────┘   │
│  ┌─ Synthesizer ────────────────── 1ms ────────────┐    │
│  │  confidence=0.91 ≥ 0.4 → generator              │   │
│  └───────────────────────────────────────────────────┘   │
│  ┌─ Generator ─────────────────── 2100ms ───────────┐   │
│  │  prompt=3240 chars → answer=412 chars            │   │
│  └───────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

**Tech:**
- Vanilla JS, single file, dark theme (`#0f172a`)
- Left panel: polling `GET /admin/traces/list` every 5s
- Right panel: click on a query → fetch `/admin/traces/<id>` → render timeline
- For LIVE traces: use `EventSource` on `/admin/traces/stream/<id>` → append nodes as they arrive
- Node bars: color-coded by type, width proportional to duration
- Collapsible detail per node (click to expand full data)

**Color coding:**
| Node | Color |
|---|---|
| IntentGuard | `#6366f1` (indigo) |
| FastRetriever | `#0ea5e9` (sky) |
| Orchestrator | `#f59e0b` (amber) |
| FullRetriever | `#0ea5e9` (sky) |
| Synthesizer | `#8b5cf6` (violet) |
| Generator | `#22c55e` (green) |
| Fallback | `#ef4444` (red) |

**Duration bar width:** `min(duration_ms / total_ms * 100, 100)%`

---

## 5. Git workflow

```bash
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && cd ~/DOANTN && git checkout -b feature/trace-viewer"
```

Files to add/edit:
```
core/trace_logger.py       ← new file
core/langgraph_agent.py    ← add trace_id to AgentState, instrument nodes
api/routes.py              ← add 4 new endpoints
ui/traces.html             ← new file
```

```bash
/bin/bash -c "export PATH=/home/phungkien/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && cd ~/DOANTN && git add core/trace_logger.py core/langgraph_agent.py api/routes.py ui/traces.html && git commit -m 'feat: add real-time pipeline trace viewer at /admin/traces' && git push origin feature/trace-viewer"
```

---

## Done Criteria

- [ ] `GET /admin/traces` loads the page
- [ ] Left panel shows list of recent queries with total time + fallback flag
- [ ] Click a query → right panel shows all nodes in order with duration bars
- [ ] Live badge (●) appears on in-progress queries, nodes appear in real-time via SSE
- [ ] IntentGuard node shows YES/NO decision + time
- [ ] FastRetriever node shows top chunk subjects + scores
- [ ] Orchestrator node shows action, tool, knowledge_topic, reasoning text
- [ ] FullRetriever node shows top_score, whether knowledge file was loaded
- [ ] Synthesizer node shows confidence vs threshold + routing decision
- [ ] Generator node shows prompt size + answer size + LLM time
- [ ] Fallback node shown in red when confidence < threshold
- [ ] Duration bars proportional to actual time
- [ ] `logs/traces.jsonl` written after each completed query
- [ ] Page works without Langfuse being up

---

## Notes

- `trace_id` does NOT need to be stored in Langfuse — fully independent
- `_store` in `trace_logger.py` is in-memory only while the process runs. Old traces
  are read back from `logs/traces.jsonl` on demand.
- Keep `logs/traces.jsonl` rotating: add a cleanup that truncates to last 1000 lines
  when it exceeds 5000 lines (add to `finish_trace` or a background task).
- The SSE stream uses 30s keepalive to prevent nginx/proxy timeouts.
