"""Tests for configuration loading."""

import os
from pathlib import Path

import pytest

from app.config import (
    PROJECT_ROOT,
    Settings,
    has_provider_credentials,
    load_settings,
    resolve_role_provider,
)


def test_project_root_points_to_interview():
    assert PROJECT_ROOT.name == "interview"


def test_load_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SESSION_MAX_MINUTES", raising=False)
    monkeypatch.delenv("ROLE_JUDGE", raising=False)
    monkeypatch.delenv("SSL_CERTFILE", raising=False)
    monkeypatch.delenv("SSL_KEYFILE", raising=False)
    monkeypatch.delenv("MODEL_GEMINI_LIVE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ROLE_JUDGE=openai\nSESSION_MAX_MINUTES=45\n",
        encoding="utf-8",
    )
    settings = load_settings(env_file)
    assert settings.role_judge == "openai"
    assert settings.session_max_minutes == 45
    assert settings.db_path == PROJECT_ROOT / "data" / "sessions.db"


def test_resolve_role_provider():
    settings = Settings(
        gemini_api_key=None,
        openai_api_key="key",
        anthropic_api_key=None,
        xai_api_key=None,
        role_partner="gemini",
        role_judge="openai",
        role_persona="anthropic",
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
    assert resolve_role_provider(settings, "judge") == "openai"


def test_has_provider_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    settings = load_settings(Path("/nonexistent/.env"))
    assert has_provider_credentials(settings, "gemini") is True
    assert has_provider_credentials(settings, "openai") is False
