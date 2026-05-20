from __future__ import annotations

from typing import Protocol


class LLMConversationClientError(RuntimeError):
    pass


class LLMConversationClient(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> str:
        ...

    def generate_reply(self, *, user_message: str) -> str:
        """Return a plain-text conversational reply."""
