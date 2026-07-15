"""Gemini text API adapter."""

from __future__ import annotations

from google import genai
from google.genai import types

from app.config import Settings
from app.providers.base import LLMProvider, Message


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not configured")
        self._settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def generate(self, system: str, messages: list[Message], json_mode: bool = False) -> str:
        contents: list[types.Content] = []
        for message in messages:
            if message.role == "system":
                continue
            role = "user" if message.role == "user" else "model"
            contents.append(
                types.Content(role=role, parts=[types.Part(text=message.content)])
            )

        config_kwargs: dict = {"system_instruction": system}
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"

        response = self._client.models.generate_content(
            model=self._settings.model_gemini_text,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        text = response.text
        if not text:
            raise RuntimeError("Gemini API returned empty response")
        return text
