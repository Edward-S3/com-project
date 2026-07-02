"""定数・パス・環境変数"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
NAI_ENV_PATH = Path("/opt/gemini-ui/.env")
LOCAL_ENV_PATH = ROOT_DIR / ".env"
PROMPTS_DIR = ROOT_DIR / "prompts"
OUTPUT_DIR = ROOT_DIR / "output"
TEMP_DIR = ROOT_DIR / "temp"

load_dotenv(NAI_ENV_PATH)
load_dotenv(LOCAL_ENV_PATH, override=True)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

VISION_MODEL = os.getenv("IMG2PPTX_MODEL", "gemini-2.5-flash")
# Gemini HTTP タイムアウト（ミリ秒）。応答がない API 呼び出しでハングするのを防ぐ
GEMINI_REQUEST_TIMEOUT_MS = int(os.getenv("IMG2PPTX_GEMINI_TIMEOUT_MS", "120000"))
DEFAULT_DPI = 96
DEFAULT_FONT = "Yu Gothic"

# 16:9 デフォルト（幅 33.87cm × 高さ 19.05cm）
DEFAULT_SLIDE_WIDTH_EMU = 12192000
DEFAULT_SLIDE_HEIGHT_EMU = 6858000
