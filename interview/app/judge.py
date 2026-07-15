"""Judge AI service for post-interview reports."""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.models import DIFFICULTY_LABELS, SessionConfig
from app.providers.base import Message
from app.providers.registry import get_provider_for_role
from app.rubrics_util import get_judge_perspectives, load_rubrics

JUDGE_JSON_SCHEMA = """
{
  "scores": {"観点名": 1-7の整数, ...},
  "good_points": [{"text": "良い点の説明", "quote": "発言引用"}],
  "improvements": [{"text": "改善点", "quote": "発言引用", "principle": "マニュアル原則"}],
  "overall_evaluation": "全体的な評価テキスト",
  "overall_grade": "S|A|B|C|D",
  "summary": "総評（次回アドバイス含む）",
  "model_answer": "模範解答例1件",
  "avg_score": 数値,
  "goal_level_percent": 数値またはnull,
  "goal_level_reached": true/false/null,
  "feedback_flow_observed": {"acknowledgment": bool, "deep_dive": bool, "expectation": bool} | null  // 2Aのみ。1B/2Bはnull
}
"""


def build_judge_system_prompt(config: SessionConfig, persona: dict[str, Any]) -> str:
    rubrics = load_rubrics()
    perspectives = get_judge_perspectives(config.role)
    rating = rubrics["rating_scale"]
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
- JSONのみ返す

【出力スキーマ】
{JUDGE_JSON_SCHEMA}
"""


class JudgeService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider = get_provider_for_role(settings, "judge", allow_mock=True)

    def evaluate(
        self,
        config: SessionConfig,
        persona: dict[str, Any],
        transcript: list[dict[str, str]],
        user_profile: dict[str, Any],
    ) -> dict[str, Any]:
        system = build_judge_system_prompt(config, persona)
        transcript_text = "\n".join(
            f"[{t['speaker']}] {t['text']}" for t in transcript
        )
        user_content = (
            f"利用者プロフィール: {json.dumps(user_profile, ensure_ascii=False)}\n"
            f"ペルソナ(採点参考・hidden_facts除く): "
            f"{json.dumps({k: v for k, v in persona.items() if k != 'hidden_facts'}, ensure_ascii=False)}\n"
            f"トランスクリプト:\n{transcript_text}"
        )
        raw = self._provider.generate(
            system,
            [Message(role="user", content=user_content)],
            json_mode=True,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            retry = self._provider.generate(
                system + "\n前回は不正なJSONでした。有効なJSONのみ返してください。",
                [Message(role="user", content=user_content)],
                json_mode=True,
            )
            return json.loads(retry)
