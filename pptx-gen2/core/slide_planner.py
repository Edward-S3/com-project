"""枚数・構成・所要時間の設計。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.file_ingest import IngestedSource, local_stats
from core.orchestrator import Orchestrator


@dataclass
class SlidePlan:
    min_slides: int
    max_slides: int
    recommended_slides: int
    presentation_minutes: int
    rationale: str
    warning: Optional[str] = None


def recommend_locally(sources: List[IngestedSource]) -> SlidePlan:
    """LLMを使わない簡易推定。"""
    stats = local_stats(sources)
    chars = stats["total_chars"]
    duration = stats["total_duration_sec"]
    topic_est = max(3, min(25, math.ceil(chars / 1500) + math.ceil(duration / 120)))
    slides = max(5, min(30, topic_est))
    minutes = max(5, int(slides * 1.2))
    rationale = f"ソースの分量から{slides}枚構成・約{minutes}分を推奨(簡易推定)"
    return SlidePlan(
        min_slides=max(3, slides - 2),
        max_slides=slides + 3,
        recommended_slides=slides,
        presentation_minutes=minutes,
        rationale=rationale,
    )


def plan_with_llm(
    orchestrator: Orchestrator,
    synthesis: dict,
    sources: List[IngestedSource],
    *,
    min_slides: Optional[int] = None,
    max_slides: Optional[int] = None,
    minutes: Optional[int] = None,
    cancel_event=None,
) -> SlidePlan:
    constraints = []
    if min_slides:
        constraints.append(f"最低枚数: {min_slides}")
    if max_slides:
        constraints.append(f"最大枚数: {max_slides}")
    if minutes:
        constraints.append(f"想定時間: {minutes}分")
    prompt = f"""プレゼン構成を設計してください。

統合コンテキスト:
{json.dumps(synthesis, ensure_ascii=False)}

制約: {', '.join(constraints) or 'なし'}

JSONで返答:
{{
  "recommended_slides": 10,
  "presentation_minutes": 15,
  "outline": [{{"title": "...", "bullets": ["..."]}}],
  "rationale": "...",
  "warning": null
}}
"""
    resp = orchestrator.run_task(
        "slide_structure_planning",
        prompt,
        json_mode=True,
        cancel_event=cancel_event,
    )
    if not resp:
        local = recommend_locally(sources)
        return local
    data = json.loads(resp.text)
    rec = int(data.get("recommended_slides", 10))
    mins = int(data.get("presentation_minutes", 15))
    warning = data.get("warning")
    if min_slides and rec < min_slides:
        warning = (warning or "") + " 指定枚数に対して情報が多い可能性があります。"
    if max_slides and rec > max_slides:
        warning = (warning or "") + " 指定枚数では情報を圧縮しすぎる可能性があります。"
    return SlidePlan(
        min_slides=min_slides or max(3, rec - 2),
        max_slides=max_slides or rec + 2,
        recommended_slides=rec,
        presentation_minutes=mins,
        rationale=data.get("rationale", ""),
        warning=warning,
    )


def build_slide_plan(
    sources: List[IngestedSource],
    synthesis: dict,
    orchestrator: Optional[Orchestrator],
    *,
    use_recommend: bool,
    use_ai_analysis: bool,
    min_slides: Optional[int],
    max_slides: Optional[int],
    minutes: Optional[int],
    cancel_event=None,
) -> SlidePlan:
    if use_recommend and not use_ai_analysis:
        plan = recommend_locally(sources)
        if min_slides or max_slides or minutes:
            plan.min_slides = min_slides or plan.min_slides
            plan.max_slides = max_slides or plan.max_slides
            if minutes:
                plan.presentation_minutes = minutes
        return plan
    if orchestrator and (use_ai_analysis or not use_recommend):
        return plan_with_llm(
            orchestrator,
            synthesis,
            sources,
            min_slides=min_slides,
            max_slides=max_slides,
            minutes=minutes,
            cancel_event=cancel_event,
        )
    return recommend_locally(sources)
