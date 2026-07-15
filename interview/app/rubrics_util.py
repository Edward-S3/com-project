"""Load rubrics.json and build prompt fragments (C-10)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import DATA_DIR

RUBRICS_PATH = DATA_DIR / "rubrics.json"


@lru_cache(maxsize=1)
def load_rubrics() -> dict[str, Any]:
    with RUBRICS_PATH.open(encoding="utf-8") as fp:
        return json.load(fp)


def get_judge_perspectives(user_role: str) -> list[str]:
    rubrics = load_rubrics()
    if user_role == "supervisor":
        return list(rubrics["judge_perspectives"]["supervisor"])
    return list(rubrics["judge_perspectives"]["subordinate"])


def build_rubric_prompt_excerpt() -> str:
    rubrics = load_rubrics()
    forbidden = rubrics["forbidden_expressions"]
    verbs = "、".join(forbidden["action_verbs"])
    quants = "、".join(forbidden["vague_quantifiers"])
    principles = "\n".join(f"- {p}" for p in rubrics["principles"])
    smart_lines = []
    for aspect in rubrics["smart_kigutakaso"]["aspects"]:
        smart_lines.append(
            f"- {aspect['kanji']}({aspect['id']}): {aspect['criterion']} "
            f"良例:{aspect['good_example']} 悪例:{aspect['bad_example']}"
        )
    return (
        f"【禁止表現】行動目標: {verbs} / 不明確基準: {quants}\n"
        f"【き・ぐ・た・か・そ（期限・具体・達成・関連・測定）】\n" + "\n".join(smart_lines) + "\n"
        f"【原則】\n{principles}"
    )


def strip_hidden_facts(persona: dict[str, Any]) -> dict[str, Any]:
    """Return persona safe for client exposure."""
    safe = dict(persona)
    safe.pop("hidden_facts", None)
    safe.pop("hidden_facts_internal", None)
    if "persona" in safe and isinstance(safe["persona"], dict):
        inner = dict(safe["persona"])
        inner.pop("hidden_facts", None)
        safe["persona"] = inner
    return safe
