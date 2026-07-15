"""Tests for persona generation."""

from app.models import SessionConfig
from app.persona import build_persona
from app.config import load_settings


def test_persona_1a_modest_goal_percent():
    settings = load_settings()
    config = SessionConfig("initial", "supervisor", "sme", "text")
    profile = {"name": "山田", "department": "製造", "age": 40, "tenure_years": 15, "grade": "4-1"}
    persona = build_persona(settings, profile, config, trait="modest", use_llm=False)
    assert persona["role"] == "subordinate"
    assert persona["initial_goal_percent"] == 80
    assert "hidden_facts" in persona


def test_persona_2a_gap_b():
    settings = load_settings()
    config = SessionConfig("final", "supervisor", "sme", "text")
    profile = {"name": "佐藤", "department": "品質", "age": 38, "tenure_years": 12, "grade": "4-1"}
    persona = build_persona(settings, profile, config, gap_pattern="b", use_llm=False)
    assert persona["gap_pattern"] == "b"
    assert persona["self_rating_claim"] == "A"


def test_hidden_facts_present():
    settings = load_settings()
    config = SessionConfig("initial", "subordinate", "enterprise", "text")
    profile = {"name": "鈴木", "department": "総務", "age": 28, "tenure_years": 5, "grade": "3"}
    persona = build_persona(settings, profile, config, use_llm=False)
    assert persona["role"] == "supervisor"
    assert len(persona["hidden_facts"]) >= 1
