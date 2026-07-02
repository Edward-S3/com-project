"""スライド単位の構造化JSON生成。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from core.orchestrator import Orchestrator
from core.slide_planner import SlidePlan


SLIDE_SCHEMA = """
各スライド:
{
  "layout_hint": "two_column|grid|stats|timeline|comparison|icon_rows|title|section",
  "title": "スライドタイトル",
  "subtitle": "任意",
  "bullets": ["要点1", "..."],
  "stats": [{"label": "...", "value": "..."}],
  "columns": [["左列"], ["右列"]],
  "is_title_slide": false,
  "is_closing_slide": false,
  "visual_element": "chart|shape|icon|image_placeholder"
}
"""


def generate_payload(
    orchestrator: Orchestrator,
    synthesis: dict,
    plan: SlidePlan,
    purpose: str,
    audience: str,
    *,
    slide_count: int,
    cancel_event=None,
) -> List[Dict[str, Any]]:
    prompt = f"""以下の情報から {slide_count} 枚のスライドJSON配列を生成してください。

目的: {purpose}
相手: {audience}
統合要約: {synthesis.get('summary', '')}
トピック: {synthesis.get('key_topics', [])}
内部メモ: {synthesis.get('internal_notes', '')}
推奨枚数: {plan.recommended_slides}
推奨時間: {plan.presentation_minutes}分

{SLIDE_SCHEMA}

禁止: タイトル下アクセントライン、装飾バー、文字だけのスライド(visual_element必須)
出力は {{"slides": [...]}} のJSONのみ。
"""
    resp = orchestrator.run_task(
        "structured_json_payload",
        prompt,
        json_mode=True,
        cancel_event=cancel_event,
    )
    if not resp:
        return _fallback_slides(synthesis, slide_count)
    try:
        data = json.loads(resp.text)
        slides = data.get("slides", data if isinstance(data, list) else [])
        return slides[:slide_count] if slides else _fallback_slides(synthesis, slide_count)
    except json.JSONDecodeError:
        return _fallback_slides(synthesis, slide_count)


def _fallback_slides(synthesis: dict, count: int) -> List[Dict[str, Any]]:
    topics = synthesis.get("key_topics") or ["概要", "詳細", "まとめ"]
    slides = [
        {
            "layout_hint": "title",
            "title": "プレゼンテーション",
            "subtitle": synthesis.get("summary", "")[:120],
            "bullets": [],
            "is_title_slide": True,
            "visual_element": "shape",
        }
    ]
    for t in topics[: max(1, count - 2)]:
        slides.append(
            {
                "layout_hint": "two_column",
                "title": str(t),
                "bullets": [f"{t}に関する要点"],
                "visual_element": "icon",
            }
        )
    slides.append(
        {
            "layout_hint": "title",
            "title": "ご清聴ありがとうございました",
            "is_closing_slide": True,
            "visual_element": "shape",
        }
    )
    return slides[:count]
