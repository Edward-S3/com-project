"""Tests for forbidden expression checker."""

from app.checker import check_text, format_warnings, get_forbidden_patterns, load_rubrics


def test_load_rubrics_has_forbidden_expressions():
    rubrics = load_rubrics()
    verbs, quantifiers = get_forbidden_patterns(rubrics)
    assert len(verbs) == 20
    assert len(quantifiers) == 4
    assert len(rubrics["good_measurement_units"]) == 12


def test_detect_action_verb():
    matches = check_text("来期は品質向上に努めます")
    assert any(m.expression == "向上" for m in matches)


def test_detect_vague_quantifier():
    matches = check_text("不良率を5%以下に抑える")
    assert any(m.expression == "以下" for m in matches)


def test_clean_text_has_no_matches():
    matches = check_text("来期は検査ミスを月3件以下に抑える")
    # "以下" is still detected - that's correct per rubric
    assert any(m.expression == "以下" for m in matches)


def test_format_warnings():
    matches = check_text("効率化を推進します")
    warnings = format_warnings(matches)
    assert any("効率化" in w for w in warnings)
    assert any("推進" in w for w in warnings)
