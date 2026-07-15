"""Provider factory and role-based resolution."""

from __future__ import annotations

from app.config import Settings, has_provider_credentials, resolve_role_provider
from app.providers.anthropic import AnthropicProvider
from app.providers.base import LLMProvider
from app.providers.gemini import GeminiProvider
from app.providers.mock import MockProvider
from app.providers.openai import OpenAIProvider
from app.providers.xai import XAIProvider

_PROVIDER_CLASSES = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "xai": XAIProvider,
    "mock": MockProvider,
}


def get_provider(settings: Settings, provider_name: str, *, allow_mock: bool = False) -> LLMProvider:
    """Instantiate a provider by name."""
    name = provider_name.lower()
    if name not in _PROVIDER_CLASSES:
        raise ValueError(f"Unsupported provider: {provider_name}")

    if name != "mock" and not has_provider_credentials(settings, name):
        if allow_mock:
            return MockProvider()
        raise ValueError(f"Credentials are not configured for provider: {name}")

    provider_cls = _PROVIDER_CLASSES[name]
    if name == "mock":
        return provider_cls()
    return provider_cls(settings)


def get_provider_for_role(
    settings: Settings,
    role: str,
    *,
    allow_mock: bool = False,
) -> LLMProvider:
    """Resolve provider for partner/judge/persona role."""
    provider_name = resolve_role_provider(settings, role)
    if allow_mock and not has_provider_credentials(settings, provider_name):
        return MockProvider()
    return get_provider(settings, provider_name, allow_mock=allow_mock)
