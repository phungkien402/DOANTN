"""
BaseAdapter — Abstract interface for all platform adapters.

Every adapter (Zalo, Telegram, Web) must implement 3 methods:
  - parse_message(raw) -> Message | None
  - format_response(answer_text, confidence) -> str
  - send_message(user_id, text) -> None

The RAG Core (core/) must never import from this package.
Only api/routes.py interacts with adapters.
"""

from abc import ABC, abstractmethod
from core.models import Message


class BaseAdapter(ABC):

    @abstractmethod
    def parse_message(self, raw: dict) -> Message | None:
        """
        Accept a raw webhook payload from the platform.
        Return a standard Message object, or None if the event should
        be ignored (e.g. delivery receipts, typing indicators).
        """
        ...

    @abstractmethod
    def format_response(self, answer_text: str, confidence: float) -> str:
        """
        Format the answer text according to the platform's conventions.
        """
        ...

    @abstractmethod
    async def send_message(self, user_id: str, text: str) -> None:
        """
        Send a message back to the user via the platform's API.
        """
        ...
