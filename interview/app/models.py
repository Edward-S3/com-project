"""Domain constants and mode helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Scene = Literal["initial", "final"]
UserRole = Literal["supervisor", "subordinate"]
Difficulty = Literal["sme", "enterprise", "consultant"]
IoMode = Literal["text", "voice"]
ModeCode = Literal["1A", "1B", "2A", "2B"]

DIFFICULTY_LABELS = {
    "sme": "中小企業",
    "enterprise": "大企業",
    "consultant": "コンサルタント",
}

MODE_LABELS = {
    "1A": "期初目標設定 × 上司役（AI=部下）",
    "1B": "期初目標設定 × 部下役（AI=上司）",
    "2A": "期末評価面談 × 上司役（AI=部下）",
    "2B": "期末評価面談 × 部下役（AI=上司）",
}

GRADE_ORDER = ["1-1", "1-2", "1-3", "2", "3", "4-1", "4-2", "5", "6", "7"]

EVALUATOR_BY_GRADE = {
    "1-1": "3",
    "1-2": "3",
    "1-3": "3",
    "2": "3",
    "3": "4-1",
    "4-1": "4-2",
    "4-2": "5",
    "5": "6",
    "6": "7",
    "7": "7",
}

INITIAL_GOAL_PERCENT = {
    ("modest", "sme"): 80,
    ("modest", "enterprise"): 70,
    ("modest", "consultant"): 60,
    ("overconfident", "sme"): 80,
    ("overconfident", "enterprise"): 70,
    ("overconfident", "consultant"): 60,
    ("vague", "sme"): 75,
    ("vague", "enterprise"): 65,
    ("vague", "consultant"): 55,
}

MAX_TURNS_DEFAULT = 30


@dataclass(frozen=True)
class SessionConfig:
    scene: Scene
    role: UserRole
    difficulty: Difficulty
    io_mode: IoMode

    @property
    def mode_code(self) -> ModeCode:
        if self.scene == "initial" and self.role == "supervisor":
            return "1A"
        if self.scene == "initial" and self.role == "subordinate":
            return "1B"
        if self.scene == "final" and self.role == "supervisor":
            return "2A"
        return "2B"

    @property
    def ai_role(self) -> Literal["supervisor", "subordinate"]:
        return "subordinate" if self.role == "supervisor" else "supervisor"


def grade_index(grade_id: str) -> int:
    return GRADE_ORDER.index(grade_id)


def subordinate_grade(user_grade: str, offset: int = 1) -> str:
    idx = max(0, grade_index(user_grade) - offset)
    return GRADE_ORDER[idx]


def parse_mode_code(mode_code: str) -> SessionConfig:
    mapping = {
        "1A": SessionConfig("initial", "supervisor", "sme", "text"),
        "1B": SessionConfig("initial", "subordinate", "sme", "text"),
        "2A": SessionConfig("final", "supervisor", "sme", "text"),
        "2B": SessionConfig("final", "subordinate", "sme", "text"),
    }
    if mode_code not in mapping:
        raise ValueError(f"Unknown mode: {mode_code}")
    return mapping[mode_code]
