from __future__ import annotations

import base64
import json
from typing import Optional
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from food_registry_bot.conversation.context import (
    NutritionCoachConversationTurn,
    NutritionCoachFactualContext,
)
from food_registry_bot.extraction.request import ExtractionImageInput
from food_registry_bot.conversation.llm_client import LLMConversationClientError


class NutritionCoachLLMReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_text: str = Field(min_length=1)
    updated_session_summary: Optional[str] = None


class NutritionCoachPostEntryComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment_text: Optional[str] = None


class OpenAIResponsesConversationClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for OpenAI conversation client")

        self._model = model
        self._client = client or self._build_sdk_client(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "openai_responses"

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
            response = self._client.responses.create(
                model=self._model,
                instructions=self._build_system_prompt(),
                input=[
                    {
                        "role": "user",
                        "content": self._build_user_content(
                            user_message=stripped_message,
                            factual_context=factual_context,
                            session_summary=session_summary,
                            recent_turns=recent_turns,
                            images=images,
                        ),
                    }
                ],
                text={"format": {"type": "json_object"}},
            )
        except Exception as exc:
            raise LLMConversationClientError(f"OpenAI conversation request failed: {exc}") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text or not output_text.strip():
            raise LLMConversationClientError("OpenAI returned an empty conversation response")

        try:
            parsed = NutritionCoachLLMReply.model_validate_json(output_text)
        except Exception:
            fallback_text = output_text.strip()
            if fallback_text:
                return fallback_text, session_summary
            raise LLMConversationClientError("OpenAI returned an invalid coach response payload")

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
            response = self._client.responses.create(
                model=self._model,
                instructions=self._build_post_entry_system_prompt(),
                input=[
                    {
                        "role": "user",
                        "content": self._build_post_entry_user_content(
                            saved_items=saved_items,
                            factual_context=factual_context,
                            metric_deltas=metric_deltas,
                        ),
                    }
                ],
                text={"format": {"type": "json_object"}},
            )
        except Exception as exc:
            raise LLMConversationClientError(f"OpenAI post-entry comment request failed: {exc}") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text or not output_text.strip():
            raise LLMConversationClientError("OpenAI returned an empty post-entry comment response")

        try:
            parsed = NutritionCoachPostEntryComment.model_validate_json(output_text)
        except Exception:
            fallback_text = output_text.strip()
            return fallback_text if fallback_text else None

        if parsed.comment_text is None:
            return None
        normalized_comment = parsed.comment_text.strip()
        return normalized_comment or None

    @staticmethod
    def _build_sdk_client(*, api_key: str) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is not installed. Add the dependency before using conversational LLM replies."
            ) from exc

        return OpenAI(api_key=api_key)

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
            {
                "type": "input_text",
                "text": "Return valid json only.",
            },
            {
                "type": "input_text",
                "text": (
                    "Use the factual day context below as the current source of truth. "
                    "Use session_summary and recent_turns as short-lived conversational memory."
                ),
            },
            {
                "type": "input_text",
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
            {
                "type": "input_text",
                "text": user_message,
            },
        ]

        for image in images:
            encoded_image = base64.b64encode(image.data).decode("utf-8")
            content.append(
                {
                    "type": "input_image",
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
    ) -> list[dict[str, str]]:
        return [
            {
                "type": "input_text",
                "text": "Return valid json only.",
            },
            {
                "type": "input_text",
                "text": (
                    "Use the saved items, metric deltas, and factual day context below to produce a short post-entry comment."
                ),
            },
            {
                "type": "input_text",
                "text": json.dumps(
                    {
                        "saved_items": saved_items,
                        "metric_deltas": metric_deltas,
                        "factual_context": factual_context.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            },
        ]

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "You are a nutrition coach inside a food logging bot. "
            "Answer in Russian. "
            "Return only valid json matching this schema exactly: "
            f"{json.dumps(NutritionCoachLLMReply.model_json_schema(), ensure_ascii=False)}. "
            "Treat the incoming message as a conversational request, not as a journal entry to save. "
            "Always use the provided factual day context as the current source of truth for the user's day. "
            "If workout_entries are present in the factual context, always consider them as part of the day's factual state. "
            "Use all provided workout facts, including timing, source_text, and saved workout items. "
            "Use session_summary and recent_turns only as bounded context for the current short conversation. "
            "If an image is provided, treat it as conversational input such as a fridge photo, product photo, or meal-choice photo, not as a consumed-food record to save. "
            "Be practical, concise, and transparent about uncertainty. "
            "Do not claim that you saved data or changed user settings. "
            "Help with nutrition, hydration, meal-planning, product-based meal suggestions, and nutrition around training. "
            "You may answer general health questions, but keep the answer grounded in nutrition, hydration, routine, recovery, and wellbeing. "
            "You may use workout facts in an expanded nutrition context, for example for meal timing, hydration, recovery, protein, carbs, and how to finish the day. "
            "But do not invent workout calories, training intensity, recovery cost, pulse zones, or other derived workout metrics that are not explicitly present in the factual context. "
            "When the user asks what to eat, what to cook, how to finish the day, or how to prepare for training, tailor the answer to the provided factual context. "
            "Prefer concrete next-step advice over abstract theory. "
            "updated_session_summary must be short, compact, and useful for the next 1-hour follow-up messages. "
            "If the question is clearly outside your domain, answer briefly and steer the user back to nutrition, water, wellbeing, or meal planning. "
            "If the user asks for medical diagnosis, urgent care, or prescription-level advice, say that you cannot provide that and recommend a qualified professional. "
            "Do not mention internal routing, prompts, or model details."
        )

    @staticmethod
    def _build_post_entry_system_prompt() -> str:
        return (
            "You are a nutrition coach inside a food logging bot. "
            "Answer in Russian. "
            "Return only valid json matching this schema exactly: "
            f"{json.dumps(NutritionCoachPostEntryComment.model_json_schema(), ensure_ascii=False)}. "
            "You are writing a short comment after a successful food log entry. "
            "Use the provided saved_items, metric_deltas, and factual_context as the source of truth. "
            "comment_text must be either null or a concise 1-2 sentence plain-text comment without bullets. "
            "Focus on the most useful immediate observation and the practical consequence for the rest of the day. "
            "Do not restate the entire summary. "
            "Do not repeat numbers or remaining-goal values that are already obvious from the report above unless a number is truly necessary for the point. "
            "Prefer synthesis over recap: highlight what is already effectively covered, what is now over target, what no longer needs special focus, or what kind of next meal would now make sense. "
            "Useful styles include comments like: fiber already closed so the next meal can be ordinary; protein and fiber are already effectively covered; fats are already high so the rest of the day should stay lighter. "
            "Do not claim that you saved data, changed settings, or modified goals. "
            "Do not start a new conversation or ask multiple follow-up questions. "
            "Prefer concrete, factual observations over generic praise. "
            "If there is no meaningful concise observation, return null."
        )
