from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from food_registry_bot.extraction.request import JournalExtractionRequest


JOURNAL = "journal"
CONVERSATION = "conversation"
AMBIGUOUS = "ambiguous"

_QUESTION_PREFIXES = (
    "как",
    "что",
    "почему",
    "зачем",
    "когда",
    "сколько",
    "какой",
    "какая",
    "какие",
    "можно ли",
    "нужно ли",
    "стоит ли",
)
_CONVERSATION_PATTERNS = (
    "подскажи",
    "посоветуй",
    "помоги",
    "объясни",
    "что думаешь",
    "хочу похуд",
    "хочу набрат",
    "как лучше",
    "план питания",
    "режим питания",
    "дефицит калорий",
    "набор массы",
    "трениров",
    "самочувств",
)
_PHOTO_CONVERSATION_PATTERNS = (
    "что приготовить",
    "что лучше приготовить",
    "что можно приготовить",
    "что съесть",
    "что лучше съесть",
    "что выбрать",
    "из этого",
    "из этих продуктов",
    "в холодильнике",
    "вот что есть",
    "вот продукты",
)
_JOURNAL_PREFIXES = (
    "лог:",
    "запиши",
    "добавь",
    "съел",
    "съела",
    "ел",
    "ела",
    "поел",
    "поела",
    "позавтракал",
    "позавтракала",
    "пообедал",
    "пообедала",
    "поужинал",
    "поужинала",
    "перекусил",
    "перекусила",
    "выпил",
    "выпила",
    "вода",
    "water",
)
_QUANTITY_PATTERN = re.compile(r"^\s*\d+\s*(мл|ml|г|гр|кг|kg)\b", re.IGNORECASE)
_WORD_PATTERN = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class MessageRoutingDecision:
    route: str
    reason: str


class MessageRoutingService(Protocol):
    def route(
        self,
        request: JournalExtractionRequest,
        *,
        has_active_conversation_session: bool = False,
    ) -> MessageRoutingDecision:
        """Choose journal, conversation, or ambiguous route."""


class RuleBasedMessageRoutingService:
    def route(
        self,
        request: JournalExtractionRequest,
        *,
        has_active_conversation_session: bool = False,
    ) -> MessageRoutingDecision:
        if request.images:
            if request.text:
                lowered_photo_text = request.text.strip().lower()
                if lowered_photo_text.startswith("/"):
                    return MessageRoutingDecision(route=AMBIGUOUS, reason="slash_like_photo_text")
                if self._looks_like_conversation_photo(
                    lowered_photo_text,
                ):
                    return MessageRoutingDecision(route=CONVERSATION, reason="photo_conversation_cue")
            return MessageRoutingDecision(route=JOURNAL, reason="photo_message")

        if not request.text:
            return MessageRoutingDecision(route=AMBIGUOUS, reason="empty_text")

        normalized_text = request.text.strip()
        lowered_text = normalized_text.lower()

        if lowered_text.startswith("{"):
            return MessageRoutingDecision(route=JOURNAL, reason="structured_payload")

        if lowered_text.startswith("/"):
            return MessageRoutingDecision(route=AMBIGUOUS, reason="slash_like_text")

        if self._looks_conversational(lowered_text):
            return MessageRoutingDecision(route=CONVERSATION, reason="conversation_cue")

        if self._looks_like_journal(lowered_text):
            return MessageRoutingDecision(route=JOURNAL, reason="journal_cue")

        if has_active_conversation_session:
            return MessageRoutingDecision(route=CONVERSATION, reason="active_conversation_session")

        if self._word_count(lowered_text) <= 6:
            return MessageRoutingDecision(route=JOURNAL, reason="short_fact_like_text")

        return MessageRoutingDecision(route=AMBIGUOUS, reason="insufficient_confidence")

    @staticmethod
    def _looks_conversational(text: str) -> bool:
        if "?" in text:
            return True

        if text.startswith(_QUESTION_PREFIXES):
            return True

        return any(pattern in text for pattern in _CONVERSATION_PATTERNS)

    @staticmethod
    def _looks_like_journal(text: str) -> bool:
        if text.startswith(_JOURNAL_PREFIXES):
            return True

        return _QUANTITY_PATTERN.match(text) is not None

    @staticmethod
    def _looks_like_conversation_photo(
        text: str,
    ) -> bool:
        if RuleBasedMessageRoutingService._looks_conversational(text):
            return True

        if any(pattern in text for pattern in _PHOTO_CONVERSATION_PATTERNS):
            return True

        return False

    @staticmethod
    def _word_count(text: str) -> int:
        return len(_WORD_PATTERN.findall(text))
