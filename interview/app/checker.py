"""Forbidden expression checker backed by rubrics.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import DATA_DIR

if TYPE_CHECKING:
    from app.models import SessionConfig

RUBRICS_PATH = DATA_DIR / "rubrics.json"
EVAL_CRITERIA_RE = re.compile(r"\([^)]*=\d+[^)]*\)")
CLOSING_ABOVE_RE = re.compile(r"以上(です|で|の|、)")
GOAL_CHECK_MODES = frozenset({"1A", "1B"})


@dataclass(frozen=True)
class ForbiddenMatch:
    expression: str
    category: str
    position: int


def load_rubrics(path: Path | None = None) -> dict:
    rubrics_path = path or RUBRICS_PATH
    with rubrics_path.open(encoding="utf-8") as fp:
        return json.load(fp)


def get_forbidden_patterns(rubrics: dict | None = None) -> tuple[list[str], list[str]]:
    data = rubrics or load_rubrics()
    forbidden = data["forbidden_expressions"]
    return list(forbidden["action_verbs"]), list(forbidden["vague_quantifiers"])


def _prepare_text_for_check(text: str) -> str:
    """Remove segments that commonly trigger false positives in goal context."""
    cleaned = EVAL_CRITERIA_RE.sub("", text)
    cleaned = CLOSING_ABOVE_RE.sub("", cleaned)
    return cleaned


def should_check_forbidden(config: SessionConfig | None) -> bool:
    if config is None:
        return True
    return config.mode_code in GOAL_CHECK_MODES


def check_text(text: str, config: SessionConfig | None = None) -> list[ForbiddenMatch]:
    """Detect forbidden expressions in user goal-declaration text."""
    if not should_check_forbidden(config):
        return []
    return _check_prepared_text(_prepare_text_for_check(text))


def _check_prepared_text(text: str, rubrics: dict | None = None) -> list[ForbiddenMatch]:
    """Detect forbidden expressions in user text."""
    action_verbs, vague_quantifiers = get_forbidden_patterns(rubrics)
    matches: list[ForbiddenMatch] = []

    for expression in action_verbs:
        for match in re.finditer(re.escape(expression), text):
            matches.append(
                ForbiddenMatch(
                    expression=expression,
                    category="action_verb",
                    position=match.start(),
                )
            )

    for expression in vague_quantifiers:
        for match in re.finditer(re.escape(expression), text):
            matches.append(
                ForbiddenMatch(
                    expression=expression,
                    category="vague_quantifier",
                    position=match.start(),
                )
            )

    matches.sort(key=lambda item: item.position)
    return matches


def format_warnings(matches: list[ForbiddenMatch]) -> list[str]:
    """Return human-readable advisory warning messages."""
    warnings: list[str] = []
    for match in matches:
        if match.category == "action_verb":
            warnings.append(f"参考警告（行動目標）: 「{match.expression}」")
        else:
            warnings.append(f"参考警告（不明確基準）: 「{match.expression}」")
    return warnings
