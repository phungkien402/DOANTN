"""
Session Manager — stores per-user conversation history in memory.

Used by:
  - Fallback Handler: to know if clarification was already asked
  - Pipeline: to provide multi-turn context if needed

Limits history to SESSION_MAX_TURNS most recent turns.

Run standalone: python -m api.session
"""


class SessionManager:
    """In-memory session store keyed by session_id."""

    def __init__(self, max_turns: int = 10):
        self._sessions: dict[str, list[dict]] = {}
        self._max_turns = max_turns

    def get_history(self, session_id: str) -> list[dict]:
        """Get conversation history for a session."""
        return self._sessions.get(session_id, [])

    def add_turn(self, session_id: str, role: str, text: str) -> None:
        """Add a turn to the session history."""
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append({"role": role, "text": text})
        # Keep only the most recent N turns
        self._sessions[session_id] = self._sessions[session_id][-self._max_turns:]

    def clear(self, session_id: str) -> None:
        """Clear a session's history."""
        self._sessions.pop(session_id, None)


if __name__ == "__main__":
    sm = SessionManager(max_turns=3)
    sm.add_turn("s1", "user", "hello")
    sm.add_turn("s1", "bot", "hi there")
    sm.add_turn("s1", "user", "how to merge records")
    sm.add_turn("s1", "bot", "Go to Administration...")
    print(f"History (max 3): {sm.get_history('s1')}")
    print(f"Empty session: {sm.get_history('s2')}")
    sm.clear("s1")
    print(f"After clear: {sm.get_history('s1')}")
    print("\n✓ SessionManager works correctly.")
