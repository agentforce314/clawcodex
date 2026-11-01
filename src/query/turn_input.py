"""A thread-safe inbox for input delivered to one active query, not a new turn."""

from __future__ import annotations

import threading

from src.types.messages import UserMessage


class TurnInput:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: list[UserMessage] = []
        self._closed = False

    def put(self, message: UserMessage) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._messages.append(message)
            return True

    def drain(self, *, finishing: bool = False) -> list[UserMessage]:
        with self._lock:
            messages, self._messages = self._messages, []
            # Checking for final input and closing admission are atomic. An
            # acknowledged message can never miss the loop's last check.
            if finishing and not messages:
                self._closed = True
            return messages

    def close(self) -> list[UserMessage]:
        """Return undelivered input on an error, interrupt, or limit exit."""
        with self._lock:
            self._closed = True
            messages, self._messages = self._messages, []
            return messages
