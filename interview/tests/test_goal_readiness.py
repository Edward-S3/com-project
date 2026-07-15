"""Tests for 1B goal approval readiness gate."""

from app.goal_readiness import (
    assess_goal_readiness,
    contains_approval_declaration,
)


def test_improvement_goal_without_deadline_not_ready():
    texts = [
        "修正します。QC検定3級社内認定を上期までに取得。"
        "改善提案10件(3=2件以下,5=10件,7=15件)"
    ]
    readiness = assess_goal_readiness(texts)
    assert not readiness.ready_for_approval
    assert "き(期限:改善提案)" in readiness.missing


def test_improvement_goal_with_deadline_ready():
    texts = [
        "修正します。QC検定3級社内認定を上期までに取得。"
        "改善提案10件(3=2件以下,5=10件,7=15件)",
        "改善提案10件は今期中に達成します。",
    ]
    readiness = assess_goal_readiness(texts)
    assert readiness.ready_for_approval


def test_approval_keyword_detection():
    assert contains_approval_declaration("この内容で進めましょう。承認します。")
    assert not contains_approval_declaration("期限を教えてください。")
