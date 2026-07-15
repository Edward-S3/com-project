"""Goal declaration readiness checks for 1B supervisor approval gate."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEADLINE_RE = re.compile(
    r"(までに|期に|上期中|下期中|今期中|来期中|当月|月末|年度末|"
    r"\d{1,2}月|\d{1,2}日|第[一二三四1-4]四半期|Q[1-4])"
)
MEASURABLE_RE = re.compile(r"\d+[%％件回個人点]?")
SPECIFIC_RE = re.compile(r"(認定|検定|取得|削減|改善提案|歩留|工程|件)")
APPROVAL_KEYWORDS = ("承認します", "承認済み", "承認した", "この内容で進め")

GOAL_CLAUSE_RE = re.compile(
    r"(改善提案[^。.\n]*|QC[^。.\n]*|検定[^。.\n]*|認定[^。.\n]*|"
    r"不良[^。.\n]*|目標[^。.\n]*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GoalReadiness:
    ki: bool
    gu: bool
    ta: bool
    ka: bool
    so: bool
    missing: tuple[str, ...]

    @property
    def ready_for_approval(self) -> bool:
        return not self.missing


def _clause_has_deadline(clause: str) -> bool:
    return bool(DEADLINE_RE.search(clause))


def assess_goal_readiness(user_texts: list[str]) -> GoalReadiness:
    """Assess whether user goal declarations satisfy き・ぐ・た・か・そ for approval."""
    combined = "\n".join(user_texts)
    if not combined.strip():
        return GoalReadiness(False, False, False, False, False, ("申告内容なし",))

    ki = bool(DEADLINE_RE.search(combined))
    gu = bool(SPECIFIC_RE.search(combined))
    so = bool(MEASURABLE_RE.search(combined))
    ta = True  # 達成可能性は対話で判断。コード側は未検証とする。
    ka = True  # 関連性も対話で判断。コード側は未検証とする。

    missing: list[str] = []
    if not ki:
        missing.append("き(期限)")

    # Per-goal deadline: 改善提案 with count but no deadline in its clause
    for clause in GOAL_CLAUSE_RE.findall(combined):
        if "改善提案" in clause and re.search(r"\d+件", clause):
            if not _clause_has_deadline(clause) and not _has_nearby_deadline(combined, clause):
                if "き(期限:改善提案)" not in missing:
                    missing.append("き(期限:改善提案)")
                ki = False

    if not gu:
        missing.append("ぐ(具体)")
    if not so:
        missing.append("そ(測定)")

    return GoalReadiness(ki=ki, gu=gu, ta=ta, ka=ka, so=so, missing=tuple(missing))


def _has_nearby_deadline(full_text: str, clause: str) -> bool:
    idx = full_text.find(clause)
    if idx < 0:
        return False
    window = full_text[idx : idx + len(clause) + 80]
    return _clause_has_deadline(window)


def contains_approval_declaration(text: str) -> bool:
    return any(keyword in text for keyword in APPROVAL_KEYWORDS)
