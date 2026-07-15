"""AI persona generation for interview partner role."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, Settings
from app.models import (
    DIFFICULTY_LABELS,
    EVALUATOR_BY_GRADE,
    INITIAL_GOAL_PERCENT,
    SessionConfig,
    subordinate_grade,
)
from app.providers.base import Message
from app.providers.registry import get_provider_for_role
from app.rubrics_util import build_rubric_prompt_excerpt

GRADES_PATH = DATA_DIR / "grades.json"

GAP_PATTERNS = {
    "a": "部下マイナス／上司プラス",
    "b": "部下プラス／上司マイナス",
    "c": "双方マイナス",
    "d": "双方プラス",
}


def _load_grades() -> dict[str, Any]:
    with GRADES_PATH.open(encoding="utf-8") as fp:
        return json.load(fp)


def _grade_record(grade_id: str) -> dict[str, Any]:
    for grade in _load_grades()["grades"]:
        if grade["id"] == grade_id:
            return grade
    raise ValueError(f"Unknown grade: {grade_id}")


def _pick_trait(difficulty: str, *, force_trait: str | None = None) -> str:
    if force_trait:
        return force_trait
    if difficulty == "sme":
        return random.choice(["modest", "vague", "overconfident"])
    if difficulty == "enterprise":
        return random.choice(["modest", "vague"])
    return random.choice(["modest", "overconfident"])


def build_persona(
    settings: Settings,
    user_profile: dict[str, Any],
    config: SessionConfig,
    *,
    trait: str | None = None,
    gap_pattern: str | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Generate persona JSON for the AI partner."""
    user_grade = user_profile["grade"]
    ai_role = config.ai_role
    trait_name = _pick_trait(config.difficulty, force_trait=trait)

    if ai_role == "subordinate":
        ai_grade = subordinate_grade(user_grade, 1)
        ai_grade_record = _grade_record(ai_grade)
        goal_percent = INITIAL_GOAL_PERCENT.get(
            (trait_name, config.difficulty), 80 if config.difficulty == "sme" else 70
        )
        persona: dict[str, Any] = {
            "display_name": f"{user_profile.get('name', '利用者')}の部下",
            "role": "subordinate",
            "grade": ai_grade,
            "role_title": ai_grade_record["role_title"],
            "age": max(22, user_profile.get("age", 30) - random.randint(3, 8)),
            "department": user_profile.get("department", ""),
            "trait": trait_name,
            "initial_goal_percent": goal_percent,
            "current_goal_percent": goal_percent,
            "gap_pattern": gap_pattern,
            "self_rating_claim": None,
            "actual_performance_level": None,
        }
        if config.scene == "final" and gap_pattern:
            persona["gap_pattern_label"] = GAP_PATTERNS.get(gap_pattern, gap_pattern)
            if gap_pattern == "b":
                persona["self_rating_claim"] = "A"
                persona["actual_performance_level"] = "C"
        hidden = _default_hidden_facts_subordinate(config, trait_name, gap_pattern)
    else:
        ai_grade = EVALUATOR_BY_GRADE.get(user_grade, "4-1")
        ai_grade_record = _grade_record(ai_grade)
        persona = {
            "display_name": f"{user_profile.get('name', '利用者')}の上司",
            "role": "supervisor",
            "grade": ai_grade,
            "role_title": ai_grade_record["role_title"],
            "age": user_profile.get("age", 35) + random.randint(5, 15),
            "department": user_profile.get("department", ""),
            "trait": trait_name,
        }
        hidden = _default_hidden_facts_supervisor(config)

    persona["hidden_facts"] = hidden
    persona["mode_code"] = config.mode_code
    persona["difficulty_label"] = DIFFICULTY_LABELS[config.difficulty]

    if use_llm and settings.gemini_api_key:
        persona = _enrich_with_llm(settings, user_profile, config, persona)

    return persona


def _default_hidden_facts_subordinate(
    config: SessionConfig,
    trait: str,
    gap_pattern: str | None,
) -> list[str]:
    facts = ["実際の数値実績は本人が聞かれないと開示しない"]
    if config.scene == "initial":
        facts.append(f"当初申告の目標水準は最適100に対し約{INITIAL_GOAL_PERCENT.get((trait, config.difficulty), 80)}%")
    if config.scene == "final" and gap_pattern == "b":
        facts.extend(
            [
                "自己評価はAと主張するが実績はC相当",
                "具体的な数値根拠を聞かれないと曖昧に答える",
                "ねぎらいを受けると少し態度が和らぐ",
            ]
        )
    return facts


def _default_hidden_facts_supervisor(config: SessionConfig) -> list[str]:
    if config.scene == "initial":
        return ["部下の曖昧な目標はき・ぐ・た・か・その観点で差し戻す", "禁止表現があれば必ず指摘する"]
    return ["部下の自己評価根拠を数値・事実で確認する", "コンサル難易度では評価点7＝真の目的まで問う"]


def _enrich_with_llm(
    settings: Settings,
    user_profile: dict[str, Any],
    config: SessionConfig,
    persona: dict[str, Any],
) -> dict[str, Any]:
    provider = get_provider_for_role(settings, "persona", allow_mock=True)
    system = (
        "人事評価マニュアルに基づき面談相手のペルソナをJSONで生成する。"
        "hidden_factsは配列。display_name, role, grade, role_titleは維持。"
        "JSONのみ返す。"
    )
    prompt = json.dumps(
        {"user_profile": user_profile, "config": config.mode_code, "base_persona": persona},
        ensure_ascii=False,
    )
    try:
        raw = provider.generate(system, [Message(role="user", content=prompt)], json_mode=True)
        enriched = json.loads(raw)
        if isinstance(enriched, dict):
            merged = {**persona, **enriched}
            if "hidden_facts" not in merged:
                merged["hidden_facts"] = persona.get("hidden_facts", [])
            return merged
    except (json.JSONDecodeError, RuntimeError, ValueError):
        pass
    return persona
