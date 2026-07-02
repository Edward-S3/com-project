"""複数ソースの統合・要約。"""

from __future__ import annotations

import json
from typing import List, Optional

from core.file_ingest import IngestedSource
from core.orchestrator import Orchestrator


def build_synthesis_prompt(sources: List[IngestedSource], purpose: str, audience: str) -> str:
    blocks = []
    for s in sources:
        blocks.append(
            f"### ソース: {s.name} ({s.source_type})\n{s.text[:12000]}\n"
        )
    return f"""以下の複数ソースを統合し、プレゼン資料作成用のコンテキストを作成してください。

生成目的: {purpose}
想定利用相手: {audience}

ソース間の矛盾や情報の濃淡があれば internal_notes に記録してください。

出力はJSON:
{{
  "summary": "統合要約(日本語)",
  "key_topics": ["トピック1", "..."],
  "internal_notes": "矛盾・濃淡のメモ",
  "recommended_tone": "formal|casual|technical",
  "suggested_palette": "Urban Skyline|Metro Blue|Sky Fresh|City Dawn|Slate Modern|Harbor Light のいずれか（白背景・濃色テキストで視認性を確保）"
}}

ソース:
{chr(10).join(blocks)}
"""


def synthesize_content(
    orchestrator: Orchestrator,
    sources: List[IngestedSource],
    purpose: str,
    audience: str,
    *,
    cancel_event=None,
) -> dict:
    prompt = build_synthesis_prompt(sources, purpose, audience)
    resp = orchestrator.run_task(
        "content_synthesis",
        prompt,
        json_mode=True,
        cancel_event=cancel_event,
    )
    if not resp:
        return {"summary": "\n\n".join(s.text for s in sources), "key_topics": [], "internal_notes": ""}
    try:
        return json.loads(resp.text)
    except json.JSONDecodeError:
        return {"summary": resp.text, "key_topics": [], "internal_notes": ""}
