"""Tests for rubrics utilities."""

from app.rubrics_util import get_judge_perspectives, strip_hidden_facts


def test_strip_hidden_facts():
    persona = {"display_name": "部下", "hidden_facts": ["秘密"], "grade": "3"}
    safe = strip_hidden_facts(persona)
    assert "hidden_facts" not in safe
    assert safe["grade"] == "3"


def test_judge_perspectives_supervisor():
    perspectives = get_judge_perspectives("supervisor")
    assert len(perspectives) == 7
    assert "傾聴・質問" in perspectives
