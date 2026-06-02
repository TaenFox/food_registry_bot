from __future__ import annotations

import base64
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from food_registry_bot.conversation.context import (
    NutritionCoachConversationTurn,
    NutritionCoachFactualContext,
)
from food_registry_bot.conversation.llm_client import LLMConversationClientError
from food_registry_bot.extraction.request import ExtractionImageInput
from food_registry_bot.mistral_chat import (
    MistralChatClientError,
    MistralChatCompletionsClient,
    extract_mistral_message_text,
)


class NutritionCoachLLMReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_text: str = Field(min_length=1)
    updated_session_summary: Optional[str] = None


class NutritionCoachPostEntryComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment_text: Optional[str] = None


class MistralChatCompletionsConversationClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: MistralChatCompletionsClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("MISTRAL_API_KEY is required for Mistral conversation client")

        self._model = model
        self._client = client or MistralChatCompletionsClient(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "mistral_chat_completions"

    @property
    def model_name(self) -> str:
        return self._model

    def generate_reply(
        self,
        *,
        user_message: str,
        factual_context: NutritionCoachFactualContext,
        session_summary: str | None,
        recent_turns: list[NutritionCoachConversationTurn],
        images: tuple[ExtractionImageInput, ...] = (),
    ) -> tuple[str, str | None]:
        stripped_message = user_message.strip()
        if not stripped_message:
            raise LLMConversationClientError("Cannot generate a reply for an empty user message")

        try:
            response_json = self._client.complete(
                model=self._model,
                messages=[
                    {"role": "system", "content": self._build_system_prompt()},
                    {
                        "role": "user",
                        "content": self._build_user_content(
                            user_message=stripped_message,
                            factual_context=factual_context,
                            session_summary=session_summary,
                            recent_turns=recent_turns,
                            images=images,
                        ),
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "nutrition_coach_reply",
                        "schema": NutritionCoachLLMReply.model_json_schema(),
                    },
                },
            )
        except MistralChatClientError as exc:
            raise LLMConversationClientError(str(exc)) from exc

        output_text = extract_mistral_message_text(response_json).strip()
        if not output_text:
            raise LLMConversationClientError("Mistral returned an empty conversation response")

        try:
            parsed = NutritionCoachLLMReply.model_validate_json(output_text)
        except Exception:
            if output_text and not output_text.lstrip().startswith("{"):
                return output_text, session_summary
            raise LLMConversationClientError("Mistral returned an invalid coach response payload")

        normalized_summary = (
            parsed.updated_session_summary.strip()
            if parsed.updated_session_summary and parsed.updated_session_summary.strip()
            else session_summary
        )
        return parsed.reply_text.strip(), normalized_summary

    def generate_post_entry_comment(
        self,
        *,
        saved_items: list[str],
        factual_context: NutritionCoachFactualContext,
        metric_deltas: dict[str, float],
    ) -> str | None:
        if not saved_items:
            return None

        try:
            response_json = self._client.complete(
                model=self._model,
                messages=[
                    {"role": "system", "content": self._build_post_entry_system_prompt()},
                    {
                        "role": "user",
                        "content": self._build_post_entry_user_content(
                            saved_items=saved_items,
                            factual_context=factual_context,
                            metric_deltas=metric_deltas,
                        ),
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "nutrition_coach_post_entry_comment",
                        "schema": NutritionCoachPostEntryComment.model_json_schema(),
                    },
                },
            )
        except MistralChatClientError as exc:
            raise LLMConversationClientError(str(exc)) from exc

        output_text = extract_mistral_message_text(response_json).strip()
        if not output_text:
            raise LLMConversationClientError("Mistral returned an empty post-entry comment response")

        try:
            parsed = NutritionCoachPostEntryComment.model_validate_json(output_text)
        except Exception:
            if output_text and not output_text.lstrip().startswith("{"):
                return output_text
            raise LLMConversationClientError("Mistral returned an invalid post-entry comment payload")

        if parsed.comment_text is None:
            return None
        normalized_comment = parsed.comment_text.strip()
        return normalized_comment or None

    @staticmethod
    def _build_user_content(
        *,
        user_message: str,
        factual_context: NutritionCoachFactualContext,
        session_summary: str | None,
        recent_turns: list[NutritionCoachConversationTurn],
        images: tuple[ExtractionImageInput, ...],
    ) -> list[dict[str, str]]:
        content = [
            {"type": "text", "text": "Return valid json only."},
            {
                "type": "text",
                "text": (
                    "Use the factual day context below as the current source of truth. "
                    "Use session_summary and recent_turns as short-lived conversational memory."
                ),
            },
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "factual_context": factual_context.model_dump(mode="json"),
                        "session_summary": session_summary,
                        "recent_turns": [
                            {
                                "role": turn.role,
                                "content": turn.content,
                                "created_at": turn.created_at.isoformat(),
                            }
                            for turn in recent_turns
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
            {"type": "text", "text": user_message},
        ]
        for image in images:
            encoded_image = base64.b64encode(image.data).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": f"data:{image.media_type};base64,{encoded_image}",
                }
            )
        return content

    @staticmethod
    def _build_post_entry_user_content(
        *,
        saved_items: list[str],
        factual_context: NutritionCoachFactualContext,
        metric_deltas: dict[str, float],
    ) -> str:
        return json.dumps(
            {
                "saved_items": saved_items,
                "factual_context": factual_context.model_dump(mode="json"),
                "metric_deltas": metric_deltas,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "You are a concise nutrition coach inside a Telegram bot. "
            "Reply in Russian unless the user clearly asks otherwise. "
            "Use the factual context as the source of truth for today's numbers and avoid inventing data. "
            "If the user asks about something missing from the factual context, say so plainly and provide a cautious general answer. "
            "Return only the reply object, not the schema description."
        )

    @staticmethod
    def _build_post_entry_system_prompt() -> str:
        return (
            "You write a short, helpful post-entry nutrition comment for a Telegram bot in Russian. "
            "Keep it brief, specific to the saved food items and metric deltas, and avoid repeating raw totals unless useful. "
            "If there is nothing useful to add, return comment_text as null. "
            "Return only the comment object, not the schema description."
        )
