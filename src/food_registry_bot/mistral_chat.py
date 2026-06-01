from __future__ import annotations

import httpx


class MistralChatClientError(RuntimeError):
    pass


class MistralChatCompletionsClient:
    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.Client | None = None,
        base_url: str = "https://api.mistral.ai/v1",
    ) -> None:
        if not api_key.strip():
            raise ValueError("MISTRAL_API_KEY is required for Mistral chat client")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=60.0)

    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        response_format: dict | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        payload: dict = {
            "model": model,
            "messages": messages,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MistralChatClientError(f"Mistral request failed: {exc}") from exc

        return response.json()


def extract_mistral_message_text(response_json: dict) -> str:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        raise MistralChatClientError("Mistral returned a response without choices")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise MistralChatClientError("Mistral returned an invalid message payload")

    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for chunk in content:
            if not isinstance(chunk, dict):
                continue
            if chunk.get("type") == "text" and isinstance(chunk.get("text"), str):
                parts.append(chunk["text"])
        if parts:
            return "".join(parts)

    raise MistralChatClientError("Mistral returned an empty message payload")
