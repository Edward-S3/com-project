"""Anthropic API（V5: 目次生成 + 章ごと分割生成 + 検証リトライ）"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import anthropic
from pydantic import BaseModel

from src.config import (
    DEFAULT_SLIDE_COUNT,
    ENV_PATH,
    FAST_MODEL,
    MAX_SOURCE_CHARS,
    MIN_SLIDE_COUNT,
    PROMPTS_DIR,
    STRUCTURED_MODEL,
    load_env,
)
from src.schemas import (
    ChapterOutline,
    ChapterSlidesResult,
    PresentationData,
    PresentationOutline,
    SlideContent,
)
from src.validator import (
    MAX_VALIDATION_RETRIES,
    validate_chapter_slides,
    validate_outline,
    validate_presentation,
)

MAX_API_KEYS = 20
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_WAIT_SEC = 2

ProgressCallback = Callable[[str, int, int], None]


class AnthropicClientError(Exception):
    def __init__(self, message: str, *, partial: PresentationData | None = None, issues: list[str] | None = None):
        super().__init__(message)
        self.partial = partial
        self.issues = issues or []


@dataclass
class GenerationResult:
    data: PresentationData
    outline: PresentationOutline | None = None
    retries_used: int = 0
    validation_warnings: list[str] = field(default_factory=list)


def _load_prompt(name: str, fallback: str) -> str:
    path = PROMPTS_DIR / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return fallback


def _load_api_keys() -> list[str]:
    load_env()
    raw = os.getenv("ANTHROPIC_API_KEYS") or os.getenv("ANTHROPIC_API_KEY") or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()][:MAX_API_KEYS]
    if not keys:
        raise AnthropicClientError(
            f"ANTHROPIC_API_KEY が見つかりません。{ENV_PATH} に ANTHROPIC_API_KEY を設定してください。"
        )
    return keys


class _KeyManager:
    def __init__(self, keys: list[str]) -> None:
        self._keys = keys
        self._index = 0
        self._exhausted: set[str] = set()

    def get_key(self) -> str | None:
        for _ in range(len(self._keys)):
            key = self._keys[self._index]
            self._index = (self._index + 1) % len(self._keys)
            if key not in self._exhausted:
                return key
        return None

    def mark_bad(self, key: str) -> bool:
        self._exhausted.add(key)
        return bool(self.get_key())


def _is_rate_limit(err: str) -> bool:
    low = err.lower()
    return "429" in low or "rate" in low or "quota" in low or "overloaded" in low


def _truncate_source(text: str) -> str:
    if len(text) <= MAX_SOURCE_CHARS:
        return text
    half = MAX_SOURCE_CHARS // 2
    return text[:half] + "\n\n...[中略]...\n\n" + text[-half:]


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _response_text(response: anthropic.types.Message) -> str:
    parts = [block.text for block in response.content if block.type == "text" and block.text]
    return "\n".join(parts).strip()


def _schema_hint(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)


def _call_anthropic_structured(
    prompt: str,
    schema: type[BaseModel],
    km: _KeyManager,
    *,
    system_instruction: str,
) -> BaseModel:
    """JSON テキスト応答 + Pydantic 検証（構造化出力の grammar 制限を回避）"""
    json_system = (
        f"{system_instruction.strip()}\n\n"
        "## 出力形式\n"
        f"- 応答は {schema.__name__} 型の有効な JSON オブジェクトのみ（説明文・コードブロック禁止）\n"
        "- フィールド名はスキーマと完全一致させること（独自のキー名は禁止）\n"
        "- 文字列はダブルクォート、null は JSON の null を使用\n\n"
        "## JSON スキーマ\n"
        f"{_schema_hint(schema)}"
    )
    last_err = ""
    for model in (STRUCTURED_MODEL, FAST_MODEL):
        rate_retries = 0
        while True:
            api_key = km.get_key()
            if not api_key:
                break
            try:
                client = anthropic.Anthropic(api_key=api_key)
                response = client.messages.create(
                    model=model,
                    max_tokens=16384,
                    system=json_system,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.45,
                )
                if response.stop_reason == "max_tokens":
                    raise AnthropicClientError("応答がトークン上限で切り詰められました。")
                text = _response_text(response)
                if not text:
                    raise AnthropicClientError("構造化データを取得できませんでした。")
                return schema.model_validate_json(_extract_json(text))
            except AnthropicClientError:
                raise
            except json.JSONDecodeError as exc:
                last_err = f"JSON 解析エラー: {exc}"
                break
            except Exception as exc:
                err_name = type(exc).__name__
                if err_name == "ValidationError":
                    last_err = f"JSON スキーマ不一致: {exc}"
                    break
                last_err = str(exc)
                if _is_rate_limit(last_err):
                    if km.mark_bad(api_key):
                        continue
                    rate_retries += 1
                    if rate_retries >= RATE_LIMIT_MAX_RETRIES:
                        break
                    time.sleep(min(RATE_LIMIT_BASE_WAIT_SEC * rate_retries, 30))
                    continue
                if km.mark_bad(api_key):
                    continue
                break
    raise AnthropicClientError(last_err or "API 呼び出しに失敗しました。")


def _build_outline_prompt(source_text: str, user_prompt: str, slide_count: int, fix_notes: str = "") -> str:
    target = max(slide_count, MIN_SLIDE_COUNT)
    base = f"""以下のソース資料と利用目的に基づき、プレゼンテーションの目次（章構成）を設計してください。

【利用目的・ターゲット・状況】
{user_prompt.strip()}

【目標スライド枚数】
合計 {target} 枚以上（表紙・章扉・まとめを含む）

【ソース資料】
{source_text}
"""
    if fix_notes:
        base += f"\n【前回の検証エラー — 必ず修正してください】\n{fix_notes}\n"
    return base


def _build_chapter_prompt(
    source_text: str,
    user_prompt: str,
    outline: PresentationOutline,
    chapter: ChapterOutline,
    fix_notes: str = "",
) -> str:
    base = f"""あなたは第{chapter.chapter_number}章（タイトル: {chapter.chapter_title}）のスライドを {chapter.estimated_slides} 枚生成してください。

【章のフォーカス】
{chapter.source_context_focus}

【プレゼン全体タイトル】
{outline.presentation_title}

【利用目的】
{user_prompt.strip()}

【テーマ】
palette: {outline.theme.palette_name}
dominant: {outline.theme.dominant_color} / support: {outline.theme.support_color} / accent: {outline.theme.accent_color}

【ソース資料（該当章に関連する部分を重点的に使用）】
{source_text}

## 厳守
- slides 配列の1枚目は CHAPTER_TITLE（章扉）
- 合計 {chapter.estimated_slides} 枚（章扉含む）
- 情報圧縮禁止。具体的な事実・数値・手順を記載
- ICON_LIST は絵文字アイコン（💡🔍⚠️🏢👥📈 等）を使用
"""
    if fix_notes:
        base += f"\n【前回の検証エラー — 必ず修正してください】\n{fix_notes}\n"
    return base


def _make_cover_slide(outline: PresentationOutline, user_prompt: str) -> SlideContent:
    return SlideContent(
        slide_number=1,
        layout_type="TITLE",
        title=outline.presentation_title,
        subtitle=user_prompt.strip()[:120] or None,
        key_message="本資料の全体概要",
    )


def _make_closing_slide(outline: PresentationOutline) -> SlideContent:
    return SlideContent(
        slide_number=0,
        layout_type="TITLE",
        title="まとめ",
        subtitle=outline.presentation_title,
        key_message="要点の振り返りと次のアクション",
    )


def _renumber_slides(slides: list[SlideContent]) -> list[SlideContent]:
    return [s.model_copy(update={"slide_number": i}) for i, s in enumerate(slides, 1)]


def _assemble_presentation(outline: PresentationOutline, chapter_slide_lists: list[list[SlideContent]], user_prompt: str) -> PresentationData:
    all_slides: list[SlideContent] = [_make_cover_slide(outline, user_prompt)]
    for chapter_slides in chapter_slide_lists:
        all_slides.extend(chapter_slides)
    all_slides.append(_make_closing_slide(outline))
    return PresentationData(theme=outline.theme, slides=_renumber_slides(all_slides))


def generate_outline(
    source_text: str,
    user_prompt: str,
    slide_count: int,
    km: _KeyManager,
    fix_notes: str = "",
) -> PresentationOutline:
    prompt = _build_outline_prompt(source_text, user_prompt, slide_count, fix_notes)
    system = _load_prompt("outline_instruction.txt", "章立て目次 JSON を設計してください。")
    result = _call_anthropic_structured(prompt, PresentationOutline, km, system_instruction=system)
    if not isinstance(result, PresentationOutline):
        raise AnthropicClientError("目次データの取得に失敗しました。")
    return result


def generate_chapter_slides(
    source_text: str,
    user_prompt: str,
    outline: PresentationOutline,
    chapter: ChapterOutline,
    km: _KeyManager,
    fix_notes: str = "",
) -> list[SlideContent]:
    prompt = _build_chapter_prompt(source_text, user_prompt, outline, chapter, fix_notes)
    system = _load_prompt("chapter_instruction.txt", "章のスライド JSON を設計してください。")
    result = _call_anthropic_structured(prompt, ChapterSlidesResult, km, system_instruction=system)
    if not isinstance(result, ChapterSlidesResult):
        raise AnthropicClientError(f"第{chapter.chapter_number}章のスライド取得に失敗しました。")
    return result.slides


def _noop_progress(message: str, current: int, total: int) -> None:
    pass


def generate_presentation_data(
    source_text: str,
    user_prompt: str,
    slide_count: int = DEFAULT_SLIDE_COUNT,
    *,
    on_progress: ProgressCallback | None = None,
) -> GenerationResult:
    """V5: 目次生成 → 章ごとスライド生成 → 結合"""
    progress = on_progress or _noop_progress
    km = _KeyManager(_load_api_keys())
    source = _truncate_source(source_text)
    effective_count = max(slide_count, MIN_SLIDE_COUNT)
    outline_fix = ""
    last_outline: PresentationOutline | None = None
    last_outline_issues: list[str] = []

    for attempt in range(MAX_VALIDATION_RETRIES + 1):
        progress("目次を生成中...", 0, 1)
        outline = generate_outline(source, user_prompt, effective_count, km, outline_fix)
        last_outline = outline
        outline_issues = validate_outline(outline, effective_count)
        if not outline_issues:
            break
        last_outline_issues = outline_issues
        outline_fix = "\n".join(f"- {x}" for x in outline_issues)
    else:
        if last_outline is None:
            raise AnthropicClientError("目次を生成できませんでした。")
        outline = last_outline

    chapters = outline.chapters
    total_steps = 1 + len(chapters) + 1
    chapter_slide_lists: list[list[SlideContent]] = []
    total_retries = 0
    all_warnings: list[str] = last_outline_issues

    for idx, chapter in enumerate(chapters):
        progress(
            f"第{chapter.chapter_number}章を生成中...（{chapter.chapter_title}）",
            idx + 1,
            total_steps - 1,
        )
        chapter_fix = ""
        last_chapter_slides: list[SlideContent] | None = None
        last_chapter_issues: list[str] = []

        for attempt in range(MAX_VALIDATION_RETRIES + 1):
            slides = generate_chapter_slides(source, user_prompt, outline, chapter, km, chapter_fix)
            last_chapter_slides = slides
            issues = validate_chapter_slides(slides, chapter)
            if not issues:
                chapter_slide_lists.append(slides)
                total_retries += attempt
                break
            last_chapter_issues = issues
            chapter_fix = "\n".join(f"- {x}" for x in issues)
            total_retries += 1
        else:
            if last_chapter_slides:
                chapter_slide_lists.append(last_chapter_slides)
                all_warnings.extend(last_chapter_issues)
            else:
                raise AnthropicClientError(f"第{chapter.chapter_number}章のスライドを生成できませんでした。")

    progress("スライドを結合中...", total_steps - 1, total_steps - 1)
    data = _assemble_presentation(outline, chapter_slide_lists, user_prompt)

    presentation_issues = validate_presentation(data)
    if presentation_issues:
        all_warnings.extend(presentation_issues)

    return GenerationResult(
        data=data,
        outline=outline,
        retries_used=total_retries,
        validation_warnings=all_warnings,
    )
