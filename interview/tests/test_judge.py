"""Tests for judge service schema."""

from app.judge import (
    JUDGE_JSON_SCHEMA,
    build_judge_json_schema,
    build_judge_system_prompt,
    format_transcript_for_judge,
    normalize_judge_scores,
    score_bounds_from_rating_scale,
)
from app.models import SessionConfig
from app.rubrics_util import load_rubrics




def test_feedback_flow_uses_acknowledgment_not_negligence():

    assert "acknowledgment" in JUDGE_JSON_SCHEMA

    assert "negligence" not in JUDGE_JSON_SCHEMA


def test_score_bounds_from_rating_scale_are_3_to_7():
    score_min, score_max = score_bounds_from_rating_scale(load_rubrics()["rating_scale"])
    assert score_min == 3
    assert score_max == 7


def test_build_judge_json_schema_uses_dynamic_bounds():
    schema = build_judge_json_schema(3, 7)
    assert "3-7の整数" in schema
    assert "1-7の整数" not in schema
    assert "acknowledgment" in schema


def test_normalize_judge_scores_clamps_rounds_and_drops_invalid():
    result = {
        "scores": {
            "低側": 1,
            "低側2": 2,
            "下限": 3,
            "上限": 7,
            "高側": 8,
            "端数": 5.6,
            "欠損": None,
            "文字列": "不可",
        },
        "avg_score": 99,
    }
    out = normalize_judge_scores(result, 3, 7)
    assert out["scores"] == {
        "低側": 3,
        "低側2": 3,
        "下限": 3,
        "上限": 7,
        "高側": 7,
        "端数": 6,
    }
    assert out["avg_score"] == (3 + 3 + 3 + 7 + 7 + 6) / 6
    corrections = {c["perspective"]: c for c in out["score_corrections"]}
    assert corrections["低側"] == {"perspective": "低側", "original": 1, "corrected": 3}
    assert corrections["低側2"] == {"perspective": "低側2", "original": 2, "corrected": 3}
    assert "下限" not in corrections
    assert "上限" not in corrections
    assert corrections["高側"] == {"perspective": "高側", "original": 8, "corrected": 7}
    assert corrections["端数"] == {"perspective": "端数", "original": 5.6, "corrected": 6}
    assert corrections["欠損"] == {"perspective": "欠損", "original": None, "corrected": None}
    assert corrections["文字列"] == {
        "perspective": "文字列",
        "original": "不可",
        "corrected": None,
    }


def test_normalize_judge_scores_omits_corrections_when_clean():
    result = {"scores": {"a": 4, "b": 5}, "avg_score": 0}
    out = normalize_judge_scores(result, 3, 7)
    assert out["scores"] == {"a": 4, "b": 5}
    assert out["avg_score"] == 4.5
    assert "score_corrections" not in out


def test_build_judge_system_prompt_embeds_3_to_7_scale():
    prompt = build_judge_system_prompt(
        SessionConfig("initial", "supervisor", "sme", "text"),
        {"role": "subordinate", "display_name": "テスト"},
    )
    assert "3-7の整数" in prompt
    assert "1-7の整数" not in prompt


def test_format_transcript_for_judge_labels_interrupted():
    text = format_transcript_for_judge(
        [
            {"speaker": "partner", "text": "通常応答"},
            {
                "speaker": "partner",
                "text": "途中までの内容です",
                "interrupted": True,
            },
            {"speaker": "user", "text": "割込発話"},
        ]
    )
    assert "[partner] 通常応答" in text
    assert "[partner] (途中打ち切り) 途中までの内容です" in text
    assert "[user] 割込発話" in text
    assert "(途中打ち切り)" not in text.splitlines()[0]


def test_build_judge_system_prompt_includes_interrupted_rule():
    prompt = build_judge_system_prompt(
        SessionConfig("initial", "supervisor", "sme", "text"),
        {"role": "subordinate", "display_name": "テスト"},
    )
    assert "途中打ち切り" in prompt
    assert "高評価の根拠として引用してはならない" in prompt
    assert "バージイン" in prompt
