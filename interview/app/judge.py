"""Judge AI service for post-interview reports."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import Settings
from app.models import DIFFICULTY_LABELS, SessionConfig
from app.providers.base import Message
from app.providers.registry import get_provider_for_role
from app.rubrics_util import get_judge_perspectives, load_rubrics

logger = logging.getLogger(__name__)


def score_bounds_from_rating_scale(rating: dict[str, Any]) -> tuple[int, int]:
    """Derive score min/max from rubrics rating_scale entry scores."""
    values = [int(entry["score"]) for entry in rating.values()]
    return min(values), max(values)


def build_judge_json_schema(score_min: int, score_max: int) -> str:
    """Assemble judge output schema with rubric-derived score range."""
    return f"""{{
  "scores": {{"観点名": {score_min}-{score_max}の整数, ...}},
  "good_points": [{{"text": "良い点の説明", "quote": "発言引用"}}],
  "improvements": [{{"text": "改善点", "quote": "発言引用", "principle": "マニュアル原則"}}],
  "overall_evaluation": "全体的な評価テキスト",
  "overall_grade": "S|A|B|C|D",
  "summary": "総評（次回アドバイス含む）",
  "model_answer": "模範解答例1件",
  "avg_score": 数値,
  "goal_level_percent": 数値またはnull,
  "goal_level_reached": true/false/null,
  "feedback_flow_observed": {{"acknowledgment": bool, "deep_dive": bool, "expectation": bool}} | null  // 2Aのみ。1B/2Bはnull
}}"""


# Existing tests import this symbol; keep acknowledgment and live rubric bounds.
_SCORE_MIN, _SCORE_MAX = score_bounds_from_rating_scale(load_rubrics()["rating_scale"])
JUDGE_JSON_SCHEMA = build_judge_json_schema(_SCORE_MIN, _SCORE_MAX)


def _coerce_numeric(value: Any) -> float | None:
    """Return float if value is a finite number (bool excluded); else None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def normalize_judge_scores(
    result: dict[str, Any],
    score_min: int,
    score_max: int,
) -> dict[str, Any]:
    """Clamp/round valid scores; drop non-numeric; recompute avg_score."""
    raw_scores = result.get("scores")
    if not isinstance(raw_scores, dict):
        result["scores"] = {}
        result["avg_score"] = None
        result.pop("score_corrections", None)
        return result

    corrected_scores: dict[str, int] = {}
    corrections: list[dict[str, Any]] = []

    for perspective, original in raw_scores.items():
        numeric = _coerce_numeric(original)
        if numeric is None:
            corrections.append(
                {
                    "perspective": perspective,
                    "original": original,
                    "corrected": None,
                }
            )
            continue

        rounded = int(round(numeric))
        clamped = max(score_min, min(score_max, rounded))
        corrected_scores[perspective] = clamped
        if clamped != original:
            corrections.append(
                {
                    "perspective": perspective,
                    "original": original,
                    "corrected": clamped,
                }
            )

    result["scores"] = corrected_scores
    if corrections:
        result["score_corrections"] = corrections
        logger.info("judge score_corrections=%s", corrections)
    else:
        result.pop("score_corrections", None)

    if corrected_scores:
        result["avg_score"] = sum(corrected_scores.values()) / len(corrected_scores)
    else:
        result["avg_score"] = None
    return result


def build_judge_system_prompt(config: SessionConfig, persona: dict[str, Any]) -> str:
    rubrics = load_rubrics()
    perspectives = get_judge_perspectives(config.role)
    rating = rubrics["rating_scale"]
    score_min, score_max = score_bounds_from_rating_scale(rating)
    schema = build_judge_json_schema(score_min, score_max)
    rating_text = "\n".join(
        f"- {k}({v['score']}): 成果={v['achievement']} / プロセス={v['process']}"
        for k, v in rating.items()
    )
    difficulty_note = {
        "sme": "中小企業水準: マニュアル基本要件で判定。講評はやや簡素。",
        "enterprise": "大企業水準: き・ぐ・た・か・そ完全充足・3段階評価基準・方針整合を要求。",
        "consultant": "コンサル水準: 評価点7＝真の目的・戦略整合まで問う。講評は詳細。",
    }[config.difficulty]

    mode_note = ""
    if config.mode_code == "1A":
        mode_note = (
            "1A追加: AI部下の最終目標水準(100=最適)を推定しgoal_level_percentを算出。"
            "85〜95ならgoal_level_reached=true。feedback_flow_observedはnull。"
        )
    elif config.mode_code == "2A":
        mode_note = (
            "2A追加: 上司（利用者）のねぎらい(acknowledgment)・根拠深掘り(deep_dive)・"
            "期待水準提示(expectation)の実施をfeedback_flow_observedに記録。"
        )
    else:
        mode_note = "1B/2B: feedback_flow_observedは必ずnullを返す（2A専用フィールド）。"

    return f"""あなたは面談研修の審判AI。演技ではなく採点のみ。
モード: {config.mode_code} / 難易度: {DIFFICULTY_LABELS[config.difficulty]}
利用者役割: {'上司' if config.role == 'supervisor' else '部下'}
{difficulty_note}
{mode_note}

【評語基準】
{rating_text}

【採点観点（scoresのキーはこの一覧と完全一致）】
{json.dumps(perspectives, ensure_ascii=False)}

【原則】
- 5=期待水準通り。安易に高得点をつけない（寛大化回避）
- 発言引用を必ず含める
- improvements[].principle にはマニュアル原則名（面談5ステップ、き・ぐ・た・か・そ観点名、評価者エラー類型名等）のみ記載する。採点方針や内部指示文は入れない。該当なしは空文字
- （途中打ち切り）ラベル付き発話は途中で遮られた発話であり、内容の一部は相手に聞こえていない。この発話の内容を高評価の根拠として引用してはならない。遮り（バージイン）の発生自体は面談進行の文脈として考慮してよい
- JSONのみ返す

【出力スキーマ】
{schema}
"""


def format_transcript_for_judge(transcript: list[dict[str, Any]]) -> str:
    """Format transcript lines; label interrupted turns for the judge prompt."""
    lines: list[str] = []
    for turn in transcript:
        speaker = turn.get("speaker", "")
        text = turn.get("text", "")
        if turn.get("interrupted") is True:
            lines.append(f"[{speaker}] (途中打ち切り) {text}")
        else:
            lines.append(f"[{speaker}] {text}")
    return "\n".join(lines)


class JudgeService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider = get_provider_for_role(settings, "judge", allow_mock=True)

    def evaluate(
        self,
        config: SessionConfig,
        persona: dict[str, Any],
        transcript: list[dict[str, Any]],
        user_profile: dict[str, Any],
    ) -> dict[str, Any]:
        system = build_judge_system_prompt(config, persona)
        transcript_text = format_transcript_for_judge(transcript)
        user_content = (
            f"利用者プロフィール: {json.dumps(user_profile, ensure_ascii=False)}\n"
            f"ペルソナ(採点参考・hidden_facts除く): "
            f"{json.dumps({k: v for k, v in persona.items() if k != 'hidden_facts'}, ensure_ascii=False)}\n"
            f"トランスクリプト:\n{transcript_text}"
        )
        score_min, score_max = score_bounds_from_rating_scale(
            load_rubrics()["rating_scale"]
        )
        raw = self._provider.generate(
            system,
            [Message(role="user", content=user_content)],
            json_mode=True,
        )
        try:
            parsed: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            retry = self._provider.generate(
                system + "\n前回は不正なJSONでした。有効なJSONのみ返してください。",
                [Message(role="user", content=user_content)],
                json_mode=True,
            )
            parsed = json.loads(retry)
        return normalize_judge_scores(parsed, score_min, score_max)
