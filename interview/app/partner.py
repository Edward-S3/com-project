"""Partner AI dialogue service."""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.models import MODE_LABELS, SessionConfig
from app.providers.base import LLMProvider, Message
from app.providers.registry import get_provider_for_role
from app.rubrics_util import build_rubric_prompt_excerpt, load_rubrics


def build_partner_system_prompt(
    config: SessionConfig,
    persona: dict[str, Any],
    user_profile: dict[str, Any],
) -> str:
    rubrics = load_rubrics()
    rubric_excerpt = build_rubric_prompt_excerpt()
    hidden_facts = persona.get("hidden_facts", [])
    hidden_block = "\n".join(f"- {f}" for f in hidden_facts)

    tone = ""
    if persona.get("role") == "supervisor":
        tone = (
            "口調: 丁寧だが敬語に拘らない砕けた話し方。威圧的にはしない。"
            "「だな」「じゃないか」「〜かな」等の語尾を使う。"
        )
    else:
        tone = (
            "口調: 敬語を意識しているが少しダルさ（気だるさ）のある男性社員。"
            "「です」「ます」は使うが覇気がなく、語尾に「…」「かな」等がある。"
            "一人称は「僕」または「自分」。"
        )

    mode_instructions = _mode_instructions(config, persona)
    interview_steps = json.dumps(rubrics["interview_five_steps"], ensure_ascii=False)

    return f"""あなたは1on1面談ロールプレイの相手役AIです。メタ発言・AIである旨の発言は禁止。
モード: {config.mode_code} ({MODE_LABELS[config.mode_code]})
難易度: {persona.get('difficulty_label', config.difficulty)}
あなたの役割: {'上司' if persona.get('role') == 'supervisor' else '部下'}
{display_name_line(persona)}
{tone}

【ペルソナ】
{json.dumps({k: v for k, v in persona.items() if k != 'hidden_facts'}, ensure_ascii=False, indent=2)}

【hidden_facts（利用者に開示しない。傾聴・具体質問で段階的に開示）】
{hidden_block}

【面談5ステップ参考】
{interview_steps}

{mode_instructions}

【マニュアル抜粋】
{rubric_excerpt}

【演技ルール】
- 1発話2〜5文
- 傾聴・具体質問があれば協力的に修正・開示する
- 高圧的なら萎縮または反発
- 二者択一の質問には短答のみ
- 面談のゴール達成時は1B/2Bで上司役なら終了宣言可
"""


def display_name_line(persona: dict[str, Any]) -> str:
    return f"表示名: {persona.get('display_name', '相手役')}"


def _mode_instructions(config: SessionConfig, persona: dict[str, Any]) -> str:
    if config.mode_code == "1A":
        pct = persona.get("initial_goal_percent", 80)
        return f"""【1A: 期初×上司役（あなた=部下）】
- 最初は最適水準100に対し約{pct}%の控えめ・曖昧な目標を申告する（禁止表現を混ぜてもよい）
- 上司の具体化・ストレッチ誘導が適切なら、current_goal_percentを85〜95に近づけて修正申告する
- current_goal_percentは内部パラメータとして意識し、85以上になったら合意可能な具体目標を述べる"""

    if config.mode_code == "1B":
        return """【1B: 期初×部下役（あなた=上司）】
- 部下の目標申告を聞き、禁止表現・き・ぐ・た・か・そ不足があれば差し戻す
- 承認前チェックリスト（すべてYESでなければ承認禁止）:
  ・き: 各目標項目に期限（いつまで）が明示されているか
  ・ぐ: 抽象的な行動目標ではなく具体的な内容か
  ・た: 達成可能な水準か（対話で確認）
  ・か: 部門方針・職務と関連があるか
  ・そ: 数値・件数・状態で測定可能か
- 一項目でも期限不明なら「承認」せず、期限の追及のみ行う
- 「承認します」「承認済み」「この内容で進め」は全項目充足確認後のみ使用可
- 変更要求時は理由と変更後内容のセットを求める
- 例示する目標文に禁止表現（向上・図る・最大・最小・以上・以下等）は使わない"""

    if config.mode_code == "2A":
        gap = persona.get("gap_pattern", "b")
        return f"""【2A: 期末×上司役（あなた=部下）】
- ギャップパターン: {GAP_LABELS.get(gap, gap)}
- パターンbの場合: 実績はC相当なのに自己評価はAと主張する
- hidden_factsの事実は具体的質問がないと開示しない
- 上司のねぎらい・根拠深掘り・期待水準提示に応じて態度が変化する"""

    return """【2B: 期末×部下役（あなた=上司）】
- 部下の自己評価根拠を数値・事実で執拗に確認する
- コンサル難易度: 評価点7＝真の目的の水準まで踏み込む
- 齟齬があれば事実ベースで検証する"""


GAP_LABELS = {
    "a": "部下マイナス／上司プラス",
    "b": "部下プラス／上司マイナス",
    "c": "双方マイナス",
    "d": "双方プラス",
}


class PartnerService:
    def __init__(self, settings: Settings, provider: LLMProvider | None = None) -> None:
        self._settings = settings
        self._provider = provider or get_provider_for_role(settings, "partner", allow_mock=True)

    def generate_opening(
        self,
        config: SessionConfig,
        persona: dict[str, Any],
        user_profile: dict[str, Any],
    ) -> str:
        system = build_partner_system_prompt(config, persona, user_profile)
        if config.mode_code == "1A":
            user_msg = "面談を開始します。来期の目標について聞かせてください。"
        elif config.mode_code == "1B":
            user_msg = "面談を開始します。目標の申告をお願いします。"
        elif config.mode_code == "2A":
            user_msg = "面談を開始します。自己評価と実績について聞かせてください。"
        else:
            user_msg = "面談を開始します。自己評価の根拠を説明してください。"
        return self._provider.generate(system, [Message(role="user", content=user_msg)])

    def reply(
        self,
        config: SessionConfig,
        persona: dict[str, Any],
        user_profile: dict[str, Any],
        history: list[dict[str, str]],
        user_message: str,
        *,
        correction_hint: str | None = None,
    ) -> str:
        system = build_partner_system_prompt(config, persona, user_profile)
        if correction_hint:
            system += f"\n\n【重要な補正指示】\n{correction_hint}"
        messages: list[Message] = []
        for turn in history:
            role = "user" if turn["speaker"] == "user" else "assistant"
            messages.append(Message(role=role, content=turn["text"]))
        messages.append(Message(role="user", content=user_message))
        return self._provider.generate(system, messages)

    def should_end_as_supervisor(self, text: str) -> bool:
        keywords = ["面談を終了", "これで終わり", "ゴールに達した", "以上で終了", "お疲れさまでした"]
        return any(k in text for k in keywords)
