"""Unit tests for Live bridge helpers (no real Live API calls)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import PROJECT_ROOT, Settings, load_settings
from app.live_bridge import (
    UsageAccumulator,
    build_live_connect_config,
    resolve_live_voice,
)
from app.session_service import SessionService


def _settings(**kwargs: object) -> Settings:
    base = dict(
        gemini_api_key="test-key",
        openai_api_key=None,
        anthropic_api_key=None,
        xai_api_key=None,
        role_partner="gemini",
        role_judge="gemini",
        role_persona="gemini",
        model_gemini_live="gemini-3.1-flash-live-preview",
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
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_resolve_live_voice_by_persona_role():
    settings = _settings()
    assert resolve_live_voice(settings, "subordinate") == "Fenrir"
    assert resolve_live_voice(settings, "supervisor") == "Kore"


def test_build_live_connect_config_uses_env_voices_not_hardcoded_model():
    settings = _settings()
    cfg = build_live_connect_config(
        settings,
        system_instruction="test prompt",
        voice_name=settings.voice_subordinate,
    )
    assert cfg.response_modalities is not None
    assert cfg.input_audio_transcription is not None
    assert cfg.output_audio_transcription is not None
    assert cfg.session_resumption is not None
    assert cfg.speech_config is not None
    voice = cfg.speech_config.voice_config.prebuilt_voice_config.voice_name
    assert voice == "Fenrir"


def test_usage_accumulator():
    acc = UsageAccumulator()

    class Meta:
        prompt_token_count = 10
        response_token_count = 5
        total_token_count = 15

    snap = acc.add(Meta())
    assert snap["session_total_tokens"] == 15
    acc.add(Meta())
    assert acc.as_dict()["total_tokens"] == 30


def test_add_voice_turn_marks_interrupted(tmp_path: Path):
    db_path = tmp_path / "t.db"
    settings = _settings(db_path=db_path)
    svc = SessionService(settings)
    uid = svc.create_user(
        {
            "name": "音声テスト",
            "department": "製造",
            "age": 35,
            "tenure_years": 10,
            "grade": "4-1",
        }
    )
    sid = svc.db.create_session(
        user_id=uid,
        scene="initial",
        role="supervisor",
        difficulty="sme",
        io_mode="voice",
        persona={"role": "subordinate", "display_name": "部下"},
        models={"partner": "mock"},
    )
    tid = svc.add_voice_turn(
        sid,
        "partner",
        "途中まで話した内容です",
        interrupted=True,
    )
    assert tid > 0
    turns = svc.db.list_turns(sid)
    partner = [t for t in turns if t["speaker"] == "partner" and t["audio_flag"]]
    assert partner
    assert partner[-1]["warnings_json"]
    assert "interrupted" in partner[-1]["warnings_json"]


def test_model_gemini_live_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "MODEL_GEMINI_LIVE=gemini-3.1-flash-live-preview\nVOICE_SUBORDINATE=Puck\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MODEL_GEMINI_LIVE", raising=False)
    monkeypatch.delenv("VOICE_SUBORDINATE", raising=False)
    settings = load_settings(env)
    assert settings.model_gemini_live == "gemini-3.1-flash-live-preview"
    assert settings.voice_subordinate == "Puck"


def test_live_smoke_script_imports():
    """Ensure smoke script is importable (syntax / path)."""
    import importlib.util

    path = PROJECT_ROOT / "scripts" / "live_smoke.py"
    spec = importlib.util.spec_from_file_location("live_smoke", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "run_smoke")
