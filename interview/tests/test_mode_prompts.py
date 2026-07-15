"""Tests for mode prompt skeletons."""

import pytest

from app.mode_prompts import VALID_MODES, get_mode_description, get_system_prompt


def test_valid_modes():
    assert VALID_MODES == {"1A", "1B"}


def test_get_system_prompt_1a():
    prompt = get_system_prompt("1A")
    assert "部下役" in prompt
    assert "上司" in prompt


def test_get_system_prompt_1b():
    prompt = get_system_prompt("1B")
    assert "上司役" in prompt
    assert "き・ぐ・た・か・そ" in prompt


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        get_system_prompt("2A")


def test_mode_description():
    assert "期初" in get_mode_description("1A")
