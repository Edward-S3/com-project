"""Common LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "system"]
    content: str


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, system: str, messages: list[Message], json_mode: bool = False) -> str:
        """Generate a model response from system prompt and chat messages."""
