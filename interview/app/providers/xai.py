"""xAI API adapter via OpenAI-compatible endpoint."""

from __future__ import annotations

from openai import OpenAI

from app.config import Settings
from app.providers.base import LLMProvider, Message


class XAIProvider(LLMProvider):
    name = "xai"

    def __init__(self, settings: Settings) -> None:
        if not settings.xai_api_key:
            raise ValueError("XAI_API_KEY is not configured")
        self._settings = settings
        self._client = OpenAI(api_key=settings.xai_api_key, base_url="https://api.x.ai/v1")

    def generate(self, system: str, messages: list[Message], json_mode: bool = False) -> str:
        payload = [{"role": "system", "content": system}]
        for message in messages:
            if message.role == "system":
                continue
            payload.append({"role": message.role, "content": message.content})

        kwargs: dict = {
            "model": self._settings.model_xai,
            "messages": payload,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("xAI API returned empty response")
        return content
