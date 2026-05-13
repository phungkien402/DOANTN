"""
Query Logger — logs every query and its outcome to logs/queries.jsonl.

Fallback entries are especially important — they tell the helpdesk team
which questions need new FAQ entries.

The admin panel reads this file to display the log table.

Run standalone: python -m api.logger
"""

from dataclasses import dataclass, asdict
import json
import os
import time


@dataclass
class QueryLog:
    """A single logged query entry."""
    timestamp: float
    user_id: str
    platform: str
    question: str
    rewritten_question: str
    answer: str
    confidence: float
    is_fallback: bool
    top_chunk_subject: str  # FAQ title used (empty string if fallback)


class QueryLogger:
    """Appends query logs as JSON lines to a file."""

    def __init__(self, log_path: str = "logs/queries.jsonl"):
        self._log_path = log_path

    def log(self, message, answer) -> None:
        """
        Log a query and its answer.
        Uses answer.rewritten_question for the rewritten field.
        """
        os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
        entry = QueryLog(
            timestamp=time.time(),
            user_id=message.user_id,
            platform=message.platform,
            question=message.text,
            rewritten_question=answer.rewritten_question,
            answer=answer.text,
            confidence=answer.confidence,
            is_fallback=answer.is_fallback,
            top_chunk_subject=(
                answer.source_chunks[0].metadata.get("subject", "")
                if answer.source_chunks else ""
            ),
        )
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def read_logs(self, limit: int = 50, fallback_only: bool = False) -> list[dict]:
        """Read recent logs from the file."""
        if not os.path.exists(self._log_path):
            return []
        with open(self._log_path, encoding="utf-8") as f:
            lines = f.readlines()
        logs = [json.loads(line) for line in lines if line.strip()]
        if fallback_only:
            logs = [log for log in logs if log["is_fallback"]]
        return logs[-limit:]


if __name__ == "__main__":
    from core.models import Message, Answer

    logger = QueryLogger("logs/test_queries.jsonl")
    msg = Message(
        user_id="test", session_id="s1",
        text="test question", timestamp=0.0, platform="web"
    )
    ans = Answer(
        text="test answer", confidence=0.9,
        rewritten_question="rewritten test question"
    )
    logger.log(msg, ans)
    logs = logger.read_logs()
    print(f"Logged {len(logs)} entries: {logs}")
    print("✓ QueryLogger works correctly.")
