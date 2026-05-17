"""
Session Manager — stores per-user conversation history in memory.

Used by:
  - Fallback Handler: to know if clarification was already asked
  - Pipeline: to provide multi-turn context if needed
  - Clarification loop: tracks how many times we've asked for more detail

Limits history to SESSION_MAX_TURNS most recent turns.
Auto-resets sessions idle longer than TTL.

Run standalone: python -m api.session
"""

import time


class SessionManager:
    """In-memory session store keyed by session_id."""

    def __init__(self, max_turns: int = 10, ttl_seconds: int = 1800):
        self._sessions: dict[str, list[dict]] = {}
        self._clarification_counts: dict[str, int] = {}
        self._last_active: dict[str, float] = {}
        self._max_turns = max_turns
        self._ttl = ttl_seconds

    def _check_ttl(self, session_id: str) -> None:
        """Clear session if idle longer than TTL."""
        last = self._last_active.get(session_id, 0)
        if last and time.time() - last > self._ttl:
            self.clear(session_id)

    def get_history(self, session_id: str) -> list[dict]:
        """Get conversation history for a session."""
        self._check_ttl(session_id)
        return self._sessions.get(session_id, [])

    def add_turn(self, session_id: str, role: str, text: str) -> None:
        """Add a turn to the session history."""
        self._check_ttl(session_id)
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append({"role": role, "text": text})
        # Keep only the most recent N turns
        self._sessions[session_id] = self._sessions[session_id][-self._max_turns:]
        self._last_active[session_id] = time.time()

    def get_clarification_count(self, session_id: str) -> int:
        """Get current clarification count for a session."""
        self._check_ttl(session_id)
        return self._clarification_counts.get(session_id, 0)

    def increment_clarification(self, session_id: str) -> int:
        """Increment and return new clarification count."""
        self._check_ttl(session_id)
        count = self._clarification_counts.get(session_id, 0) + 1
        self._clarification_counts[session_id] = count
        self._last_active[session_id] = time.time()
        return count

    def reset_clarification(self, session_id: str) -> None:
        """Reset clarification count (after confident answer or ticket creation)."""
        self._clarification_counts.pop(session_id, None)

    def clear(self, session_id: str) -> None:
        """Clear a session's history and all tracking data."""
        self._sessions.pop(session_id, None)
        self._clarification_counts.pop(session_id, None)
        self._last_active.pop(session_id, None)


if __name__ == "__main__":
    print("=== SessionManager standalone test ===\n")

    # Basic history test
    sm = SessionManager(max_turns=3, ttl_seconds=1800)
    sm.add_turn("s1", "user", "hello")
    sm.add_turn("s1", "bot", "hi there")
    sm.add_turn("s1", "user", "how to merge records")
    sm.add_turn("s1", "bot", "Go to Administration...")
    print(f"History (max 3): {sm.get_history('s1')}")
    assert len(sm.get_history("s1")) == 3, "Should keep only 3 turns"

    print(f"Empty session: {sm.get_history('s2')}")
    sm.clear("s1")
    print(f"After clear: {sm.get_history('s1')}")

    # Clarification count test
    print("\n--- Clarification count test ---")
    assert sm.get_clarification_count("s2") == 0
    assert sm.increment_clarification("s2") == 1
    assert sm.increment_clarification("s2") == 2
    assert sm.increment_clarification("s2") == 3
    assert sm.get_clarification_count("s2") == 3
    sm.reset_clarification("s2")
    assert sm.get_clarification_count("s2") == 0
    print("Clarification counts: OK")

    # TTL test
    print("\n--- TTL test ---")
    sm_ttl = SessionManager(max_turns=5, ttl_seconds=1)
    sm_ttl.add_turn("t1", "user", "test")
    sm_ttl.increment_clarification("t1")
    assert sm_ttl.get_clarification_count("t1") == 1
    print("Before TTL expiry: count=1")
    time.sleep(1.1)
    assert sm_ttl.get_history("t1") == []
    assert sm_ttl.get_clarification_count("t1") == 0
    print("After TTL expiry: session cleared")

    print("\n✓ SessionManager works correctly.")
