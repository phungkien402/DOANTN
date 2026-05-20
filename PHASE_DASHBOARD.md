# PHASE: Admin Monitoring Dashboard

## Goal

Build a single-page monitoring dashboard served by the existing FastAPI app.
The dashboard gives the admin a real-time view of system health and usage
statistics — no external services required.

```
http://localhost:8001/admin/dashboard
```

---

## Prerequisites

- Phase 3 complete — FastAPI running, `logs/queries.jsonl` being written
- Python packages: `psutil` (add to `requirements.txt`)
- Optional: `pynvml` for GPU monitoring (V100 on Dell R730xd)

---

## Architecture Decision

**Single-page HTML served by FastAPI + JSON API endpoints.**

Do NOT use React, Vue, or any frontend build tool.
The dashboard is a single `ui/dashboard.html` file served as a static page.
All data is fetched via `fetch()` calls to new `/admin/stats/*` endpoints.

Reasons:
- No build toolchain needed on the server
- Fits the existing project structure (no `ui/` package, just static files)
- Loads instantly, works offline on the hospital LAN

---

## 1. New Python packages

Add to `requirements.txt`:
```
psutil>=5.9.0
pynvml>=11.5.0
```

---

## 2. New API endpoints in `api/routes.py`

Add these 4 endpoints. Each reads from existing data files — no new DB schema.

### `GET /admin/stats/queries`

Read `logs/queries.jsonl`. Return aggregated stats.

```python
@app.get("/admin/stats/queries")
async def stats_queries(days: int = 7):
    """
    Returns:
    {
      "total_queries": 312,
      "total_fallbacks": 45,
      "fallback_rate": 0.144,
      "avg_confidence": 0.71,
      "avg_latency_ms": 4290,
      "by_day": [
        {"date": "2026-05-13", "count": 48, "fallbacks": 6},
        ...
      ],
      "by_platform": {"telegram": 290, "zalo": 22},
      "top_unanswered": [
        {"question": "...", "count": 5, "last_seen": "2026-05-18"},
        ...
      ]
    }
    """
```

Implementation notes:
- Read `logs/queries.jsonl` line by line (each line is a JSON `QueryLog`)
- Filter to last `days` days using `timestamp` field
- `by_day`: group by `datetime.fromtimestamp(ts).date().isoformat()`
- `top_unanswered`: read `data/unanswered.jsonl`, group by `question` text,
  sort by frequency descending, return top 10
- `avg_latency_ms`: stored in QueryLog as `latency_ms` field
  (if field missing in older logs, skip gracefully)

### `GET /admin/stats/health`

Check all service dependencies. Must respond in < 2 seconds total.
Use `asyncio.gather` with per-service timeouts.

```python
@app.get("/admin/stats/health")
async def stats_health():
    """
    Returns:
    {
      "fastapi": "ok",
      "qdrant": "ok",          # GET http://localhost:6333/healthz
      "vllm": "ok",            # GET http://localhost:8000/health
      "redis": "ok",           # redis-cli ping via subprocess or redis-py
      "timestamp": 1716038400.0
    }
    """
```

Each service status: `"ok"` | `"error"` | `"timeout"`.
Never let one failed service crash the whole endpoint — catch all exceptions.

### `GET /admin/stats/resources`

System resource snapshot.

```python
@app.get("/admin/stats/resources")
async def stats_resources():
    """
    Returns:
    {
      "cpu_percent": 34.2,
      "ram_used_gb": 48.3,
      "ram_total_gb": 128.0,
      "ram_percent": 37.7,
      "disk_used_gb": 210.4,
      "disk_total_gb": 800.0,
      "disk_percent": 26.3,
      "gpu": [
        {
          "index": 0,
          "name": "Tesla V100-SXM2-16GB",
          "vram_used_mb": 13800,
          "vram_total_mb": 16160,
          "vram_percent": 85.4,
          "temperature_c": 62,
          "utilization_percent": 78
        }
      ]
    }
    """
```

Implementation:
- Use `psutil.cpu_percent(interval=0.5)`, `psutil.virtual_memory()`,
  `psutil.disk_usage('/')`
- For GPU: `import pynvml; pynvml.nvmlInit()` then iterate handles.
  If `pynvml` import fails or `nvmlInit` raises, return `"gpu": []`
  (server may not have GPU drivers at import time)

### `GET /admin/stats/tickets`

Ticket summary from SQLite.

```python
@app.get("/admin/stats/tickets")
async def stats_tickets():
    """
    Returns:
    {
      "total": 45,
      "pending": 12,
      "pushed_to_redmine": 33,
      "recent": [
        {
          "ticket_id": "abc-123",
          "user_id": "u_456",
          "question": "...",
          "created_at": "2026-05-18T14:22:00",
          "status": "pushed"
        },
        ...
      ]
    }
    """
```

Read from `data/tickets.db`. Return last 20 tickets in `recent`.

---

## 3. `ui/dashboard.html`

A single self-contained HTML file. Served via FastAPI:

```python
# In api/routes.py — add near the top:
from fastapi.responses import HTMLResponse
from pathlib import Path

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def dashboard():
    html = Path("ui/dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)
```

### Layout

Two-tab layout:

```
┌─────────────────────────────────────────────┐
│  🏥 EHC Helpdesk — Admin Dashboard          │
│  [Thống kê]  [Sức khoẻ hệ thống]            │
├─────────────────────────────────────────────┤
│  TAB 1 — Thống kê                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐│
│  │ Tổng lượt│ │Fallback  │ │ Avg latency  ││
│  │   312    │ │  14.4%   │ │   4.29s      ││
│  └──────────┘ └──────────┘ └──────────────┘│
│                                             │
│  [Bar chart: queries per day — last 7 days] │
│                                             │
│  Platform breakdown: Telegram 93% | Zalo 7% │
│                                             │
│  Top câu hỏi chưa trả lời được:             │
│  1. "..."   × 5 lần   2026-05-18            │
│  2. "..."   × 3 lần   2026-05-17            │
│  ...                                        │
├─────────────────────────────────────────────┤
│  TAB 2 — Sức khoẻ hệ thống                 │
│  Services:                                  │
│  ● FastAPI  ✅ ok   ● Qdrant   ✅ ok        │
│  ● vLLM     ✅ ok   ● Redis    ✅ ok        │
│                                             │
│  Resources:                                 │
│  CPU  ████░░░░░░  34%                       │
│  RAM  ████░░░░░░  37.7%  (48 / 128 GB)     │
│  Disk ███░░░░░░░  26.3%  (210 / 800 GB)    │
│                                             │
│  GPU 0 — Tesla V100                         │
│  VRAM ████████░░  85%   (13.8 / 16.2 GB)   │
│  Temp: 62°C   Util: 78%                     │
│                                             │
│  Tickets: 45 total | 12 pending             │
│  [Recent tickets table — last 20]           │
└─────────────────────────────────────────────┘
```

### Tech stack for the HTML file

- Vanilla JS only — no npm, no bundler
- CSS: inline `<style>` block, dark theme (`#0f172a` background)
- Chart: use **Chart.js from CDN**
  `<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js">`
- Auto-refresh: `setInterval(fetchAll, 30000)` — refresh every 30 seconds
- Show last-updated timestamp in header

### Color coding

| Condition | Color |
|---|---|
| Service ok | `#22c55e` (green) |
| Service error/timeout | `#ef4444` (red) |
| Resource < 70% | `#22c55e` |
| Resource 70–85% | `#f59e0b` (amber) |
| Resource > 85% | `#ef4444` |

---

## 4. Update `api/logger.py`

Add `latency_ms` field to `QueryLog` so the dashboard can show avg latency:

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
    top_chunk_subject: str
    latency_ms: float = 0.0    # ← ADD THIS FIELD
```

In `api/routes.py`, measure latency in `handle_webhook`:

```python
import time
t0 = time.time()
answer = pipeline.run(message, history)        # existing call
latency_ms = (time.time() - t0) * 1000
query_logger.log(message, answer, latency_ms)  # pass latency
```

---

## 5. Git workflow

```bash
git checkout -b feature/admin-dashboard
# ... implement ...
git add api/routes.py ui/dashboard.html api/logger.py requirements.txt
git commit -m "rtk: add admin monitoring dashboard (stats + health + resources)"
git push origin feature/admin-dashboard
```

---

## Done Criteria

- [ ] `GET /admin/dashboard` loads the HTML page in browser
- [ ] Tab 1 shows total queries, fallback rate, and bar chart (last 7 days)
- [ ] Tab 1 shows top-10 unanswered questions from `data/unanswered.jsonl`
- [ ] Tab 2 shows green/red status for FastAPI, Qdrant, vLLM, Redis
- [ ] Tab 2 shows CPU / RAM / Disk progress bars with correct values
- [ ] Tab 2 shows GPU VRAM and temperature (or empty section if no GPU)
- [ ] Tab 2 shows recent tickets table
- [ ] Page auto-refreshes every 30 seconds
- [ ] Color thresholds correct: amber at 70%, red at 85%
- [ ] A single service being down does NOT crash `/admin/stats/health`
- [ ] `latency_ms` recorded in new log entries; old entries without it are
      handled gracefully (treat as 0 or skip from avg calculation)

---

## File checklist

```
api/routes.py          ← add 4 new endpoints + /admin/dashboard HTML route
api/logger.py          ← add latency_ms field to QueryLog
ui/dashboard.html      ← new file, self-contained single-page dashboard
requirements.txt       ← add psutil, pynvml
```
