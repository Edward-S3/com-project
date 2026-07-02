"""定数・パス"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
FALLBACK_ENV_PATH = Path("/opt/gemini-ui/.env")
PROMPTS_DIR = ROOT_DIR / "prompts"

_API_KEY_VARS = ("GOOGLE_API_KEYS", "GOOGLE_API_KEY", "GEMINI_API_KEYS", "GEMINI_API_KEY")


def _has_api_key() -> bool:
    return any(os.getenv(name) for name in _API_KEY_VARS)


def load_env() -> None:
    """Load /opt/pptx-gen/.env first; fall back to shared gemini-ui only if no API key."""
    load_dotenv(ENV_PATH)
    if not _has_api_key() and FALLBACK_ENV_PATH.is_file():
        load_dotenv(FALLBACK_ENV_PATH, override=False)


load_env()

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(ROOT_DIR / "output")))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FLASH_MODEL = os.getenv("PPTX_FLASH_MODEL", "gemini-2.0-flash")
STRUCTURED_MODEL = os.getenv("PPTX_STRUCTURED_MODEL", "gemini-2.5-pro")

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx", ".pptx", ".xlsx"}
MAX_SOURCE_CHARS = int(os.getenv("PPTX_MAX_SOURCE_CHARS", "120000"))

MIN_SLIDE_COUNT = 12
DEFAULT_SLIDE_COUNT = 12
MAX_SLIDE_COUNT = 20

SLIDE_MARGIN_IN = 0.5
FONT_TITLE = "Meiryo"
FONT_BODY = "Meiryo"

GRID_LAYOUTS = ("GRID_1X3", "GRID_2X2")
FULL_LAYOUTS = ("FULL_STEP_FLOW", "MATURITY_BAR_FULL")
