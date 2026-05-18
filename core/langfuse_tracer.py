"""
Thin wrapper around Langfuse SDK for EHC Helpdesk tracing.
Creates one trace per query; individual nodes log spans.
"""

import os
import time
from typing import Any

try:
    from langfuse import Langfuse

    _lf = Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
        host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
    )
    _enabled = bool(os.getenv("LANGFUSE_PUBLIC_KEY"))
except ImportError:
    _lf = None
    _enabled = False


def new_trace(query: str, session_id: str = "") -> Any:
    if not _enabled:
        return None
    return _lf.trace(
        name="query",
        input={"query": query},
        session_id=session_id or None,
    )


def log_span(
    trace: Any,
    name: str,
    input_data: dict,
    output_data: dict,
    start_time: float,
) -> None:
    if not trace:
        return
    elapsed_ms = int((time.time() - start_time) * 1000)
    trace.span(
        name=name,
        input=input_data,
        output=output_data,
        metadata={"latency_ms": elapsed_ms},
    )


def end_trace(
    trace: Any,
    tool: str,
    confidence: float,
    answered: bool,
    tokens: int = 0,
) -> None:
    if not trace:
        return
    trace.update(
        output={"answered": answered, "tool": tool, "confidence": round(confidence, 4)},
        metadata={"tokens": tokens},
    )
    _lf.flush()
