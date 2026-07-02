"""日本語スピーカーノート生成。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from core.orchestrator import Orchestrator


def generate_narratives(
    orchestrator: Orchestrator,
    slides: List[Dict[str, Any]],
    plan_minutes: int,
    *,
    cancel_event=None,
) -> List[str]:
    prompt = f"""各スライドのスピーカーノートを日本語で生成してください。
全体のプレゼン時間は約{plan_minutes}分です。自然な話し言葉で。

スライド:
{json.dumps(slides, ensure_ascii=False)}

JSON: {{"notes": ["スライド1のノート", "..."]}}
"""
    resp = orchestrator.run_task(
        "japanese_narrative",
        prompt,
        json_mode=True,
        cancel_event=cancel_event,
    )
    if not resp:
        return [f"{s.get('title', '')}について説明します。" for s in slides]
    try:
        data = json.loads(resp.text)
        notes = data.get("notes", [])
        while len(notes) < len(slides):
            notes.append("")
        return notes[: len(slides)]
    except json.JSONDecodeError:
        return [resp.text] * len(slides)
