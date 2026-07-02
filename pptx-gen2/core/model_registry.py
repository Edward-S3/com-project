"""利用可能モデル一覧・工程別モデル解決。"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, TYPE_CHECKING

from config.design_system import MODEL_NAMES

if TYPE_CHECKING:
    from core.llm_clients import LLMClientManager

logger = logging.getLogger(__name__)

# テキスト生成向け Gemini（TTS/embedding/live 等を除外）
_GEMINI_SKIP = re.compile(
    r"(tts|embedding|image|live|robotics|computer-use|native-audio|omni|translate|bidi)",
    re.I,
)

# プロバイダ別デフォルト候補（API 未取得時のフォールバック）
STATIC_MODELS: Dict[str, List[str]] = {
    "gemini": [
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ],
    "claude": [
        MODEL_NAMES["claude"],
        "claude-sonnet-4-20250514",
        "claude-3-5-sonnet-20241022",
    ],
    "gpt4o": [
        MODEL_NAMES["gpt4o"],
        "gpt-4o-mini",
        "gpt-4-turbo",
    ],
    "grok": [
        MODEL_NAMES["grok"],
        "grok-2-latest",
    ],
}


def default_model(provider: str) -> str:
    key = {
        "gemini": "gemini",
        "claude": "claude",
        "gpt4o": "gpt4o",
        "grok": "grok",
    }.get(provider, provider)
    return MODEL_NAMES.get(key, MODEL_NAMES["gemini"])


def _normalize_gemini_name(name: str) -> str:
    return name.removeprefix("models/")


def list_gemini_models(llm: "LLMClientManager", *, refresh: bool = False) -> List[str]:
    """Gemini API から generateContent 対応モデルを取得。"""
    cache = getattr(llm, "_gemini_model_cache", None)
    if cache and not refresh:
        return cache

    models: List[str] = []
    try:
        client = llm._get_gemini()
        for item in client.models.list():
            raw = getattr(item, "name", str(item))
            short = _normalize_gemini_name(raw)
            if not short.startswith("gemini"):
                continue
            actions = getattr(item, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            if _GEMINI_SKIP.search(short):
                continue
            models.append(short)
    except Exception as exc:
        logger.warning("Gemini モデル一覧取得失敗: %s", exc)
        models = list(STATIC_MODELS["gemini"])

    if not models:
        models = list(STATIC_MODELS["gemini"])

    # 新しめ・Flash/Pro を優先してソート
    def _sort_key(m: str) -> tuple:
        priority = 0
        if "3.5" in m or "3.1" in m:
            priority -= 10
        if "flash" in m.lower() and "lite" not in m.lower():
            priority -= 5
        if "pro" in m.lower():
            priority -= 3
        if "preview" in m.lower():
            priority += 1
        return (priority, m)

    models = sorted(set(models), key=_sort_key)
    llm._gemini_model_cache = models
    return models


def list_models_for_provider(
    llm: "LLMClientManager",
    provider: str,
    *,
    refresh: bool = False,
) -> List[str]:
    if provider == "gemini" and llm.available.get("gemini"):
        return list_gemini_models(llm, refresh=refresh)
    return list(dict.fromkeys(STATIC_MODELS.get(provider, [default_model(provider)])))


def resolve_model(provider: str, override: Optional[str] = None) -> str:
    if override and override not in ("auto", ""):
        return override
    return default_model(provider)


def models_for_task_ui(
    llm: "LLMClientManager",
    provider: str,
    *,
    refresh: bool = False,
) -> List[str]:
    """UI 用: auto 以外のプロバイダ向けモデル一覧。"""
    if provider in ("auto", ""):
        return []
    if not llm.available.get(provider):
        return []
    return list_models_for_provider(llm, provider, refresh=refresh)
