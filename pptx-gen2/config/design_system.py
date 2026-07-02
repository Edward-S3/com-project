"""デザインシステム定数・モデル名・並列処理上限など。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKGROUND_FRAME_PATH = ROOT_DIR / "assets" / "slide_background_frame.png"
DEFAULT_PALETTE_NAME = "Urban Skyline"
BACKGROUND_WHITE = "FFFFFF"

# 背景フレーム: スライド端からの余白 (mm)
FRAME_MARGIN_MM = 5
FRAME_MARGIN_IN = FRAME_MARGIN_MM / 25.4

# Webサイト並列取得の同時実行数上限
WEB_FETCH_MAX_WORKERS = 4

# スライド寸法 (16:9)
SLIDE_WIDTH_IN = 13.333
SLIDE_HEIGHT_IN = 7.5
MIN_MARGIN_IN = 0.5
BLOCK_GAP_IN = 0.4

SAFE_FONTS = [
    "Arial",
    "Calibri",
    "Cambria",
    "Times New Roman",
    "Courier New",
    "Bookman Old Style",
    "Century Schoolbook",
]
DEFAULT_FONT = "Calibri"

TYPOGRAPHY = {
    "slide_title": {"size_pt": 40, "bold": True},
    "section_heading": {"size_pt": 22, "bold": True},
    "body": {"size_pt": 15, "bold": False},
    "caption": {"size_pt": 11, "bold": False},
}

# モデル名 (API利用可能名・デフォルト)
MODEL_NAMES = {
    "gemini": "gemini-3.5-flash",
    "gemini_pro": "gemini-3.1-pro-preview",
    "claude": "claude-sonnet-4-20250514",
    "gpt4o": "gpt-4o",
    "grok": "grok-2-latest",
}

# 爽やかで都会的な配色
# primary=本文・見出し用の濃色 / secondary=図形塗りのみ(文字色禁止) / accent=強調(文字は自動で濃度調整)
PALETTES: Dict[str, Dict[str, str]] = {
    "Urban Skyline": {"primary": "1D3557", "secondary": "E8EEF2", "accent": "0077B6", "text_muted": "475569"},
    "Metro Blue": {"primary": "2B2D42", "secondary": "EDF2F4", "accent": "2563EB", "text_muted": "4B5563"},
    "Sky Fresh": {"primary": "0C4A6E", "secondary": "E0F2FE", "accent": "0369A1", "text_muted": "475569"},
    "City Dawn": {"primary": "1E3A5F", "secondary": "F0F4F8", "accent": "0F766E", "text_muted": "52606D"},
    "Slate Modern": {"primary": "1E293B", "secondary": "F1F5F9", "accent": "0369A1", "text_muted": "64748B"},
    "Harbor Light": {"primary": "023047", "secondary": "E9F5F8", "accent": "0077B6", "text_muted": "4A5568"},
}

# 白背景上の最低コントラスト比 (WCAG AA 目安)
MIN_CONTRAST_BODY = 4.5
MIN_CONTRAST_LARGE = 3.0
FALLBACK_TEXT = "1E293B"

STRUCTURE_URBAN_LIGHT = "urban_light"


def _hex_to_rgb_tuple(hex_color: str) -> Tuple[int, int, int]:
    h = str(hex_color).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(hex_color: str) -> float:
    r, g, b = _hex_to_rgb_tuple(hex_color)

    def _lin(channel: int) -> float:
        c = channel / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = _relative_luminance(fg_hex)
    l2 = _relative_luminance(bg_hex)
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)


def ensure_readable_on_white(hex_color: str, min_ratio: float = MIN_CONTRAST_BODY) -> str:
    """白背景で読めるまで色を濃くする。"""
    color = str(hex_color).lstrip("#").upper()
    if contrast_ratio(color, BACKGROUND_WHITE) >= min_ratio:
        return color
    r, g, b = _hex_to_rgb_tuple(color)
    for _ in range(24):
        r = max(0, int(r * 0.88))
        g = max(0, int(g * 0.88))
        b = max(0, int(b * 0.88))
        candidate = f"{r:02X}{g:02X}{b:02X}"
        if contrast_ratio(candidate, BACKGROUND_WHITE) >= min_ratio:
            return candidate
    return FALLBACK_TEXT


@dataclass
class DesignContext:
    palette_name: str
    primary: str
    secondary: str
    accent: str
    text_muted: str = ""
    structure: str = STRUCTURE_URBAN_LIGHT
    font: str = DEFAULT_FONT
    _text_primary: str = ""
    _text_accent: str = ""

    def __post_init__(self) -> None:
        self._text_primary = ensure_readable_on_white(self.primary, MIN_CONTRAST_BODY)
        muted_src = self.text_muted or self.primary
        self.text_muted = ensure_readable_on_white(muted_src, MIN_CONTRAST_BODY)
        self._text_accent = ensure_readable_on_white(self.accent, MIN_CONTRAST_BODY)

    def title_bg(self) -> str:
        """全スライド背景は白で統一。"""
        return BACKGROUND_WHITE

    def title_fg(self) -> str:
        return self._text_primary

    def body_bg(self) -> str:
        """全スライド背景は白で統一。"""
        return BACKGROUND_WHITE

    def body_fg(self) -> str:
        return self._text_primary

    def subtitle_fg(self) -> str:
        """サブタイトル・補足文（やや弱いが可読）。"""
        return self.text_muted

    def accent_text(self) -> str:
        """強調テキスト用（淡色 accent を自動補正）。"""
        return self._text_accent

    def surface_fill(self) -> str:
        """図形・カードの塗りつぶし専用。文字色に使わない。"""
        return self.secondary


def palette_from_name(name: str) -> DesignContext:
    p = PALETTES.get(name, PALETTES[DEFAULT_PALETTE_NAME])
    return DesignContext(
        palette_name=name,
        primary=p["primary"],
        secondary=p["secondary"],
        accent=p["accent"],
        text_muted=p.get("text_muted", ""),
    )


def palette_choices() -> List[str]:
    return list(PALETTES.keys())


def hex_to_rgb(hex_color: str):
    """HEX文字列を python-pptx RGBColor に変換。"""
    from pptx.dml.color import RGBColor

    h = str(hex_color).lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class DesignBridge:
    """LLM生成コード向け design ラッパー。"""

    def __init__(self, ctx: DesignContext) -> None:
        from pptx.util import Inches

        self._ctx = ctx
        self.primary = hex_to_rgb(ctx._text_primary)
        self.secondary = hex_to_rgb(ctx.surface_fill())
        self.accent = hex_to_rgb(ctx._text_accent)
        self.text_muted = hex_to_rgb(ctx.subtitle_fg())
        self.surface = hex_to_rgb(ctx.surface_fill())
        self.SLIDE_WIDTH = Inches(SLIDE_WIDTH_IN)
        self.SLIDE_HEIGHT = Inches(SLIDE_HEIGHT_IN)

    def body_bg(self):
        return hex_to_rgb(self._ctx.body_bg())

    def body_fg(self):
        return hex_to_rgb(self._ctx.body_fg())

    def title_bg(self):
        return hex_to_rgb(self._ctx.title_bg())

    def title_fg(self):
        return hex_to_rgb(self._ctx.title_fg())

    def subtitle_fg(self):
        return hex_to_rgb(self._ctx.subtitle_fg())

    def accent_text(self):
        return hex_to_rgb(self._ctx.accent_text())


def make_design_bridge(ctx: DesignContext) -> DesignBridge:
    return DesignBridge(ctx)
