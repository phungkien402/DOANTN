"""
tracer.py — In-memory run trace collector for LangGraph agent.
Stores last 50 runs. Thread-safe via a deque + lock.
"""
import time
from collections import deque
from threading import Lock
from typing import Optional
import uuid

_store: deque = deque(maxlen=50)
_lock = Lock()


def new_run(session_id: str, query: str) -> dict:
    run = {
        "run_id": str(uuid.uuid4())[:8],
        "session_id": session_id,
        "query": query,
        "started_at": time.time(),
        "nodes": [],
        "tool_called": "",
        "confidence": 0.0,
        "answer": "",
        "duration_ms": 0,
    }
    with _lock:
        _store.appendleft(run)
    return run


def log_node(run: dict, name: str, input_summary: dict, output_summary: dict, llm: Optional[dict] = None, duration_ms: int = 0):
    """Append a node entry to run["nodes"]."""
    run["nodes"].append({
        "name": name,
        "input": input_summary,
        "output": output_summary,
        "llm": llm,
        "duration_ms": duration_ms,
    })


def finish_run(run: dict, tool_called: str, confidence: float, answer: str):
    run["tool_called"] = tool_called
    run["confidence"] = round(confidence, 4)
    run["answer"] = answer[:200] if answer else ""
    run["duration_ms"] = int((time.time() - run["started_at"]) * 1000)


def get_all() -> list:
    with _lock:
        return list(_store)


def get_one(run_id: str) -> Optional[dict]:
    with _lock:
        for r in _store:
            if r["run_id"] == run_id:
                return r
    return None
