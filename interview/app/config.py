"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ENV_PATH = PROJECT_ROOT / ".env"

VALID_PROVIDERS = frozenset({"gemini", "openai", "anthropic", "xai", "mock"})
VALID_ROLES = frozenset({"partner", "judge", "persona"})


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    openai_api_key: str | None
    anthropic_api_key: str | None
    xai_api_key: str | None
    role_partner: str
    role_judge: str
    role_persona: str
    model_gemini_live: str | None
    model_gemini_text: str
    model_openai: str
    model_anthropic: str
    model_xai: str
    voice_subordinate: str
    voice_supervisor: str
    admin_password: str | None
    session_max_minutes: int
    db_path: Path
    ssl_certfile: Path | None
    ssl_keyfile: Path | None


def _parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    return int(value)


def _normalize_provider(value: str | None, default: str = "gemini") -> str:
    provider = (value or default).strip().lower()
    if provider not in VALID_PROVIDERS - {"mock"}:
        raise ValueError(f"Unsupported provider: {provider}")
    return provider


def load_settings(env_path: Path | None = None) -> Settings:
    """Load settings from .env and environment variables."""
    path = env_path or ENV_PATH
    if path.exists():
        load_dotenv(path)

    def _optional_path(raw: str | None) -> Path | None:
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        xai_api_key=os.getenv("XAI_API_KEY") or None,
        role_partner=_normalize_provider(os.getenv("ROLE_PARTNER"), "gemini"),
        role_judge=_normalize_provider(os.getenv("ROLE_JUDGE"), "gemini"),
        role_persona=_normalize_provider(os.getenv("ROLE_PERSONA"), "gemini"),
        model_gemini_live=os.getenv("MODEL_GEMINI_LIVE") or None,
        model_gemini_text=os.getenv("MODEL_GEMINI_TEXT", "gemini-2.5-flash"),
        model_openai=os.getenv("MODEL_OPENAI", "gpt-4o-mini"),
        model_anthropic=os.getenv("MODEL_ANTHROPIC", "claude-3-5-haiku-latest"),
        model_xai=os.getenv("MODEL_XAI", "grok-2-latest"),
        voice_subordinate=os.getenv("VOICE_SUBORDINATE", "Fenrir"),
        voice_supervisor=os.getenv("VOICE_SUPERVISOR", "Kore"),
        admin_password=os.getenv("ADMIN_PASSWORD") or None,
        session_max_minutes=_parse_int(os.getenv("SESSION_MAX_MINUTES"), 30),
        db_path=DATA_DIR / "sessions.db",
        ssl_certfile=_optional_path(os.getenv("SSL_CERTFILE")),
        ssl_keyfile=_optional_path(os.getenv("SSL_KEYFILE")),
    )


def resolve_role_provider(settings: Settings, role: str) -> str:
    """Return provider name for a logical role."""
    if role not in VALID_ROLES:
        raise ValueError(f"Unknown role: {role}")
    mapping = {
        "partner": settings.role_partner,
        "judge": settings.role_judge,
        "persona": settings.role_persona,
    }
    return mapping[role]


def has_provider_credentials(settings: Settings, provider: str) -> bool:
    """Return True when API credentials exist for the provider."""
    if provider == "mock":
        return True
    if provider == "gemini":
        return bool(settings.gemini_api_key)
    if provider == "openai":
        return bool(settings.openai_api_key)
    if provider == "anthropic":
        return bool(settings.anthropic_api_key)
    if provider == "xai":
        return bool(settings.xai_api_key)
    return False
