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
from contextvars import ContextVar

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.tracer as tracer

_current_run: ContextVar[dict] = ContextVar("_current_run", default=None)

from langgraph.graph import StateGraph, END

from openai import OpenAI
from config import CONFIDENCE_THRESHOLD, MAINTENANCE_MODE, VLLM_BASE_URL, VLLM_MODEL, RETRIEVER_TOP_K, RERANKER_TOP_N
from core.models import Message, Answer, RetrievedChunk
from core.intent_guard import classify, chat_fallback
from core.tools.search_faq import search_faq, full_retrieve_and_rerank
from core.tools.create_ticket import save_ticket
from core import generator, confidence, retriever, reranker
from core.query_rewriter import analyze_and_rewrite
from core.generator import LLMUnavailableError

# Module-level vLLM client for clarifier
_client = OpenAI(base_url=f"{VLLM_BASE_URL}/v1", api_key="not-needed")


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
    fast_chunks: list             # top 3 chunks from step 1, used by clarifier
    confidence: float
    answer: str
    ticket_id: Optional[int]
    user_intent: Optional[str]    # intent description from analyze_and_rewrite
    session_history: list         # conversation history
    session_id: str               # needed to look up clarification count
    clarification_count: int      # how many times we've asked for clarification
    answerable: str               # "yes" | "no" | "unclear" from analyze_and_rewrite


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
    t0 = time.time()
    query = state["query"]
    session_id = state.get("session_id", "")
    print(f"\n[AGENT] Node: QueryAnalyzer | query=\"{query}\"")

    # If user is in clarification loop, bypass classifier
    if _session_mgr:
        count = _session_mgr.get_clarification_count(session_id)
        if count > 0:
            # Detect if user replied with a number (e.g. "1", "2") or free-text
            stripped = query.strip()
            if stripped.isdigit():
                print(f"[AGENT] Classifier: BYPASS (clarification_count={count}, numeric reply)")
                result = {"is_ehc_related": True, "intent": "search_faq"}
                run = _current_run.get()
                if run:
                    tracer.log_node(run, "QueryAnalyzer",
                        {"query": query},
                        {"is_ehc_related": True, "intent": "search_faq"},
                        llm={"result": "BYPASS_NUMERIC"},
                        duration_ms=int((time.time()-t0)*1000))
                return result
            else:
                print(f"[AGENT] Classifier: BYPASS (clarification_count={count}, free-text → block_x)")
                result = {"is_ehc_related": True, "intent": "block_x"}
                run = _current_run.get()
                if run:
                    tracer.log_node(run, "QueryAnalyzer",
                        {"query": query},
                        {"is_ehc_related": True, "intent": "block_x"},
                        llm={"result": "BYPASS_FREETEXT"},
                        duration_ms=int((time.time()-t0)*1000))
                return result

    is_off_topic = classify(query)

    if is_off_topic:
        print(f"[AGENT] Classifier: NO (off-topic)")
        result = {"is_ehc_related": False, "intent": "chat_fallback"}
    else:
        print(f"[AGENT] Classifier: YES (EHC-related)")
        result = {"is_ehc_related": True, "intent": "search_faq"}

    run = _current_run.get()
    if run:
        tracer.log_node(run, "QueryAnalyzer",
            {"query": query},
            {"is_ehc_related": result["is_ehc_related"], "intent": result["intent"]},
            llm={"result": "NO" if is_off_topic else "YES"},
            duration_ms=int((time.time()-t0)*1000))
    return result


def node_tool_router(state: AgentState) -> dict:
    """Route to the appropriate tool based on intent."""
    t0 = time.time()
    intent = state["intent"]
    print(f"[AGENT] Node: ToolRouter | intent={intent}")

    if intent == "create_ticket":
        tool = "ticket_creator"
    elif intent == "block_x":
        tool = "block_x"
    else:
        tool = "retriever"

    run = _current_run.get()
    if run:
        tracer.log_node(run, "ToolRouter",
            {"intent": intent},
            {"tool_called": tool},
            duration_ms=int((time.time()-t0)*1000))
    return {"tool_called": tool}


def node_retriever(state: AgentState) -> dict:
    """Execute the full RAG search pipeline."""
    t0 = time.time()
    query = state["query"]
    session_id = state.get("session_id", "")
    session_history = state.get("session_history", [])
    print(f"[AGENT] Node: Retriever | query=\"{query}\"")

    # If in clarification loop, reuse saved fast_chunks — don't retrieve with short answer like "2"
    saved_fast_chunks = None
    if _session_mgr:
        count = _session_mgr.get_clarification_count(session_id)
        if count > 0:
            saved_fast_chunks = _session_mgr.get_fast_chunks(session_id)
            if saved_fast_chunks:
                print(f"[AGENT] Node: Retriever | reusing {len(saved_fast_chunks)} saved fast_chunks")

    chunks, rewritten, user_intent, answerable, fast_chunks = search_faq(
        query, session_history=session_history, saved_fast_chunks=saved_fast_chunks
    )

    print(f"[AGENT] Node: Retriever | rewritten=\"{rewritten}\" | answerable={answerable}")

    # Route directly to clarifier if answerable=unclear/no (steps 3+4 were skipped)
    next_intent = "clarify" if answerable in ("unclear", "no") else state.get("intent", "search_faq")

    run = _current_run.get()
    if run:
        tracer.log_node(run, "Retriever",
            {"query": query},
            {"rewritten_query": rewritten, "answerable": answerable, "chunks_count": len(chunks)},
            llm={"rewritten": rewritten, "answerable": answerable},
            duration_ms=int((time.time()-t0)*1000))

    return {
        "chunks": chunks,
        "rewritten_query": rewritten,
        "user_intent": user_intent,
        "answerable": answerable,
        "fast_chunks": fast_chunks,
        "intent": next_intent,
    }


def node_synthesizer(state: AgentState) -> dict:
    """Check rerank confidence of retrieved chunks and decide next route.

    Only reached when answerable=yes (unclear/no are routed to clarifier earlier).
    Uses rerank score as a secondary quality gate.
    """
    t0 = time.time()
    chunks = state["chunks"]
    session_id = state.get("session_id", "")

    top_score = chunks[0].score if chunks else 0.0

    is_confident = chunks and confidence.is_confident(chunks[0], threshold=CONFIDENCE_THRESHOLD)

    if is_confident:
        print(f"[AGENT] Node: Synthesizer | confidence={top_score:.4f} → CONFIDENT")
        decision = "CONFIDENT"
        result = {"confidence": top_score, "intent": "search_faq"}
    else:
        # Low rerank score despite answerable=yes — ask for clarification
        count = _session_mgr.get_clarification_count(session_id) if _session_mgr else 0
        print(f"[AGENT] Node: Synthesizer | confidence={top_score:.4f} → LOW | clarification_count={count}")
        if count >= 3:
            decision = "LOW_MAX_CLARIFY"
            result = {"confidence": top_score, "intent": "create_ticket"}
        else:
            decision = "LOW"
            result = {"confidence": top_score, "intent": "clarify"}

    run = _current_run.get()
    if run:
        tracer.log_node(run, "Synthesizer",
            {"confidence": round(top_score, 4)},
            {"decision": decision, "next_intent": result["intent"]},
            duration_ms=int((time.time()-t0)*1000))
    return result


def node_generator(state: AgentState) -> dict:
    """Generate a grounded answer from retrieved chunks."""
    t0 = time.time()
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

    run = _current_run.get()
    if run:
        tracer.log_node(run, "Generator",
            {"chunks_count": len(chunks), "intent": state.get("intent", "")},
            {"answer_preview": answer_text[:100]},
            llm={"tokens": len(answer_text)},
            duration_ms=int((time.time()-t0)*1000))
    return {"answer": answer_text}


def node_ticket_creator(state: AgentState) -> dict:
    """Create a ticket for low-confidence queries."""
    t0 = time.time()
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

    run = _current_run.get()
    if run:
        tracer.log_node(run, "TicketCreator",
            {"query": query},
            {"ticket_id": ticket_id},
            duration_ms=int((time.time()-t0)*1000))
    return {
        "ticket_id": ticket_id,
        "answer": answer,
    }


def node_chat_fallback(state: AgentState) -> dict:
    """Generate a short polite off-topic response."""
    t0 = time.time()
    query = state["query"]
    print(f"[AGENT] Node: ChatFallback | query=\"{query}\"")

    answer = chat_fallback(query)

    print(f"[AGENT] Node: ChatFallback | answer=\"{answer}\"")

    run = _current_run.get()
    if run:
        tracer.log_node(run, "ChatFallback",
            {"query": query},
            {"answer_preview": answer[:100]},
            duration_ms=int((time.time()-t0)*1000))
    return {"answer": answer}


CLARIFIER_PROMPT = (
    "Bạn là trợ lý hỗ trợ phần mềm EHC. "
    "Người dùng hỏi một câu chưa rõ ràng. "
    "Dựa vào câu hỏi và danh sách vấn đề liên quan bên dưới, "
    "hãy hỏi lại người dùng bằng cách đưa ra các lựa chọn cụ thể (dạng danh sách đánh số). "
    "Giọng thân thiện, ngắn gọn. Không giải thích thêm. "
    "Kết thúc bằng: 'Bạn đang gặp vấn đề nào trong các trường hợp trên? "
    "Nếu không có trường hợp nào phù hợp, bạn có thể mô tả chi tiết vấn đề bằng lời của mình.'"
)


def node_clarifier(state: AgentState) -> dict:
    """Ask user for more detail using LLM + fast_chunks choices."""
    t0 = time.time()
    session_id = state.get("session_id", "")
    query = state["query"]
    fast_chunks = state.get("fast_chunks", [])
    count = _session_mgr.increment_clarification(session_id) if _session_mgr else 1

    # Save fast_chunks so next turn can reuse them instead of re-retrieving with "2"
    if _session_mgr and fast_chunks:
        _session_mgr.set_fast_chunks(session_id, fast_chunks)

    print(f"[AGENT] Node: Clarifier | count={count} | chunks={len(fast_chunks)}")

    # Reuse saved options from first clarification turn to keep ordering consistent
    existing_options = _session_mgr.get_clarification_options(session_id) if _session_mgr else ""
    if existing_options:
        answer = existing_options
        print(f"[AGENT] Node: Clarifier | reusing saved options from Turn 1")
    elif fast_chunks and _client is not None:
        # Build choice list from fast_chunks subjects
        choices = "\n".join(
            f"{i}. {c.metadata.get('subject', c.text[:60])}"
            for i, c in enumerate(fast_chunks, 1)
        )
        user_content = f"Câu hỏi của người dùng: \"{query}\"\n\nCác vấn đề liên quan:\n{choices}"

        try:
            response = _client.chat.completions.create(
                model=VLLM_MODEL,
                messages=[
                    {"role": "system", "content": CLARIFIER_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=200,
                temperature=0.2,
            )
            answer = response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[AGENT] Clarifier LLM failed: {e}, using fallback template")
            answer = _clarifier_fallback(query, fast_chunks, count)

        # Save options for subsequent turns
        if _session_mgr:
            _session_mgr.set_clarification_options(session_id, answer)
    else:
        answer = _clarifier_fallback(query, fast_chunks, count)
        # Save fallback options too
        if _session_mgr:
            _session_mgr.set_clarification_options(session_id, answer)

    print(f"[AGENT] Node: Clarifier | answer=\"{answer[:80]}...\"")

    run = _current_run.get()
    if run:
        chunk_titles = [c.metadata.get("subject", c.text[:40]) for c in fast_chunks[:5]]
        tracer.log_node(run, "Clarifier",
            {"chunks_titles": chunk_titles},
            {"answer_preview": answer[:100]},
            duration_ms=int((time.time()-t0)*1000))
    return {"answer": answer, "tool_called": "clarifier"}


def _clarifier_fallback(query: str, fast_chunks: list, count: int) -> str:
    """Template fallback if LLM unavailable."""
    if fast_chunks:
        choices = "\n".join(
            f"{i}. {c.metadata.get('subject', c.text[:60])}"
            for i, c in enumerate(fast_chunks, 1)
        )
        return (
            f"Mình chưa xác định rõ vấn đề của bạn. "
            f"Bạn đang gặp vấn đề nào trong các trường hợp sau?\n{choices}\n\n"
            "Bạn đang gặp vấn đề nào trong các trường hợp trên?"
        )
    return (
        "Mình chưa tìm được thông tin phù hợp. "
        "Bạn có thể mô tả chi tiết hơn không? "
        "Ví dụ: lỗi xảy ra ở module nào, màn hình hiển thị thông báo gì?"
    )


def node_block_x(state: AgentState) -> dict:
    """Block X: Synthesis node for free-text clarification responses.

    When user replies with free-text (not a number) during clarification loop:
    1. Retrieve saved_fast_chunks from session
    2. Call analyze_and_rewrite with chunks + history to resolve intent
    3. If answerable=yes → full retrieve + rerank → route to synthesizer
    4. If answerable=unclear/no → route to ticket_creator
    """
    t0 = time.time()
    query = state["query"]
    session_id = state.get("session_id", "")
    session_history = state.get("session_history", [])
    print(f"[AGENT] Node: BlockX | query=\"{query}\"")

    # Get saved fast_chunks from clarification session
    saved_fast_chunks = []
    if _session_mgr:
        saved_fast_chunks = _session_mgr.get_fast_chunks(session_id) or []
    print(f"[AGENT] Node: BlockX | saved_fast_chunks={len(saved_fast_chunks)}")

    # Analyze + rewrite using saved chunks as context
    user_intent = None
    rewritten = query
    answerable = "unclear"
    try:
        if saved_fast_chunks:
            user_intent, rewritten, answerable = analyze_and_rewrite(
                query, chunks=saved_fast_chunks, session_history=session_history
            )
        else:
            user_intent, rewritten, answerable = analyze_and_rewrite(
                query, session_history=session_history
            )
    except LLMUnavailableError:
        print("[AGENT] Node: BlockX | vLLM unavailable, defaulting to ticket")
        answerable = "no"

    print(f"[AGENT] Node: BlockX | rewritten=\"{rewritten}\" answerable={answerable}")

    # If answerable=yes or unclear → attempt full retrieve + rerank
    if answerable == "yes" or answerable == "unclear":
        print(f"[AGENT] Node: BlockX | answerable={answerable} → full retrieve + rerank")
        ranked_chunks, top_score = full_retrieve_and_rerank(rewritten)

        # If unclear and confidence below threshold → fall back to ticket
        if answerable == "unclear" and top_score < CONFIDENCE_THRESHOLD:
            print(f"[AGENT] Node: BlockX | confidence={top_score:.4f} below threshold → ticket_creator")
            if _session_mgr and session_id:
                _session_mgr.reset_clarification(session_id)
            run = _current_run.get()
            if run:
                tracer.log_node(run, "BlockX",
                    {"query": query, "saved_chunks": len(saved_fast_chunks)},
                    {"rewritten": rewritten, "answerable": answerable, "confidence": round(top_score, 4), "route": "ticket"},
                    llm={"rewritten": rewritten, "answerable": answerable},
                    duration_ms=int((time.time()-t0)*1000))
            return {
                "chunks": [],
                "rewritten_query": rewritten,
                "user_intent": user_intent,
                "answerable": answerable,
                "confidence": top_score,
                "fast_chunks": saved_fast_chunks,
                "intent": "create_ticket",
            }

        # Confident enough → route to synthesizer
        if _session_mgr and session_id:
            _session_mgr.reset_clarification(session_id)

        run = _current_run.get()
        if run:
            tracer.log_node(run, "BlockX",
                {"query": query, "saved_chunks": len(saved_fast_chunks)},
                {"rewritten": rewritten, "answerable": answerable, "confidence": round(top_score, 4), "route": "synthesizer"},
                llm={"rewritten": rewritten, "answerable": answerable},
                duration_ms=int((time.time()-t0)*1000))

        return {
            "chunks": ranked_chunks,
            "rewritten_query": rewritten,
            "user_intent": user_intent,
            "answerable": answerable,
            "confidence": top_score,
            "fast_chunks": saved_fast_chunks,
            "intent": "search_faq",  # route to synthesizer
        }

    else:
        # answerable=no → route to ticket_creator immediately
        print(f"[AGENT] Node: BlockX | answerable=no → ticket_creator immediately")

        # Reset clarification
        if _session_mgr and session_id:
            _session_mgr.reset_clarification(session_id)

        run = _current_run.get()
        if run:
            tracer.log_node(run, "BlockX",
                {"query": query, "saved_chunks": len(saved_fast_chunks)},
                {"rewritten": rewritten, "answerable": answerable, "route": "ticket_no"},
                llm={"rewritten": rewritten, "answerable": answerable},
                duration_ms=int((time.time()-t0)*1000))

        return {
            "chunks": [],
            "rewritten_query": rewritten,
            "user_intent": user_intent,
            "answerable": answerable,
            "fast_chunks": saved_fast_chunks,
            "intent": "create_ticket",
        }


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
graph.add_node("block_x", node_block_x)

# Set entry point
graph.set_entry_point("query_analyzer")

# Conditional edges
graph.add_conditional_edges(
    "query_analyzer",
    lambda s: "chat_fallback" if not s["is_ehc_related"] else "tool_router"
)
graph.add_conditional_edges(
    "tool_router",
    lambda s: s["tool_called"]  # "retriever" or "ticket_creator" or "block_x"
)
graph.add_conditional_edges(
    "synthesizer",
    lambda s: {
        "create_ticket": "ticket_creator",
        "search_faq": "generator",
        "clarify": "clarifier",
    }[s["intent"]]
)

# Retriever → conditional: clarifier (if answerable=unclear/no) or synthesizer
graph.add_conditional_edges(
    "retriever",
    lambda s: "clarifier" if s.get("intent") == "clarify" else "synthesizer"
)

# BlockX → conditional: synthesizer (if answerable=yes) or ticket_creator
graph.add_conditional_edges(
    "block_x",
    lambda s: "synthesizer" if s.get("intent") == "search_faq" else "ticket_creator"
)

# Linear edges
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

    # Start trace
    run_ctx = tracer.new_run(message.session_id, message.text)
    _current_run.set(run_ctx)

    initial_state: AgentState = {
        "query": message.text,
        "expanded_query": message.text,
        "is_ehc_related": False,
        "intent": "search_faq",
        "rewritten_query": "",
        "tool_called": "",
        "chunks": [],
        "fast_chunks": [],
        "confidence": 0.0,
        "answer": "",
        "ticket_id": None,
        "user_intent": None,
        "session_history": session_history,
        "session_id": message.session_id,
        "clarification_count": 0,
        "answerable": "unclear",
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

    # Finish trace
    tracer.finish_run(run_ctx, tool, conf, answer.text)

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
