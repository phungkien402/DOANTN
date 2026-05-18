# Phase A — LangGraph Orchestrator: Claude Code Instructions

## Context

You are working on **DOANTN** — a thesis project built on top of EHC AI Helpdesk.
The project is a RAG chatbot for EHC hospital management software (ehcHIS), running 100% on-premise.

Current state: The pipeline uses `core/pipeline.py` (plain Python, no LangGraph).
Phase A replaces this with a LangGraph Agent Orchestrator while keeping all adapters unchanged.

**Server**: phungkien@14.162.132.21, project folder: `~/DOANTN/`
**Running service**: `doantn.service` on port 8001
**vLLM**: shared service on port 8000 (do NOT touch)

---

## Task

Implement a LangGraph Agent Orchestrator in `core/langgraph_agent.py` that replaces `core/pipeline.py` as the main processing unit.

---

## Architecture

```
User Query
    │
    ▼
[Node 1] QueryAnalyzer
    - Reuse: intent_guard.classify(query) → YES/NO
    - If NO → route to ChatFallback
    - If YES → route to ToolRouter
    │
    ▼
[Node 2] ToolRouter
    - Inspect intent from QueryAnalyzer result
    - Route: search_faq | create_ticket
    - Default: search_faq (for all EHC-related queries)
    │
    ├──[search_faq]──▶ [Node 3] Retriever
    │                       - Fast retrieve top 3 (existing retriever, no rerank)
    │                       - Rewrite query (existing query_rewriter)
    │                       - Full retrieve top 10
    │                       - Rerank top 3 (existing reranker)
    │                       │
    │                       ▼
    │                  [Node 4] Synthesizer
    │                       - Check confidence (existing confidence.py, threshold 0.4)
    │                       - If confident → route to Generator
    │                       - If low confidence → route to TicketCreator
    │                       │
    │                       ▼
    │                  [Node 5] Generator
    │                       - Reuse existing generator.py
    │                       - Return final answer string
    │
    └──[create_ticket]──▶ [Node 6] TicketCreator
                            - Save to SQLite: data/tickets.db
                            - Return "Đã ghi nhận sự cố, mã ticket: #<id>"
    │
    ▼
[Node 7] ChatFallback
    - Reuse: intent_guard.chat_fallback(query)
    - Return short polite off-topic response
```

---

## Step 1: Install dependencies

```bash
pip install langgraph langchain-core --break-system-packages
```

Verify: `python3 -c "import langgraph; print(langgraph.__version__)"`

---

## Step 2: Create `core/tools/create_ticket.py`

```python
"""
create_ticket.py — Save low-confidence queries to local SQLite.
SQLite file: data/tickets.db
Table: tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, timestamp TEXT, status TEXT DEFAULT 'open', assigned_to TEXT DEFAULT 'helpdesk')
Expose: save_ticket(query: str) -> int  (returns ticket_id)
FastAPI endpoint will be added in api/routes.py later.
"""
```

- Create `data/` directory if it doesn't exist
- Table DDL runs on first import (CREATE TABLE IF NOT EXISTS)
- Test standalone: `python3 core/tools/create_ticket.py` should insert a test row and print ticket_id

---

## Step 3: Create `core/tools/search_faq.py`

```python
"""
search_faq.py — Wrap the existing RAG pipeline steps for use as a LangGraph tool.
Calls: retriever → query_rewriter → retriever (full) → reranker
Returns: (chunks: list[RetrievedChunk], rewritten_query: str)
"""
```

- Import from existing: `core/retriever.py`, `core/query_rewriter.py`, `core/reranker.py`
- Top-K values: fast retrieve=3, full retrieve=10, rerank=3 (match config.py RETRIEVER_TOP_K / RERANKER_TOP_N)
- Test standalone: `python3 core/tools/search_faq.py` with a sample EHC query

---

## Step 4: Create `core/langgraph_agent.py`

### State schema

```python
from typing import TypedDict, Optional, List

class AgentState(TypedDict):
    query: str                   # original user query
    expanded_query: str          # after abbreviation expansion (use query if no expansion)
    is_ehc_related: bool
    intent: str                  # "search_faq" | "create_ticket" | "chat_fallback"
    rewritten_query: str
    tool_called: str             # actual tool that ran
    chunks: list                 # list of RetrievedChunk
    confidence: float
    answer: str
    ticket_id: Optional[int]
```

### Nodes

Each node is a plain Python function `def node_name(state: AgentState) -> dict`:
- Returns only the keys it updates (LangGraph merges into state)
- Prints `[AGENT] Node: <NodeName> | <key info>` at entry

```
node_query_analyzer(state)  → updates: is_ehc_related, intent
node_tool_router(state)     → updates: tool_called (determines next edge)
node_retriever(state)       → updates: chunks, rewritten_query
node_synthesizer(state)     → updates: confidence, intent (may change to create_ticket)
node_generator(state)       → updates: answer
node_ticket_creator(state)  → updates: ticket_id, answer
node_chat_fallback(state)   → updates: answer
```

### Graph wiring

```python
from langgraph.graph import StateGraph, END

graph = StateGraph(AgentState)

# Add nodes
graph.add_node("query_analyzer", node_query_analyzer)
graph.add_node("tool_router", node_tool_router)
graph.add_node("retriever", node_retriever)
graph.add_node("synthesizer", node_synthesizer)
graph.add_node("generator", node_generator)
graph.add_node("ticket_creator", node_ticket_creator)
graph.add_node("chat_fallback", node_chat_fallback)

# Set entry point
graph.set_entry_point("query_analyzer")

# Conditional edges
graph.add_conditional_edges(
    "query_analyzer",
    lambda s: "chat_fallback" if not s["is_ehc_related"] else "tool_router"
)
graph.add_conditional_edges(
    "tool_router",
    lambda s: s["tool_called"]  # "retriever" or "ticket_creator"
)
graph.add_conditional_edges(
    "synthesizer",
    lambda s: "ticket_creator" if s["intent"] == "create_ticket" else "generator"
)

# Linear edges
graph.add_edge("retriever", "synthesizer")
graph.add_edge("generator", END)
graph.add_edge("ticket_creator", END)
graph.add_edge("chat_fallback", END)

app = graph.compile()
```

### Public API (same signature as pipeline.py)

```python
def run(query: str, session_history: list = None) -> str:
    """Drop-in replacement for pipeline.run()"""
    initial_state: AgentState = {
        "query": query,
        "expanded_query": query,
        "is_ehc_related": False,
        "intent": "search_faq",
        "rewritten_query": "",
        "tool_called": "",
        "chunks": [],
        "confidence": 0.0,
        "answer": "",
        "ticket_id": None,
    }
    result = app.invoke(initial_state)
    return result["answer"]
```

- Test standalone: `python3 core/langgraph_agent.py` with 3 queries:
  1. An EHC question (should go search_faq → generator)
  2. "hello" (should go chat_fallback)
  3. An EHC question with deliberately low context (force ticket_creator by temporarily lowering threshold to 0.9)

---

## Step 5: Update adapters and API

Update **only the import line** in these files. Do NOT change any other logic:

```python
# In: adapters/telegram_adapter.py, adapters/slack_adapter.py, adapters/zalo_adapter.py, api/main.py
# Find:
from core.pipeline import run
# Replace with:
from core.langgraph_agent import run
```

Verify each file still has the same function call: `run(query, session_history)`

---

## Step 6: Add SQLite tickets endpoint to `api/main.py`

```python
@app.get("/tickets")
async def list_tickets():
    """Return all tickets from data/tickets.db"""
    ...
```

---

## Step 7: Test end-to-end

```bash
cd ~/DOANTN
rtk python3 core/langgraph_agent.py
```

Then restart service and test via Telegram:
```bash
sudo systemctl restart doantn
sudo journalctl -u doantn -f
```

Send 3 test messages to the Telegram bot:
1. "không in được phiếu thu" → expect: answer from FAQ
2. "hôm nay bạn thế nào" → expect: short polite refusal
3. (if possible) a query that returns low confidence → expect: "Đã ghi nhận sự cố, mã ticket: #1"

---

## Expected Log Output

```
[AGENT] Node: QueryAnalyzer | query="không in được phiếu thu"
[AGENT] Classifier: YES
[AGENT] Node: ToolRouter | tool=search_faq
[AGENT] Node: Retriever | rewritten="lỗi in phiếu thu EHC"
[AGENT] Node: Synthesizer | confidence=0.87 → CONFIDENT
[AGENT] Node: Generator | tokens=643
[AGENT] Done | tool=search_faq confidence=0.87

[AGENT] Node: QueryAnalyzer | query="hello"
[AGENT] Classifier: NO
[AGENT] Node: ChatFallback
[AGENT] Done | tool=chat_fallback

[AGENT] Node: Synthesizer | confidence=0.21 → LOW
[AGENT] Node: ToolRouter | tool=create_ticket
[AGENT] Node: TicketCreator | ticket_id=3
[AGENT] Done | tool=create_ticket
```

---

## Constraints

- **DO NOT** modify `core/pipeline.py` — keep it intact as fallback
- **DO NOT** touch `core/intent_guard.py` — import and reuse as-is
- **DO NOT** change any adapter logic except the import line
- **DO NOT** change `.env` or config values
- **DO NOT** restart vLLM service (port 8000)
- Use `rtk` prefix for all shell commands (e.g., `rtk python3 ...`, `rtk pip install ...`)
- All new files must have `if __name__ == "__main__":` standalone test block

---

## Success Criteria

- [ ] `python3 core/langgraph_agent.py` runs all 3 test queries without error
- [ ] Telegram bot still responds correctly after `systemctl restart doantn`
- [ ] `GET /tickets` endpoint returns JSON list
- [ ] Log shows correct node path for each query type
- [ ] No changes to adapter files except the import line
