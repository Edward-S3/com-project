"""Gemini API による OCR・レイアウト解析"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from io import BytesIO

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image

from bbox_utils import sanitize_bbox
from config import (
    GEMINI_REQUEST_TIMEOUT_MS,
    LOCAL_ENV_PATH,
    NAI_ENV_PATH,
    PROMPTS_DIR,
    VISION_MODEL,
)

logger = logging.getLogger(__name__)

MAX_API_KEYS = 20
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_WAIT_SEC = 2

VALID_ROLES = {"title", "body", "caption", "other"}


@dataclass
class ParseResult:
    data: dict
    fallback: bool = False
    error: str | None = None


class ImageParserError(Exception):
    pass


def _load_api_keys() -> list[str]:
    load_dotenv(NAI_ENV_PATH)
    load_dotenv(LOCAL_ENV_PATH, override=True)
    raw = (
        os.getenv("GOOGLE_API_KEYS")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEYS")
        or os.getenv("GEMINI_API_KEY")
        or ""
    )
    keys = [k.strip() for k in raw.split(",") if k.strip()][:MAX_API_KEYS]
    if not keys:
        raise ImageParserError(
            f"API キーが見つかりません。{NAI_ENV_PATH} を確認してください。"
        )
    return keys


def _load_system_prompt() -> str:
    path = PROMPTS_DIR / "system_prompt.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return "スライド画像を解析し、指定JSONのみを返してください。"


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _fallback_structure(slide_index: int, image: Image.Image, image_dpi: int) -> dict:
    return {
        "slide_index": slide_index,
        "slide_width_px": image.width,
        "slide_height_px": image.height,
        "image_dpi": image_dpi,
        "text_blocks": [],
        "image_blocks": [],
        "use_full_image": True,
    }


def _normalize_bbox(bbox: dict, img_w: int, img_h: int) -> dict | None:
    return sanitize_bbox(bbox, img_w, img_h)


def _normalize_slide_json(raw: dict, slide_index: int, image: Image.Image, image_dpi: int) -> dict:
    width = int(raw.get("slide_width_px") or image.width)
    height = int(raw.get("slide_height_px") or image.height)
    dpi = int(raw.get("image_dpi") or image_dpi)

    text_blocks = []
    for i, block in enumerate(raw.get("text_blocks") or []):
        bbox = _normalize_bbox(block.get("bbox") or {}, image.width, image.height)
        content = str(block.get("content") or "").strip()
        if not bbox or not content:
            continue
        role = str(block.get("role") or "other")
        if role not in VALID_ROLES:
            role = "other"
        font_size = block.get("font_size_pt")
        if font_size is None:
            font_size = round(bbox["height"] / dpi * 72, 1)
        text_blocks.append(
            {
                "id": block.get("id") or f"t_{i}",
                "content": content,
                "role": role,
                "bbox": bbox,
                "font_size_pt": float(font_size),
            }
        )

    image_blocks = []
    for i, block in enumerate(raw.get("image_blocks") or []):
        bbox = _normalize_bbox(block.get("bbox") or {}, image.width, image.height)
        if not bbox:
            continue
        image_blocks.append(
            {
                "id": block.get("id") or f"img_{i}",
                "bbox": bbox,
            }
        )

    return {
        "slide_index": slide_index,
        "slide_width_px": width,
        "slide_height_px": height,
        "image_dpi": dpi,
        "text_blocks": text_blocks,
        "image_blocks": image_blocks,
        "use_full_image": False,
    }


def _call_gemini(image: Image.Image, slide_index: int) -> str:
    keys = _load_api_keys()
    buf = BytesIO()
    image.save(buf, format="PNG")
    image_bytes = buf.getvalue()
    system_prompt = _load_system_prompt()
    user_prompt = (
        f"slide_index={slide_index} のスライド画像を解析してください。"
        f"画像サイズ: {image.width}x{image.height}px"
    )

    last_error: Exception | None = None
    for key_idx, api_key in enumerate(keys):
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS),
        )
        for attempt in range(RATE_LIMIT_MAX_RETRIES):
            try:
                logger.info(
                    "スライド %d: Gemini API 呼び出し開始 (model=%s, timeout=%dms, key=%d, attempt=%d)",
                    slide_index,
                    VISION_MODEL,
                    GEMINI_REQUEST_TIMEOUT_MS,
                    key_idx,
                    attempt + 1,
                )
                response = client.models.generate_content(
                    model=VISION_MODEL,
                    contents=[
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_bytes(
                                    data=image_bytes,
                                    mime_type="image/png",
                                ),
                                types.Part.from_text(text=user_prompt),
                            ],
                        )
                    ],
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.1,
                        response_mime_type="application/json",
                    ),
                )
                text = (response.text or "").strip()
                if not text:
                    raise ImageParserError("Gemini から空の応答が返されました")
                return text
            except Exception as exc:
                last_error = exc
                msg = str(exc).lower()
                if "timeout" in msg or "timed out" in msg:
                    logger.error(
                        "スライド %d: Gemini API タイムアウト (%dms): %s",
                        slide_index,
                        GEMINI_REQUEST_TIMEOUT_MS,
                        exc,
                    )
                    raise ImageParserError(
                        f"Gemini API タイムアウト ({GEMINI_REQUEST_TIMEOUT_MS // 1000}s): {exc}"
                    ) from exc
                if "429" in msg or "rate" in msg or "quota" in msg:
                    wait = RATE_LIMIT_BASE_WAIT_SEC * (2**attempt)
                    logger.warning("レート制限: key=%d attempt=%d wait=%ds", key_idx, attempt, wait)
                    time.sleep(wait)
                    continue
                if key_idx < len(keys) - 1:
                    logger.warning("API キー切替: %s", exc)
                    break
                raise
    raise ImageParserError(f"Gemini API 呼び出し失敗: {last_error}")


def _parse_response(text: str, slide_index: int, image: Image.Image, image_dpi: int) -> dict:
    cleaned = _strip_json_fence(text)
    raw = json.loads(cleaned)
    if not isinstance(raw, dict):
        raise ValueError("JSON ルートがオブジェクトではありません")
    return _normalize_slide_json(raw, slide_index, image, image_dpi)


def parse_slide_image(slide_index: int, image: Image.Image, image_dpi: int) -> ParseResult:
    """
    抽出画像を Gemini で解析し中間 JSON を返す。
    失敗時はフォールバック（元画像をそのまま貼り付け）構造を返す。
    """
    for attempt in range(2):
        try:
            text = _call_gemini(image, slide_index)
            data = _parse_response(text, slide_index, image, image_dpi)
            logger.info(
                "スライド %d: 解析完了 (text=%d, images=%d)",
                slide_index,
                len(data["text_blocks"]),
                len(data["image_blocks"]),
            )
            return ParseResult(data=data, fallback=False)
        except Exception as exc:
            logger.warning(
                "スライド %d: 解析失敗 (attempt %d/2): %s",
                slide_index,
                attempt + 1,
                exc,
            )
            if attempt == 0:
                continue
            fb = _fallback_structure(slide_index, image, image_dpi)
            return ParseResult(data=fb, fallback=True, error=str(exc))

    fb = _fallback_structure(slide_index, image, image_dpi)
    return ParseResult(data=fb, fallback=True, error="unknown")
