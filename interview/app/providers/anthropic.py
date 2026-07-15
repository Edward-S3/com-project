"""Anthropic API adapter."""

from __future__ import annotations

from anthropic import Anthropic

from app.config import Settings
from app.providers.base import LLMProvider, Message


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured")
        self._settings = settings
        self._client = Anthropic(api_key=settings.anthropic_api_key)

    def generate(self, system: str, messages: list[Message], json_mode: bool = False) -> str:
        del json_mode
        payload = []
        for message in messages:
            if message.role == "system":
                continue
            payload.append({"role": message.role, "content": message.content})

        response = self._client.messages.create(
            model=self._settings.model_anthropic,
            max_tokens=2048,
            system=system,
            messages=payload,
        )
        parts = [block.text for block in response.content if block.type == "text"]
        if not parts:
            raise RuntimeError("Anthropic API returned empty response")
        return "".join(parts)
