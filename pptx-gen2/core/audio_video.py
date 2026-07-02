"""Gemini API による音声/動画・YouTube理解。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

from core.file_ingest import IngestedSource
from core.llm_clients import LLMClientManager
from core.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

AUDIO_VIDEO_PROMPT = (
    "このメディアの内容を、後続のスライド資料作成に使えるよう詳細に要約してください。"
    "話者の主張、データ、結論を日本語で整理してください。"
)
YOUTUBE_PROMPT = (
    "この動画の内容を、後続のスライド資料作成に使えるよう詳細に要約してください。"
    "※YouTube URL直接入力はGeminiプレビュー機能のため、将来的に料金・制限が変更される可能性があります。"
)


def process_audio_video_sources(
    sources: List[IngestedSource],
    orchestrator: Orchestrator,
    llm: LLMClientManager,
    *,
    cancel_event=None,
    on_youtube_progress: Optional[Callable[[int, int], None]] = None,
) -> List[IngestedSource]:
    """音声/動画ファイルをGeminiでテキスト化する。"""
    av_model = orchestrator._resolve_task_model("audio_video_understanding", "gemini")
    out: List[IngestedSource] = []
    for src in sources:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("キャンセル")
        if src.source_type not in {"audio", "video"} or not src.media_path:
            out.append(src)
            continue
        try:
            resp = llm.generate_with_media_file(
                src.media_path,
                AUDIO_VIDEO_PROMPT,
                src.mime_type or "application/octet-stream",
                cancel_event=cancel_event,
                model=av_model,
            )
            src.text = resp.text
            src.metadata["transcribed"] = True
        except Exception as exc:
            logger.error("メディア理解失敗 %s: %s", src.name, exc)
            src.text = f"[メディア理解失敗: {src.name}]"
        out.append(src)
    return out


def process_youtube_urls(
    urls: List[str],
    llm: LLMClientManager,
    *,
    cancel_event=None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    model: Optional[str] = None,
) -> tuple[List[IngestedSource], int]:
    """YouTube URLを1本ずつGeminiに渡す。失敗数を返す。"""
    sources: List[IngestedSource] = []
    failed = 0
    total = len(urls)
    for i, url in enumerate(urls, 1):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("キャンセル")
        if on_progress:
            on_progress(i, total)
        try:
            resp = llm.generate_with_file_uri(
                url, YOUTUBE_PROMPT, cancel_event=cancel_event, model=model
            )
            sources.append(
                IngestedSource(
                    name=f"YouTube: {url[:60]}",
                    source_type="youtube",
                    text=resp.text,
                    metadata={"url": url, "char_count": len(resp.text)},
                )
            )
        except Exception as exc:
            logger.warning("YouTube読み込み失敗 %s: %s", url, exc)
            failed += 1
    return sources, failed
