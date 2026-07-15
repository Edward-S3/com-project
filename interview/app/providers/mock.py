"""Mock LLM provider for tests and offline CLI demos."""

from __future__ import annotations

from app.providers.base import LLMProvider, Message


class MockProvider(LLMProvider):
    name = "mock"

    def generate(self, system: str, messages: list[Message], json_mode: bool = False) -> str:
        del system, json_mode
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if "目標" in last_user or "申告" in last_user:
            return (
                "なるほど、おっしゃる通りですね。"
                "来期は検査ミスを月3件以下に抑える、という目標で進めましょうか。"
            )
        return "承知しました。もう少し具体的に教えてください。"
