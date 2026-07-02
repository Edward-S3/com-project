"""テキストボックス寸法からはみ出さないフォントサイズを逆算"""
from __future__ import annotations

import math

MIN_FONT_SIZE = 10
CHARS_PER_INCH_AT_14PT = 4.0


def calculate_font_size(
    text: str,
    box_width_inches: float,
    box_height_inches: float,
    base_size: int,
) -> int:
    size = base_size
    content = text or ""
    while size >= MIN_FONT_SIZE:
        chars_per_inch = CHARS_PER_INCH_AT_14PT * (14 / size)
        chars_per_line = max(int(box_width_inches * chars_per_inch), 1)
        lines = math.ceil(len(content) / chars_per_line) if content else 1
        height_needed = lines * (size * 1.4) / 72
        if height_needed <= box_height_inches:
            return size
        size -= 1
    return MIN_FONT_SIZE
