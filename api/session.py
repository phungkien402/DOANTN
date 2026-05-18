"""
Session Manager — stores per-user conversation history in memory.

Used by:
  - Pipeline: to provide multi-turn context to the orchestrator
  - Routes: to store user/bot turns

Limits history to SESSION_MAX_TURNS most recent turns.
Auto-resets sessions idle longer than TTL.

Run standalone: python -m api.session
"""

import time


class SessionManager:
    """In-memory session store keyed by session_id."""

    def __init__(self, max_turns: int = 10, ttl_seconds: int = 1800):
        self._sessions: dict[str, list[dict]] = {}
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

    def clear(self, session_id: str) -> None:
        """Clear a session's history."""
        self._sessions.pop(session_id, None)
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

    # TTL test
    print("\n--- TTL test ---")
    sm_ttl = SessionManager(max_turns=5, ttl_seconds=1)
    sm_ttl.add_turn("t1", "user", "test")
    assert sm_ttl.get_history("t1") != []
    print("Before TTL expiry: has history")
    time.sleep(1.1)
    assert sm_ttl.get_history("t1") == []
    print("After TTL expiry: session cleared")

    print("\n✓ SessionManager works correctly.")
