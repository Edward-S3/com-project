"""
llm_providers.py — マルチプロバイダ LLM 抽象化
Gemini / OpenAI / Anthropic / Ollama（ローカル）+ レート制限リトライ + 自動モデル選択
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Iterator

import requests
from dotenv import load_dotenv, dotenv_values

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_ENV_KEY_VARS = (
    "GOOGLE_API_KEY", "GOOGLE_API_KEYS",
    "OPENAI_API_KEY", "OPENAI_API_KEYS",
    "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEYS",
)

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_types = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import anthropic
except ImportError:
    anthropic = None

AUTO_MODEL_ID = "__auto__"
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
GEMINI_AUDIO_INLINE_MAX_BYTES = 20 * 1024 * 1024

AUDIO_EXTENSIONS = frozenset({"mp3", "wav", "aac", "flac", "m4a", "ogg", "webm"})
AUDIO_MIME_MAP = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
}

RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_WAIT_SEC = 15
RATE_LIMIT_MAX_WAIT_SEC = 60


@dataclass
class FileAttachment:
    """プロバイダ非依存の添付ファイル（data または path のいずれか）"""
    name: str
    mime: str
    data: bytes | None = None
    path: str | None = None

    def read_bytes(self) -> bytes:
        if self.data is not None:
            return self.data
        if self.path and os.path.isfile(self.path):
            with open(self.path, "rb") as f:
                return f.read()
        raise ValueError(f"添付ファイルにデータがありません: {self.name}")

    def byte_size(self) -> int:
        if self.data is not None:
            return len(self.data)
        if self.path and os.path.isfile(self.path):
            return os.path.getsize(self.path)
        return 0


def _is_image_attachment(att: FileAttachment) -> bool:
    if att.mime.startswith("image/"):
        return True
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    return ext in ("jpg", "jpeg", "png", "gif", "webp")


def _is_pdf_attachment(att: FileAttachment) -> bool:
    if att.mime == "application/pdf":
        return True
    return att.name.lower().endswith(".pdf")


def _is_text_attachment(att: FileAttachment) -> bool:
    if att.mime.startswith("text/"):
        return True
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    return ext in ("txt", "csv", "md")


def _is_office_attachment(att: FileAttachment) -> bool:
    import office_files as office

    return office.is_office_file(att.name, att.mime)


def _is_audio_attachment(att: FileAttachment) -> bool:
    if att.mime.startswith("audio/"):
        return True
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    return ext in AUDIO_EXTENSIONS


def _audio_mime_for_api(att: FileAttachment) -> str:
    if att.mime.startswith("audio/"):
        return att.mime
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    return AUDIO_MIME_MAP.get(ext, "audio/mpeg")


def is_image_generation_model(model: str) -> bool:
    return model == GEMINI_IMAGE_MODEL


def template_requires_audio(tmpl: dict | None) -> bool:
    """音声文字起こし・議事録テンプレートでは音声添付が必須"""
    import template_registry as tr
    return tr.get_template_kind(tmpl) == tr.KIND_AUDIO


def template_is_excel_extra(tmpl: dict | None) -> bool:
    """Excel 集計・分析テンプレート（Python による xlsx 分析）"""
    import template_registry as tr
    return tr.get_template_kind(tmpl) == tr.KIND_EXCEL_EXTRA


def template_is_office_output(tmpl: dict | None) -> bool:
    """Office ファイル（docx/pptx）出力テンプレート"""
    import template_registry as tr
    return tr.get_template_kind(tmpl) == tr.KIND_OFFICE_OUTPUT


def model_is_gemini(model: str) -> bool:
    info = get_model_info(model)
    return bool(info and info.get("provider") == "google")


def template_compatible_models(
    tmpl: dict | None,
    user_models: dict[str, str],
) -> dict[str, str]:
    """テンプレートで実際に利用可能なモデル（表示・実行の整合用）"""
    if not tmpl:
        return dict(user_models)
    if template_is_excel_extra(tmpl) or template_requires_audio(tmpl):
        return {k: v for k, v in user_models.items() if model_is_gemini(k)}
    if template_is_image_generation(tmpl):
        return {
            k: v for k, v in user_models.items()
            if is_image_generation_model(k)
        }
    return dict(user_models)


def template_model_requirement_note(tmpl: dict | None) -> str | None:
    """テンプレート固有のモデル制約の説明"""
    if not tmpl:
        return None
    if template_is_excel_extra(tmpl):
        return "※ Excel 集計・分析は Gemini モデルのみ（集計コード生成・分析の両方）"
    if template_requires_audio(tmpl):
        return "※ 音声テンプレートは Gemini モデルのみ"
    if template_is_image_generation(tmpl):
        return "※ 画像生成は専用モデルのみ"
    if template_is_office_output(tmpl):
        import template_registry as tr
        fmt = tr.office_output_format(tmpl)
        if fmt:
            return f"※ 回答から {fmt.upper()} ファイルを自動生成します"
    return None


def attachment_is_audio(att: FileAttachment | None) -> bool:
    return bool(att and _is_audio_attachment(att))


def attachment_is_image(att: FileAttachment | None) -> bool:
    return bool(att and _is_image_attachment(att))


def attachments_have_audio(atts: list[FileAttachment] | None) -> bool:
    return any(_is_audio_attachment(a) for a in (atts or []))


def template_is_image_generation(tmpl: dict | None) -> bool:
    """画像生成テンプレート（参照画像1件まで可）"""
    import template_registry as tr
    return tr.get_template_kind(tmpl) == tr.KIND_IMAGE


IMAGE_REFERENCE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "gif", "webp"})


def validate_image_generation_attachments(atts: list[FileAttachment] | None) -> FileAttachment | None:
    """画像生成用: 参照画像は0〜1件・画像形式のみ"""
    items = atts or []
    if not items:
        return None
    if len(items) > 1:
        raise ValueError("画像生成では参照画像は1件のみ指定できます。")
    ref = items[0]
    if not _is_image_attachment(ref):
        raise ValueError("画像生成の参照には画像ファイル（JPG/PNG/GIF/WebP）を指定してください。")
    return ref


def _decode_text_bytes(data: bytes) -> str:
    for enc in ("utf-8", "cp932", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        parts = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(p for p in parts if p.strip())
        return text.strip() or "[PDF からテキストを抽出できませんでした]"
    except Exception:
        return "[PDF のテキスト抽出に失敗しました]"


def _gemini_inline_attachment(att: FileAttachment) -> bool:
    """Gemini へバイナリで渡す添付（画像・音声のみ）"""
    return _is_image_attachment(att) or _is_audio_attachment(att)


def _attachment_text_suffix(att: FileAttachment) -> str:
    if _is_pdf_attachment(att):
        body = _extract_pdf_text(att.read_bytes())
        return f"\n\n--- 添付 PDF ({att.name}) ---\n{body}"
    if _is_office_attachment(att):
        import office_files as office

        return office.attachment_context_suffix(att.name, att.mime, att.read_bytes())
    if _is_text_attachment(att):
        body = _decode_text_bytes(att.read_bytes())
        return f"\n\n--- 添付ファイル ({att.name}) ---\n{body}"
    return f"\n\n[添付: {att.name} ({att.mime}) — 内容はテキストとして送信できません]"


def _image_mime_for_api(att: FileAttachment) -> str:
    if att.mime.startswith("image/"):
        return att.mime
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    mapping = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "gif": "image/gif", "webp": "image/webp",
    }
    return mapping.get(ext, "image/jpeg")


def build_gemini_file_part(att: FileAttachment) -> Any:
    if not genai_types:
        raise ValueError("google-genai パッケージがインストールされていません。")
    raw = att.read_bytes()
    if _is_audio_attachment(att):
        return genai_types.Part.from_bytes(
            data=raw, mime_type=_audio_mime_for_api(att),
        )
    return genai_types.Part(
        inline_data=genai_types.Blob(data=raw, mime_type=att.mime or "application/octet-stream")
    )


def _prepare_gemini_attachment(client: Any, att: FileAttachment) -> tuple[Any, str | None]:
    """Gemini 用添付パーツを準備。大きな音声は File API 経由。"""
    if _is_audio_attachment(att) and att.byte_size() > GEMINI_AUDIO_INLINE_MAX_BYTES:
        mime = _audio_mime_for_api(att)
        if att.path and os.path.isfile(att.path):
            uploaded = client.files.upload(
                file=att.path,
                config=genai_types.UploadFileConfig(mime_type=mime, display_name=att.name),
            )
            return uploaded, uploaded.name
        suffix = "." + att.name.rsplit(".", 1)[-1] if "." in att.name else ".audio"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(att.read_bytes())
            tmp_path = tmp.name
        try:
            uploaded = client.files.upload(
                file=tmp_path,
                config=genai_types.UploadFileConfig(mime_type=mime, display_name=att.name),
            )
            return uploaded, uploaded.name
        finally:
            os.unlink(tmp_path)
    return build_gemini_file_part(att), None


def _cleanup_gemini_upload(client: Any, file_name: str | None) -> None:
    if not file_name:
        return
    try:
        client.files.delete(name=file_name)
    except Exception:
        pass


def build_openai_user_content(
    atts: list[FileAttachment] | None, user_text: str,
) -> str | list[dict]:
    if not atts:
        return user_text
    content: list[dict] = [{"type": "text", "text": user_text}]
    text_suffix = ""
    for att in atts:
        if _is_image_attachment(att):
            mime = _image_mime_for_api(att)
            b64 = base64.standard_b64encode(att.read_bytes()).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        else:
            text_suffix += _attachment_text_suffix(att)
    if text_suffix:
        content[0]["text"] = user_text + text_suffix
    if len(content) == 1:
        return content[0]["text"]
    return content


def build_anthropic_user_content(
    atts: list[FileAttachment] | None, user_text: str,
) -> str | list[dict]:
    if not atts:
        return user_text
    blocks: list[dict] = []
    text = user_text
    for att in atts:
        if _is_image_attachment(att):
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _image_mime_for_api(att),
                    "data": base64.standard_b64encode(att.read_bytes()).decode(),
                },
            })
        elif _is_pdf_attachment(att):
            blocks.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(att.read_bytes()).decode(),
                },
            })
        else:
            text += _attachment_text_suffix(att)
    blocks.append({"type": "text", "text": text})
    return blocks


def build_ollama_user_message(
    atts: list[FileAttachment] | None, user_text: str,
) -> tuple[str, list[str]]:
    """Ollama 用 (text, base64_images[])"""
    images: list[str] = []
    text = user_text
    for att in atts or []:
        if _is_image_attachment(att):
            images.append(base64.standard_b64encode(att.read_bytes()).decode())
        else:
            text += _attachment_text_suffix(att)
    return text, images

# ── モデル定義（クラウド API） ────────────────────────────
# api_style: openai 系のみ — legacy=max_tokens+temp / reasoning=max_completion_tokens（temp 不可）
CLOUD_MODELS: dict[str, dict[str, str]] = {
    # Google Gemini
    "gemini-3.5-flash":       {"provider": "google",    "label": "Gemini 3.5 Flash ⚡ (最新・推奨)"},
    "gemini-3.1-pro-preview": {"provider": "google",    "label": "Gemini 3.1 Pro 🧠 (最高精度)"},
    "gemini-3.1-flash-lite":  {"provider": "google",    "label": "Gemini 3.1 Flash Lite 🪶 (軽量・低コスト)"},
    "gemini-2.5-pro":         {"provider": "google",    "label": "Gemini 2.5 Pro 🧠"},
    "gemini-2.5-flash":       {"provider": "google",    "label": "Gemini 2.5 Flash ⚡"},
    GEMINI_IMAGE_MODEL:       {"provider": "google",    "label": "Gemini 2.5 Flash Image 🎨 (画像生成)"},
    # OpenAI（2026-06 時点で API 確認済み）
    "gpt-5.5":                {"provider": "openai", "label": "GPT-5.5 🧠 (最新・推奨)", "api_style": "reasoning"},
    "gpt-5.4":                {"provider": "openai", "label": "GPT-5.4 🧠",             "api_style": "reasoning"},
    "gpt-5.4-mini":           {"provider": "openai", "label": "GPT-5.4 Mini ⚡",         "api_style": "reasoning"},
    "gpt-4.1":                {"provider": "openai", "label": "GPT-4.1 🧠",             "api_style": "legacy"},
    "gpt-4.1-mini":           {"provider": "openai", "label": "GPT-4.1 Mini ⚡",        "api_style": "legacy"},
    "gpt-4o":                 {"provider": "openai", "label": "GPT-4o",                 "api_style": "legacy"},
    "gpt-4o-mini":            {"provider": "openai", "label": "GPT-4o Mini ⚡",          "api_style": "legacy"},
    "o4-mini":                {"provider": "openai", "label": "o4-mini 🧠 (推論)",       "api_style": "reasoning"},
    "o3-mini":                {"provider": "openai", "label": "o3-mini 🧠 (推論)",       "api_style": "reasoning"},
    # Anthropic（2026-06 時点で API 確認済み）
    "claude-sonnet-4-6":          {"provider": "anthropic", "label": "Claude Sonnet 4.6 🧠 (最新・推奨)"},
    "claude-opus-4-6":            {"provider": "anthropic", "label": "Claude Opus 4.6 🧠 (最高精度)"},
    "claude-haiku-4-5-20251001":  {"provider": "anthropic", "label": "Claude Haiku 4.5 ⚡"},
}

ALL_MODELS: dict[str, dict[str, str]] = dict(CLOUD_MODELS)

AUTO_MODEL_LABEL = "🎯 自動選択（プロンプトに最適なモデル）"

_ollama_cache: dict[str, float | list[str]] = {"ts": 0.0, "models": []}
_OLLAMA_CACHE_TTL = 60.0

# Gemma 4（Google DeepMind, 2026-04-02 リリース）— Ollama タグ
# E2B: 自動選択ルーター専用（高速） / E4B・26B: 通常チャット向け
GEMMA4_TAGS: dict[str, dict[str, str]] = {
    "e2b": {"label": "Gemma 4 E2B ⚡ (Edge・軽量)",       "role": "router"},
    "2b":  {"label": "Gemma 4 E2B ⚡ (Edge・軽量)",       "role": "router"},
    "e4b": {"label": "Gemma 4 E4B 🏠 (汎用・バランス)",    "role": "chat"},
    "4b":  {"label": "Gemma 4 E4B 🏠 (汎用・バランス)",    "role": "chat"},
    "12b": {"label": "Gemma 4 12B 🏠 (ワークステーション・バランス)", "role": "chat"},
    "26b": {"label": "Gemma 4 26B MoE 🧠 (高精度)",        "role": "chat"},
    "31b": {"label": "Gemma 4 31B Dense 🧠 (最高品質)",   "role": "chat"},
}

# 自動選択ルーターの優先順（E2B を最優先）
ROUTER_MODEL_CANDIDATES = (
    "gemma4:e2b", "gemma4:2b",
    "llama3.2:latest",
)


# ── API キー管理 ────────────────────────────────────────

def reload_provider_env() -> None:
    """.env を再読み込みし、削除された API キーを環境変数からも除去"""
    file_vals = dotenv_values(_ENV_PATH)
    load_dotenv(dotenv_path=_ENV_PATH, override=True)
    for var in _ENV_KEY_VARS:
        v = file_vals.get(var)
        if v is None or not str(v).strip():
            os.environ.pop(var, None)
        else:
            os.environ[var] = str(v).strip()


def _parse_keys(single_var: str, multi_var: str) -> list[str]:
    reload_provider_env()
    raw = os.getenv(multi_var) or os.getenv(single_var, "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def get_provider_keys(provider: str) -> list[str]:
    mapping = {
        "google":    ("GOOGLE_API_KEY",    "GOOGLE_API_KEYS"),
        "openai":    ("OPENAI_API_KEY",    "OPENAI_API_KEYS"),
        "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEYS"),
    }
    if provider not in mapping:
        return []
    return _parse_keys(*mapping[provider])


def _ollama_enabled() -> bool:
    """OLLAMA_ENABLED=0 で無効化。未設定時は有効（ただしモデルはインストール済みのみ表示）"""
    return os.getenv("OLLAMA_ENABLED", "1").strip().lower() not in ("0", "false", "no")


def _ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def _fetch_ollama_installed() -> list[str]:
    now = time.time()
    if now - float(_ollama_cache["ts"]) < _OLLAMA_CACHE_TTL:
        return list(_ollama_cache["models"])  # type: ignore[arg-type]
    models: list[str] = []
    try:
        resp = requests.get(f"{_ollama_base_url()}/api/tags", timeout=3)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", []) if m.get("name")]
    except Exception:
        models = []
    _ollama_cache["ts"] = now
    _ollama_cache["models"] = models
    return models


def _match_installed_ollama(name: str, installed: list[str]) -> str | None:
    """Ollama タグ名をインストール済みリストと照合"""
    if name in installed:
        return name
    base, _, tag = name.partition(":")
    for m in installed:
        if m == name:
            return m
        mb, _, mt = m.partition(":")
        if mb == base and (not tag or tag in mt or mt.startswith(tag)):
            return m
    return None


def _parse_gemma4_install(name: str) -> tuple[str, dict[str, str]] | None:
    """gemma4:* インストール名 → (model_key, info)"""
    low = name.lower()
    if not low.startswith("gemma4"):
        return None
    tag = name.split(":", 1)[1].lower() if ":" in name else "26b"
    if tag in ("latest", ""):
        tag = "26b"
    meta = GEMMA4_TAGS.get(tag)
    if not meta:
        # 長いタグを先に照合（"2b" が "12b" に誤マッチしないよう key in tag は使わない）
        for key, m in sorted(GEMMA4_TAGS.items(), key=lambda kv: len(kv[0]), reverse=True):
            if tag == key or tag.startswith(f"{key}-"):
                meta = m
                tag = key
                break
    if not meta:
        meta = {"label": f"Gemma 4 ({tag}) 🏠", "role": "chat"}
    model_key = f"local:gemma4-{tag}"
    return model_key, {
        "provider": "ollama",
        "label": meta["label"],
        "ollama_model": name,
        "gemma4_role": meta.get("role", "chat"),
    }


def _resolve_router_model() -> str | None:
    """自動選択ルーター用 Ollama モデル（デフォルト: Gemma 4 E2B）"""
    if not _ollama_enabled():
        return None
    installed = _fetch_ollama_installed()
    if not installed:
        return None

    explicit = (os.getenv("LOCAL_LLM_ROUTER_MODEL") or "").strip()
    if explicit:
        return _match_installed_ollama(explicit, installed)

    for cand in ROUTER_MODEL_CANDIDATES:
        hit = _match_installed_ollama(cand, installed)
        if hit:
            return hit

    for m in installed:
        ml = m.lower()
        if "gemma4" in ml and ("e2b" in ml or ":2b" in ml or ml.endswith(":2b")):
            return m
    return installed[0]


def get_ollama_model_entries() -> dict[str, dict[str, str]]:
    """Ollama にインストール済みのモデルのみ（Gemma 4 は公式タグ名で表示）"""
    if not _ollama_enabled():
        return {}
    entries: dict[str, dict[str, str]] = {}
    for name in _fetch_ollama_installed():
        parsed = _parse_gemma4_install(name)
        if parsed:
            key, info = parsed
            entries[key] = info
            continue
        short = name.split(":")[0]
        key = f"local:{short}"
        if key not in entries:
            entries[key] = {
                "provider": "ollama",
                "label": f"ローカル {short} 🏠",
                "ollama_model": name,
            }
    return entries


def get_model_registry() -> dict[str, dict[str, str]]:
    return {**CLOUD_MODELS, **get_ollama_model_entries()}


def get_model_info(model_id: str) -> dict[str, str] | None:
    return get_model_registry().get(model_id)


def provider_available(provider: str) -> bool:
    if provider == "ollama":
        return _ollama_enabled() and bool(_fetch_ollama_installed())
    return bool(get_provider_keys(provider))


def ollama_router_available() -> bool:
    """自動選択のルーター用 LLM（Gemma 4 E2B 等）が使えるか"""
    return _resolve_router_model() is not None


def auto_select_available() -> bool:
    """自動選択機能が利用可能か（ルーターまたはヒューリスティック）"""
    return True


def clear_provider_caches() -> None:
    """Ollama モデル一覧キャッシュをクリア（深夜バッチ等で使用）"""
    _ollama_cache["ts"] = 0.0
    _ollama_cache["models"] = []


def get_available_models() -> dict[str, str]:
    """API キー設定済み + Ollama インストール済みのモデルのみ（ルーター専用は除外）"""
    reload_provider_env()
    result: dict[str, str] = {}
    for mid, info in CLOUD_MODELS.items():
        if provider_available(info["provider"]):
            result[mid] = info["label"]
    for mid, info in get_ollama_model_entries().items():
        if _is_router_only_model(mid):
            continue
        result[mid] = info["label"]
    return result


class KeyManager:
    """プロバイダごとの API キーローテーション"""

    def __init__(self, provider: str):
        self.provider = provider
        self.keys = get_provider_keys(provider)
        self.exhausted: set[str] = set()

    def get_key(self) -> str | None:
        available = [k for k in self.keys if k not in self.exhausted]
        return random.choice(available) if available else None

    def other_available(self, key: str) -> bool:
        return any(k not in self.exhausted and k != key for k in self.keys)

    def mark_rate_limited(self, key: str) -> bool:
        if self.other_available(key):
            self.exhausted.add(key)
            return True
        return False

    def mark_permission_denied(self, key: str) -> None:
        self.exhausted.add(key)


def classify_api_error(msg: str) -> str:
    if "PERMISSION_DENIED" in msg or " 403 " in f" {msg} ":
        return "permission_denied"
    low = msg.lower()
    if (
        "RESOURCE_EXHAUSTED" in msg
        or "rate limit" in low
        or " 429 " in f" {msg} "
        or "quota" in low
        or "too many requests" in low
        or "overloaded" in low
    ):
        return "rate_limit"
    return "other"


# ── 自動モデル選択（ローカル LLM ルーター） ─────────────

def _ollama_chat(model: str, messages: list[dict], temperature: float = 0.1) -> str:
    resp = requests.post(
        f"{_ollama_base_url()}/api/chat",
        json={"model": model, "messages": messages, "stream": False,
              "options": {"temperature": temperature}},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "")


def _is_router_only_model(model_key: str) -> bool:
    info = get_model_info(model_key) or {}
    return info.get("gemma4_role") == "router"


def _auto_select_answer_candidates(candidates: dict[str, str]) -> dict[str, str]:
    """自動選択の回答候補（ルーター専用モデルを除外）"""
    answer = {k: v for k, v in candidates.items() if not _is_router_only_model(k)}
    return answer if answer else dict(candidates)


def _attachment_model_priority() -> list[str]:
    return [
        "gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-2.5-pro",
        "gpt-5.5", "gpt-5.4", "gpt-4o", "gpt-4.1",
        "claude-sonnet-4-6", "claude-opus-4-6",
        "local:gemma4-26b", "local:gemma4-31b", "local:gemma4-12b", "local:gemma4-e4b",
    ]


def _heuristic_auto_select(
    prompt: str, candidates: dict[str, str], has_attachment: bool = False,
) -> str:
    """Ollama 未使用時のフォールバック: キーワード + 優先順位"""
    if has_attachment:
        for m in _attachment_model_priority():
            if m in candidates:
                return m

    # ローカル Gemma 4 のみが候補の場合（クラウド API 未設定時など）
    locals_only = all(k.startswith("local:") for k in candidates)
    if locals_only:
        hard = any(h in prompt for h in ("詳細", "分析", "コード", "論文", "レポート"))
        if hard:
            for m in ("local:gemma4-26b", "local:gemma4-31b", "local:gemma4-12b", "local:gemma4-e4b"):
                if m in candidates:
                    return m
        for m in ("local:gemma4-e4b", "local:gemma4-12b", "local:gemma4-26b"):
            if m in candidates:
                return m
        return next(iter(candidates))

    priority = [
        "gemini-3.5-flash", "gpt-5.5", "claude-sonnet-4-6",
        "gpt-5.4-mini", "gpt-4.1-mini", "claude-haiku-4-5-20251001",
        "local:gemma4-e4b", "local:gemma4-12b", "local:gemma4-26b", "local:gemma4-31b",
        "gemini-3.1-flash-lite", "gpt-4o-mini",
    ]
    low = prompt.lower()
    code_hints = ("```", "def ", "class ", "function", "コード", "python", "javascript", "bug", "error")
    hard_hints = ("論文", "research", "詳細に", "分析", "レポート", "契約", "法務")
    local_hints = ("社内", "機密", "オフライン", "ローカル", "社外に出さ")

    if any(h in low or h in prompt for h in local_hints):
        for m in ("local:gemma4-26b", "local:gemma4-12b", "local:gemma4-e4b", "local:gemma4-31b"):
            if m in candidates:
                return m

    if any(h in low or h in prompt for h in hard_hints):
        for m in ("claude-opus-4-6", "gemini-3.1-pro-preview", "gpt-5.5", "gpt-5.4", "local:gemma4-26b", "local:gemma4-12b"):
            if m in candidates:
                return m
    if any(h in low or h in prompt for h in code_hints):
        for m in ("gpt-5.5", "claude-sonnet-4-6", "gemini-3.1-pro-preview", "gpt-5.4", "local:gemma4-26b", "local:gemma4-12b"):
            if m in candidates:
                return m
    for m in priority:
        if m in candidates:
            return m
    return next(iter(candidates))


def auto_select_model(
    prompt: str, candidates: dict[str, str], has_attachment: bool = False,
) -> str:
    """
    プロンプト内容から最適なモデル ID を返す。
    Ollama ルーター利用可能時はローカル LLM で判定、それ以外はヒューリスティック。
    ローカル Gemma 4（E4B/26B 等）も回答候補に含める（E2B はルーター専用で除外）。
    """
    answer = _auto_select_answer_candidates(candidates)
    if not answer:
        return "gemini-3.5-flash"
    if len(answer) == 1:
        return next(iter(answer))

    if has_attachment:
        for m in _attachment_model_priority():
            if m in answer:
                return m

    if ollama_router_available():
        router = _resolve_router_model()
        options_text = "\n".join(f"- {mid}: {label}" for mid, label in answer.items())
        system = (
            "あなたは LLM ルーターです。ユーザーの質問内容を分析し、"
            "最も適切なモデル ID を1つだけ返してください。"
            "返答はモデル ID のみ（説明不要）。"
            "コード・複雑な推論は高性能モデル、簡単な質問は高速モデルを選んでください。"
            "local: で始まるモデルは社内サーバー上のローカル LLM です。"
            "機密・社内データ・オフライン希望には local:gemma4-e4b / local:gemma4-12b / local:gemma4-26b を優先してください。"
            "添付ファイル（画像/PDF）がある質問はマルチモーダル対応のクラウドまたは local:gemma4-12b / local:gemma4-26b を選んでください。"
        )
        attach_note = "（添付ファイルあり）" if has_attachment else ""
        user_msg = (
            f"利用可能なモデル:\n{options_text}\n\n"
            f"ユーザーの質問{attach_note}:\n{prompt[:1500]}\n\n"
            "最適なモデル ID:"
        )
        try:
            answer_text = _ollama_chat(
                router,
                [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
                temperature=0.0,
            ).strip()
            for mid in answer:
                if mid in answer_text:
                    return mid
            for mid in answer:
                slug = mid.replace("local:", "")
                if slug in answer_text or mid.split("-")[-1] in answer_text:
                    return mid
        except Exception:
            pass

    return _heuristic_auto_select(prompt, answer, has_attachment=has_attachment)


def resolve_model(
    selected: str,
    prompt: str,
    user_models: dict[str, str],
    template_active: bool,
    has_attachment: bool = False,
) -> str:
    """選択モデルを実際の model_id に解決する"""
    if selected == AUTO_MODEL_ID and not template_active:
        return auto_select_model(prompt, user_models, has_attachment=has_attachment)
    return selected


def get_ollama_model_id(model_key: str) -> str:
    info = get_model_info(model_key) or {}
    return info.get("ollama_model") or os.getenv("LOCAL_LLM_MODEL", "gemma4:e2b")


def get_router_model_label() -> str:
    """UI 表示用: 自動選択ルーターで使用中のモデル"""
    m = _resolve_router_model()
    if not m:
        return ""
    ml = m.lower()
    if "gemma4" in ml and ("e2b" in ml or ":2b" in ml):
        return "Gemma 4 E2B"
    if "gemma4" in ml:
        return f"Gemma 4 ({m.split(':')[-1] if ':' in m else m})"
    return m.split(":")[0]


def _openai_api_style(model: str) -> str:
    info = get_model_info(model) or {}
    if info.get("api_style") == "reasoning":
        return "reasoning"
    if model.startswith(("gpt-5", "o1", "o3", "o4")):
        return "reasoning"
    return "legacy"


# ── ストリーミング ──────────────────────────────────────

_EMBEDDED_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(data:image/[^;]+;base64,[^)]+\)",
    re.IGNORECASE,
)
_OFFICE_OUTPUT_RE = re.compile(
    r"\n?<!--NAI_(?:OFFICE|EXCELEXTRA):[A-Za-z0-9+/=]+-->\s*",
)
_IMAGE_GEN_HISTORY_MAX = 12


def _sanitize_message_content(content: str) -> str:
    """API 送信用に巨大な埋め込み（生成画像 base64 等）を除去"""
    text = _EMBEDDED_IMAGE_RE.sub("[生成画像]", content or "")
    text = _OFFICE_OUTPUT_RE.sub("", text)
    return text.strip()


def _prepare_history_messages(
    messages: list[dict], *, image_generation: bool = False,
) -> list[dict]:
    cleaned: list[dict] = []
    for msg in messages:
        text = _sanitize_message_content(msg.get("content", ""))
        if text:
            cleaned.append({"role": msg["role"], "content": text})
    if image_generation and len(cleaned) > _IMAGE_GEN_HISTORY_MAX:
        cleaned = cleaned[-_IMAGE_GEN_HISTORY_MAX:]
    return cleaned


def _build_gemini_history(messages: list[dict], *, image_generation: bool = False) -> list:
    history = []
    for msg in _prepare_history_messages(messages, image_generation=image_generation):
        role = "user" if msg["role"] == "user" else "model"
        history.append(genai_types.Content(role=role, parts=[genai_types.Part(text=msg["content"])]))
    return history


def _yield_gemini_stream_chunks(stream: Any) -> Iterator[str | tuple]:
    """Gemini ストリームからテキスト・画像チャンクを抽出"""
    full_text = ""
    in_tok = out_tok = 0
    for chunk in stream:
        if getattr(chunk, "usage_metadata", None):
            in_tok = chunk.usage_metadata.prompt_token_count or 0
            out_tok = chunk.usage_metadata.candidates_token_count or 0
        if chunk.text:
            full_text += chunk.text
            yield chunk.text
            continue
        for part in getattr(chunk, "parts", None) or []:
            if getattr(part, "text", None):
                full_text += part.text
                yield part.text
            elif getattr(part, "inline_data", None) and part.inline_data.data:
                mime = part.inline_data.mime_type or "image/png"
                if mime.startswith("image/"):
                    yield ("__image__", part.inline_data.data, mime)
    yield ("__meta__", full_text, in_tok, out_tok)


def _stream_gemini(
    model: str, history: list[dict], user_text: str,
    file_attachments: list[FileAttachment] | None, system_prompt: str,
    temperature: float, max_tokens: int, use_web_search: bool,
    km: KeyManager,
) -> Iterator[str | tuple]:
    if is_image_generation_model(model):
        reference_image = validate_image_generation_attachments(file_attachments)
        yield from _stream_gemini_image(
            model, history, user_text, system_prompt, temperature, max_tokens, km,
            reference_image=reference_image,
        )
        return

    tools = [genai_types.Tool(google_search=genai_types.GoogleSearch())] if use_web_search else []
    config = genai_types.GenerateContentConfig(
        system_instruction=system_prompt.strip() or None,
        temperature=temperature,
        max_output_tokens=max_tokens,
        tools=tools if tools else None,
    )
    rate_retries = 0
    last_err = ""

    while True:
        api_key = km.get_key()
        if not api_key:
            raise ValueError("利用可能な Google API キーがありません。")
        client = genai.Client(api_key=api_key)
        uploaded_names: list[str] = []
        try:
            parts: list = []
            text_suffix = ""
            for att in file_attachments or []:
                if _is_audio_attachment(att):
                    att_part, uploaded_name = _prepare_gemini_attachment(client, att)
                    parts.append(att_part)
                    if uploaded_name:
                        uploaded_names.append(uploaded_name)
                elif _gemini_inline_attachment(att):
                    parts.append(build_gemini_file_part(att))
                else:
                    text_suffix += _attachment_text_suffix(att)
            combined_text = user_text + text_suffix
            parts.append(genai_types.Part(text=combined_text))
            chat = client.chats.create(model=model, config=config, history=_build_gemini_history(history))
            send = parts if len(parts) > 1 else combined_text
            yield from _yield_gemini_stream_chunks(chat.send_message_stream(send))
            return
        except Exception as e:
            last_err = str(e)
            kind = classify_api_error(last_err)
            if kind == "permission_denied":
                km.mark_permission_denied(api_key)
                if km.get_key():
                    continue
                raise ValueError(f"Google API 権限エラー: {last_err}") from e
            if kind == "rate_limit":
                switched = km.mark_rate_limited(api_key)
                if switched and km.get_key():
                    continue
                rate_retries += 1
                if rate_retries >= RATE_LIMIT_MAX_RETRIES:
                    raise ValueError(f"Google API レート制限（リトライ上限）: {last_err}") from e
                wait = min(RATE_LIMIT_BASE_WAIT_SEC * rate_retries, RATE_LIMIT_MAX_WAIT_SEC)
                time.sleep(wait)
                continue
            raise
        finally:
            for uploaded_name in uploaded_names:
                _cleanup_gemini_upload(client, uploaded_name)


def _stream_gemini_image(
    model: str, history: list[dict], user_text: str,
    system_prompt: str, temperature: float, max_tokens: int,
    km: KeyManager,
    reference_image: FileAttachment | None = None,
) -> Iterator[str | tuple]:
    """Gemini 画像生成モデル（response_modalities=IMAGE+TEXT）"""
    config = genai_types.GenerateContentConfig(
        system_instruction=system_prompt.strip() or None,
        temperature=temperature,
        max_output_tokens=max_tokens,
        response_modalities=["IMAGE", "TEXT"],
    )
    contents: list = list(_build_gemini_history(history, image_generation=True))
    user_parts: list = []
    if reference_image:
        user_parts.append(build_gemini_file_part(reference_image))
    prompt_text = (user_text or "").strip()
    if reference_image and not prompt_text:
        prompt_text = (
            "添付画像のイメージ・構図・雰囲気を参照して、"
            "新しい画像を生成してください。"
        )
    elif reference_image:
        prompt_text = (
            f"{prompt_text}\n\n"
            "（添付画像のイメージ・構図・雰囲気を参照してください。）"
        )
    user_parts.append(genai_types.Part(text=prompt_text))
    contents.append(genai_types.Content(role="user", parts=user_parts))

    rate_retries = 0
    last_err = ""

    while True:
        api_key = km.get_key()
        if not api_key:
            raise ValueError("利用可能な Google API キーがありません。")
        try:
            client = genai.Client(api_key=api_key)
            stream = client.models.generate_content_stream(
                model=model, contents=contents, config=config,
            )
            yield from _yield_gemini_stream_chunks(stream)
            return
        except Exception as e:
            last_err = str(e)
            kind = classify_api_error(last_err)
            if kind == "permission_denied":
                km.mark_permission_denied(api_key)
                if km.get_key():
                    continue
                raise ValueError(f"Google API 権限エラー: {last_err}") from e
            if kind == "rate_limit":
                switched = km.mark_rate_limited(api_key)
                if switched and km.get_key():
                    continue
                rate_retries += 1
                if rate_retries >= RATE_LIMIT_MAX_RETRIES:
                    raise ValueError(f"Google API レート制限（リトライ上限）: {last_err}") from e
                wait = min(RATE_LIMIT_BASE_WAIT_SEC * rate_retries, RATE_LIMIT_MAX_WAIT_SEC)
                time.sleep(wait)
                continue
            raise


def _stream_openai(
    model: str, history: list[dict], user_text: str,
    file_attachments: list[FileAttachment] | None,
    system_prompt: str, temperature: float, max_tokens: int,
    km: KeyManager,
) -> Iterator[str | tuple]:
    messages = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    for msg in _prepare_history_messages(history):
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({
        "role": "user",
        "content": build_openai_user_content(file_attachments, user_text),
    })

    rate_retries = 0
    while True:
        api_key = km.get_key()
        if not api_key:
            raise ValueError("利用可能な OpenAI API キーがありません。")
        try:
            client = OpenAI(api_key=api_key)
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": True,
            }
            if _openai_api_style(model) == "reasoning":
                kwargs["max_completion_tokens"] = max_tokens
            else:
                kwargs["max_tokens"] = max_tokens
                kwargs["temperature"] = temperature
            stream = client.chat.completions.create(**kwargs)
            full_text = ""
            in_tok = out_tok = 0
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    full_text += delta
                    yield delta
                if chunk.usage:
                    in_tok = chunk.usage.prompt_tokens or 0
                    out_tok = chunk.usage.completion_tokens or 0
            yield ("__meta__", full_text, in_tok, out_tok)
            return
        except Exception as e:
            kind = classify_api_error(str(e))
            if kind == "permission_denied":
                km.mark_permission_denied(api_key)
                if km.get_key():
                    continue
                raise ValueError(f"OpenAI API 権限エラー: {e}") from e
            if kind == "rate_limit":
                switched = km.mark_rate_limited(api_key)
                if switched and km.get_key():
                    continue
                rate_retries += 1
                if rate_retries >= RATE_LIMIT_MAX_RETRIES:
                    raise ValueError(f"OpenAI API レート制限（リトライ上限）: {e}") from e
                wait = min(RATE_LIMIT_BASE_WAIT_SEC * rate_retries, RATE_LIMIT_MAX_WAIT_SEC)
                time.sleep(wait)
                continue
            raise


def _stream_anthropic(
    model: str, history: list[dict], user_text: str,
    file_attachments: list[FileAttachment] | None,
    system_prompt: str, temperature: float, max_tokens: int,
    km: KeyManager,
) -> Iterator[str | tuple]:
    api_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in _prepare_history_messages(history)
    ]
    api_messages.append({
        "role": "user",
        "content": build_anthropic_user_content(file_attachments, user_text),
    })

    rate_retries = 0
    while True:
        api_key = km.get_key()
        if not api_key:
            raise ValueError("利用可能な Anthropic API キーがありません。")
        try:
            client = anthropic.Anthropic(api_key=api_key)
            full_text = ""
            in_tok = out_tok = 0
            with client.messages.stream(
                model=model, max_tokens=max_tokens,
                system=system_prompt.strip() or anthropic.NOT_GIVEN,
                messages=api_messages, temperature=temperature,
            ) as stream:
                for text in stream.text_stream:
                    full_text += text
                    yield text
                resp = stream.get_final_message()
                in_tok = resp.usage.input_tokens
                out_tok = resp.usage.output_tokens
            yield ("__meta__", full_text, in_tok, out_tok)
            return
        except Exception as e:
            kind = classify_api_error(str(e))
            if kind == "permission_denied":
                km.mark_permission_denied(api_key)
                if km.get_key():
                    continue
                raise ValueError(f"Anthropic API 権限エラー: {e}") from e
            if kind == "rate_limit":
                switched = km.mark_rate_limited(api_key)
                if switched and km.get_key():
                    continue
                rate_retries += 1
                if rate_retries >= RATE_LIMIT_MAX_RETRIES:
                    raise ValueError(f"Anthropic API レート制限（リトライ上限）: {e}") from e
                wait = min(RATE_LIMIT_BASE_WAIT_SEC * rate_retries, RATE_LIMIT_MAX_WAIT_SEC)
                time.sleep(wait)
                continue
            raise


def _stream_ollama(
    model_key: str, history: list[dict], user_text: str,
    file_attachments: list[FileAttachment] | None,
    system_prompt: str, temperature: float,
) -> Iterator[str | tuple]:
    base = _ollama_base_url()
    ollama_model = get_ollama_model_id(model_key)
    ollama_text, ollama_images = build_ollama_user_message(file_attachments, user_text)
    messages = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    for msg in _prepare_history_messages(history):
        messages.append({"role": msg["role"], "content": msg["content"]})
    user_msg: dict[str, Any] = {"role": "user", "content": ollama_text}
    if ollama_images:
        user_msg["images"] = ollama_images
    messages.append(user_msg)

    resp = requests.post(
        f"{base}/api/chat",
        json={"model": ollama_model, "messages": messages, "stream": True,
              "options": {"temperature": temperature}},
        stream=True, timeout=300,
    )
    resp.raise_for_status()
    full_text = ""
    for line in resp.iter_lines():
        if not line:
            continue
        data = json.loads(line)
        chunk = data.get("message", {}).get("content", "")
        if chunk:
            full_text += chunk
            yield chunk
        if data.get("done"):
            break
    # Ollama はトークン数を返さない場合がある
    yield ("__meta__", full_text, len(user_text) // 4, len(full_text) // 4)


def generate_text(
    model: str,
    user_text: str,
    system_prompt: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 8192,
    history: list[dict] | None = None,
) -> tuple[str, int, int]:
    """非ストリーミング単発生成（Google Gemini）"""
    info = get_model_info(model)
    if not info:
        raise ValueError(f"未知のモデル: {model}")
    if info.get("provider") != "google":
        raise ValueError(f"generate_text は Gemini モデルのみ対応です: {model}")
    if not genai:
        raise ValueError("google-genai パッケージがインストールされていません。")

    config = genai_types.GenerateContentConfig(
        system_instruction=system_prompt.strip() or None,
        temperature=temperature,
        max_output_tokens=max_tokens,
    )
    km = KeyManager("google")
    rate_retries = 0
    last_err = ""

    while True:
        api_key = km.get_key()
        if not api_key:
            raise ValueError("利用可能な Google API キーがありません。")
        client = genai.Client(api_key=api_key)
        try:
            chat = client.chats.create(
                model=model,
                config=config,
                history=_build_gemini_history(history or []),
            )
            response = chat.send_message(user_text)
            text = (response.text or "").strip()
            in_tok = out_tok = 0
            if getattr(response, "usage_metadata", None):
                in_tok = response.usage_metadata.prompt_token_count or 0
                out_tok = response.usage_metadata.candidates_token_count or 0
            return text, in_tok, out_tok
        except Exception as e:
            last_err = str(e)
            kind = classify_api_error(last_err)
            if kind == "permission_denied":
                km.mark_permission_denied(api_key)
                if km.get_key():
                    continue
                raise ValueError(f"Google API 権限エラー: {last_err}") from e
            if kind == "rate_limit":
                switched = km.mark_rate_limited(api_key)
                if switched and km.get_key():
                    continue
                rate_retries += 1
                if rate_retries >= RATE_LIMIT_MAX_RETRIES:
                    raise ValueError(f"Google API レート制限（リトライ上限）: {last_err}") from e
                wait = min(RATE_LIMIT_BASE_WAIT_SEC * rate_retries, RATE_LIMIT_MAX_WAIT_SEC)
                time.sleep(wait)
                continue
            raise


def stream_response(
    model: str,
    history: list[dict],
    user_text: str,
    file_attachments: list[FileAttachment] | None,
    system_prompt: str,
    temperature: float,
    max_tokens: int,
    use_web_search: bool,
) -> Iterator[str | tuple]:
    """統一ストリーミングインターフェース"""
    info = get_model_info(model)
    if not info:
        raise ValueError(f"未知のモデル: {model}")

    provider = info["provider"]
    atts = file_attachments or []

    if use_web_search and provider != "google":
        raise ValueError("Web 検索は Gemini モデルのみ対応しています。")
    if atts and use_web_search and is_image_generation_model(model):
        raise ValueError("画像生成モデルでは Web 検索は利用できません。")

    if is_image_generation_model(model) and atts:
        validate_image_generation_attachments(atts)

    if attachments_have_audio(atts) and provider != "google":
        raise ValueError("音声ファイルを含む添付は Gemini モデルのみ対応しています。")

    if provider == "google":
        if not genai:
            raise ValueError("google-genai パッケージがインストールされていません。")
        km = KeyManager("google")
        yield from _stream_gemini(
            model, history, user_text, atts or None, system_prompt,
            temperature, max_tokens, use_web_search, km,
        )
    elif provider == "openai":
        if not OpenAI:
            raise ValueError("openai パッケージがインストールされていません。")
        km = KeyManager("openai")
        yield from _stream_openai(
            model, history, user_text, atts or None,
            system_prompt, temperature, max_tokens, km,
        )
    elif provider == "anthropic":
        if not anthropic:
            raise ValueError("anthropic パッケージがインストールされていません。")
        km = KeyManager("anthropic")
        yield from _stream_anthropic(
            model, history, user_text, atts or None,
            system_prompt, temperature, max_tokens, km,
        )
    elif provider == "ollama":
        yield from _stream_ollama(
            model, history, user_text, atts or None, system_prompt, temperature,
        )
    else:
        raise ValueError(f"未対応プロバイダ: {provider}")


def web_search_supported(model: str) -> bool:
    info = get_model_info(model) or {}
    return info.get("provider") == "google"
