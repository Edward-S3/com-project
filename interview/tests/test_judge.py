"""Tests for judge service schema."""



from app.judge import JUDGE_JSON_SCHEMA





def test_feedback_flow_uses_acknowledgment_not_negligence():

    assert "acknowledgment" in JUDGE_JSON_SCHEMA

    assert "negligence" not in JUDGE_JSON_SCHEMA


