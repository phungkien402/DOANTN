"""
LangGraph Agent Orchestrator — replaces core/pipeline.py as the main processing unit.

Graph flow:
  QueryAnalyzer → ToolRouter → Retriever → Synthesizer → Generator
                                                       → TicketCreator
              → ChatFallback

Public API: run(message, session_history) -> Answer
Same signature as pipeline.py for drop-in replacement.

Run standalone: python3 core/langgraph_agent.py
"""

import sys
import time
from pathlib import Path
from typing import TypedDict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from langgraph.graph import StateGraph, END

from config import CONFIDENCE_THRESHOLD, MAINTENANCE_MODE
from core.models import Message, Answer, RetrievedChunk
from core.intent_guard import classify, chat_fallback
from core.tools.search_faq import search_faq
from core.tools.create_ticket import save_ticket
from core import generator, confidence


# --- Session manager injection (set from api/routes.py to avoid circular imports) ---

_session_mgr = None


def set_session_manager(mgr):
    """Inject the SessionManager instance from api/routes.py."""
    global _session_mgr
    _session_mgr = mgr
    print("[AGENT] SessionManager injected")


# --- State schema ---

class AgentState(TypedDict):
    query: str                    # original user query
    expanded_query: str           # after abbreviation expansion (use query if no expansion)
    is_ehc_related: bool
    intent: str                   # "search_faq" | "create_ticket" | "chat_fallback" | "clarify"
    rewritten_query: str
    tool_called: str              # actual tool that ran
    chunks: list                  # list of RetrievedChunk
    confidence: float
    answer: str
    ticket_id: Optional[int]
    user_intent: Optional[str]    # intent description from analyze_and_rewrite
    session_history: list         # conversation history
    session_id: str               # needed to look up clarification count
    clarification_count: int      # how many times we've asked for clarification


# --- Maintenance mode (same as pipeline.py) ---

_maintenance_mode: bool = MAINTENANCE_MODE

MAINTENANCE_MESSAGE = (
    "⚙️ Hệ thống đang bảo trì, vui lòng thử lại sau ít phút. "
    "Xin lỗi vì sự bất tiện này 🙏"
)


def set_maintenance_mode(enabled: bool):
    """Toggle maintenance mode at runtime."""
    global _maintenance_mode
    _maintenance_mode = enabled
    print(f"[AGENT] Maintenance mode: {'ON' if enabled else 'OFF'}")


def is_maintenance_mode() -> bool:
    """Check if maintenance mode is active."""
    return _maintenance_mode


# --- Node functions ---

def node_query_analyzer(state: AgentState) -> dict:
    """Classify query: EHC-related or off-topic."""
    query = state["query"]
    print(f"\n[AGENT] Node: QueryAnalyzer | query=\"{query}\"")

    is_off_topic = classify(query)

    if is_off_topic:
        print(f"[AGENT] Classifier: NO (off-topic)")
        return {
            "is_ehc_related": False,
            "intent": "chat_fallback",
        }
    else:
        print(f"[AGENT] Classifier: YES (EHC-related)")
        return {
            "is_ehc_related": True,
            "intent": "search_faq",
        }


def node_tool_router(state: AgentState) -> dict:
    """Route to the appropriate tool based on intent."""
    intent = state["intent"]
    print(f"[AGENT] Node: ToolRouter | intent={intent}")

    if intent == "create_ticket":
        return {"tool_called": "ticket_creator"}
    else:
        # Default: search_faq for all EHC-related queries
        return {"tool_called": "retriever"}


def node_retriever(state: AgentState) -> dict:
    """Execute the full RAG search pipeline."""
    query = state["query"]
    print(f"[AGENT] Node: Retriever | query=\"{query}\"")

    chunks, rewritten, user_intent = search_faq(query)

    print(f"[AGENT] Node: Retriever | rewritten=\"{rewritten}\"")
    return {
        "chunks": chunks,
        "rewritten_query": rewritten,
        "user_intent": user_intent,
    }


def node_synthesizer(state: AgentState) -> dict:
    """Check confidence of retrieved chunks and decide next route."""
    chunks = state["chunks"]
    session_id = state.get("session_id", "")

    if not chunks:
        top_score = 0.0
    else:
        top_score = chunks[0].score

    is_confident = chunks and confidence.is_confident(chunks[0], threshold=CONFIDENCE_THRESHOLD)

    if is_confident:
        print(f"[AGENT] Node: Synthesizer | confidence={top_score:.4f} → CONFIDENT")
        return {
            "confidence": top_score,
            "intent": "search_faq",
        }
    else:
        # Check how many times we've already asked for clarification
        count = _session_mgr.get_clarification_count(session_id) if _session_mgr else 0
        print(f"[AGENT] Node: Synthesizer | confidence={top_score:.4f} → LOW | clarification_count={count}")
        if count >= 3:
            return {"confidence": top_score, "intent": "create_ticket"}
        else:
            return {"confidence": top_score, "intent": "clarify"}


def node_generator(state: AgentState) -> dict:
    """Generate a grounded answer from retrieved chunks."""
    rewritten = state["rewritten_query"]
    chunks = state["chunks"]
    session_history = state.get("session_history", [])
    user_intent = state.get("user_intent")
    session_id = state.get("session_id", "")

    print(f"[AGENT] Node: Generator | chunks={len(chunks)}")

    try:
        answer_text = generator.generate(
            rewritten, chunks, session_history, user_intent=user_intent
        )
    except Exception as e:
        print(f"[AGENT] Generator failed: {e}")
        answer_text = (
            "⚠️ Hệ thống AI đang bận hoặc đang khởi động lại, "
            "vui lòng thử lại sau 1–2 phút. Nếu vẫn lỗi, liên hệ bộ phận IT để kiểm tra server."
        )

    # Reset clarification count after successful confident answer
    if _session_mgr and session_id:
        _session_mgr.reset_clarification(session_id)

    print(f"[AGENT] Node: Generator | answer_len={len(answer_text)}")
    return {"answer": answer_text}


def node_ticket_creator(state: AgentState) -> dict:
    """Create a ticket for low-confidence queries."""
    query = state["query"]
    user_intent = state.get("user_intent")
    session_id = state.get("session_id", "")
    print(f"[AGENT] Node: TicketCreator | query=\"{query}\"")

    ticket_id = save_ticket(query, user_intent=user_intent)

    # Reset clarification count after ticket creation
    if _session_mgr and session_id:
        _session_mgr.reset_clarification(session_id)

    answer = (
        f"Mình đã ghi nhận vấn đề của bạn do vấn đề này chưa có trong cơ sở dữ liệu của mình (ticket #{ticket_id}). "
        "Vui lòng nhắn lại yêu cầu vào nhóm Zalo hỗ trợ để được nhân viên kỹ thuật giải đáp."
    )

    print(f"[AGENT] Node: TicketCreator | ticket_id={ticket_id}")
    return {
        "ticket_id": ticket_id,
        "answer": answer,
    }


def node_chat_fallback(state: AgentState) -> dict:
    """Generate a short polite off-topic response."""
    query = state["query"]
    print(f"[AGENT] Node: ChatFallback | query=\"{query}\"")

    answer = chat_fallback(query)

    print(f"[AGENT] Node: ChatFallback | answer=\"{answer}\"")
    return {"answer": answer}


def node_clarifier(state: AgentState) -> dict:
    """Ask user for more detail (no LLM call — template only)."""
    session_id = state.get("session_id", "")
    user_intent = state.get("user_intent") or state["query"]

    count = _session_mgr.increment_clarification(session_id) if _session_mgr else 1

    print(f"[AGENT] Node: Clarifier | count={count}")

    if count == 1:
        answer = (
            f"Mình chưa tìm được thông tin phù hợp về: \"{user_intent}\".\n"
            "Bạn có thể mô tả chi tiết hơn không? "
            "Ví dụ: lỗi xảy ra ở module nào, màn hình hiển thị thông báo gì?"
        )
    else:
        answer = (
            "Mình vẫn chưa tìm được câu trả lời phù hợp. "
            "Bạn có thể cung cấp thêm thông tin không? "
            "Ví dụ: tên chức năng đang dùng, các bước đã thực hiện trước khi gặp lỗi."
        )

    return {"answer": answer, "tool_called": "clarifier"}


# --- Graph wiring ---

graph = StateGraph(AgentState)

# Add nodes
graph.add_node("query_analyzer", node_query_analyzer)
graph.add_node("tool_router", node_tool_router)
graph.add_node("retriever", node_retriever)
graph.add_node("synthesizer", node_synthesizer)
graph.add_node("generator", node_generator)
graph.add_node("ticket_creator", node_ticket_creator)
graph.add_node("chat_fallback", node_chat_fallback)
graph.add_node("clarifier", node_clarifier)

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
    lambda s: {
        "create_ticket": "ticket_creator",
        "search_faq": "generator",
        "clarify": "clarifier",
    }[s["intent"]]
)

# Linear edges
graph.add_edge("retriever", "synthesizer")
graph.add_edge("generator", END)
graph.add_edge("ticket_creator", END)
graph.add_edge("chat_fallback", END)
graph.add_edge("clarifier", END)

# Compile the graph
app = graph.compile()


# --- Public API (same signature as pipeline.py) ---

def run(message: Message, session_history: list) -> Answer:
    """
    Drop-in replacement for pipeline.run().
    Accepts a Message object and session history, returns an Answer.
    """
    # Short-circuit if maintenance mode is active
    if _maintenance_mode:
        print(f"[AGENT] Maintenance mode active — returning maintenance message")
        return Answer(
            text=MAINTENANCE_MESSAGE,
            confidence=0.0,
            source_chunks=[],
            is_fallback=True,
            rewritten_question="",
        )

    print(f"\n{'='*60}")
    print(f"[AGENT] Input: \"{message.text}\"")
    print(f"{'='*60}")

    initial_state: AgentState = {
        "query": message.text,
        "expanded_query": message.text,
        "is_ehc_related": False,
        "intent": "search_faq",
        "rewritten_query": "",
        "tool_called": "",
        "chunks": [],
        "confidence": 0.0,
        "answer": "",
        "ticket_id": None,
        "user_intent": None,
        "session_history": session_history,
        "session_id": message.session_id,
        "clarification_count": 0,
    }

    result = app.invoke(initial_state)

    # Build Answer object
    chunks = result.get("chunks", [])
    conf = result.get("confidence", 0.0)
    is_fallback = result.get("intent") in ("chat_fallback", "create_ticket", "clarify")
    rewritten = result.get("rewritten_query", "")

    answer = Answer(
        text=result["answer"],
        confidence=conf,
        source_chunks=chunks,
        is_fallback=is_fallback,
        rewritten_question=rewritten,
    )

    tool = result.get("tool_called", result.get("intent", "unknown"))
    print(f"\n[AGENT] Done | tool={tool} confidence={conf:.4f}")
    return answer


if __name__ == "__main__":
    print("=== LangGraph Agent — Standalone Test ===\n")

    # Set up a local session manager for standalone testing
    from api.session import SessionManager
    _test_mgr = SessionManager(max_turns=5, ttl_seconds=1800)
    set_session_manager(_test_mgr)

    test_queries = [
        ("không in được phiếu thu", "EHC question → search_faq → generator"),
        ("hello", "Off-topic → chat_fallback"),
    ]

    for query, description in test_queries:
        print(f"\n{'='*60}")
        print(f"TEST: {description}")
        print(f"{'='*60}")

        msg = Message(
            user_id="test", session_id="s1",
            text=query, timestamp=time.time(), platform="web"
        )
        answer = run(msg, [])
        print(f"\n  Bot: {answer.text}")
        print(f"  [confidence={answer.confidence:.2f} fallback={answer.is_fallback}]")

    print(f"\n{'='*60}")
    print("✓ All test queries completed.")
