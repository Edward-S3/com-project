"""Tests for LLM provider adapters."""

import pytest

from app.config import Settings, PROJECT_ROOT
from app.providers.base import Message
from app.providers.mock import MockProvider
from app.providers.registry import get_provider, get_provider_for_role


def _settings(**overrides) -> Settings:
    base = Settings(
        gemini_api_key=None,
        openai_api_key=None,
        anthropic_api_key=None,
        xai_api_key=None,
        role_partner="gemini",
        role_judge="gemini",
        role_persona="gemini",
        model_gemini_live=None,
        model_gemini_text="gemini-2.5-flash",
        model_openai="gpt-4o-mini",
        model_anthropic="claude-3-5-haiku-latest",
        model_xai="grok-2-latest",
        voice_subordinate="Fenrir",
        voice_supervisor="Kore",
        admin_password=None,
        session_max_minutes=30,
        db_path=PROJECT_ROOT / "data" / "sessions.db",
        ssl_certfile=None,
        ssl_keyfile=None,
    )
    return Settings(**{**base.__dict__, **overrides})


def test_mock_provider_generate():
    provider = MockProvider()
    response = provider.generate(
        "system",
        [Message(role="user", content="来期の目標を申告します")],
    )
    assert "目標" in response or "具体的" in response


def test_get_provider_mock_explicit():
    provider = get_provider(_settings(), "mock")
    assert provider.name == "mock"


def test_get_provider_missing_credentials_raises():
    with pytest.raises(ValueError, match="Credentials"):
        get_provider(_settings(), "gemini")


def test_get_provider_for_role_uses_mock_when_allowed():
    provider = get_provider_for_role(_settings(), "partner", allow_mock=True)
    assert provider.name == "mock"
