"""V5: Anthropic 構造化出力用 Pydantic スキーマ"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class SlideTheme(BaseModel):
    palette_name: str
    dominant_color: str
    support_color: str
    accent_color: str
    alert_color: str = Field(default="#D32F2F", description="警告色（HEX形式, 例: #D32F2F）")
    is_dark_mode_cover: bool = True


class ChapterOutline(BaseModel):
    chapter_number: int
    chapter_title: str
    estimated_slides: int = Field(description="この章に必要なスライド数（情報量に応じて3〜6枚）")
    source_context_focus: str = Field(description="この章でソースのどの部分にフォーカスするか")


class PresentationOutline(BaseModel):
    presentation_title: str
    theme: SlideTheme
    chapters: List[ChapterOutline]


class ChapterSlidesResult(BaseModel):
    """章ごとのスライド生成 API 応答"""

    slides: List[SlideContent]


class GridCell(BaseModel):
    """GRIDレイアウトの各セル。header + body が必須"""

    header: str = Field(description="セルの見出し（太字・16pt）")
    body: List[str] = Field(
        description="説明文リスト（2〜3行必須、空リスト禁止）",
        min_length=1,
    )
    color_variant: Literal["dominant", "support", "accent", "light"] = "support"


class IconItem(BaseModel):
    icon: str = Field(description="Unicode絵文字または記号（例: '📊', '01'）")
    header: str = Field(description="見出し")
    body: str = Field(description="説明文")


class VisualElement(BaseModel):
    kind: Literal[
        "BIG_STAT",
        "STEP_FLOW",
        "MATURITY_BAR",
        "ICON_LIST",
        "COMPARISON",
    ]
    stat_value: Optional[str] = None
    stat_label: Optional[str] = None
    items: Optional[List[str]] = None
    item_descriptions: Optional[List[str]] = None
    stages: Optional[List[str]] = None
    stage_descriptions: Optional[List[str]] = None
    before_label: Optional[str] = None
    before_body: Optional[str] = None
    after_label: Optional[str] = None
    after_body: Optional[str] = None
    icon_items: Optional[List[IconItem]] = Field(
        default=None,
        description="ICON_LIST 用: icon, header, body のリスト",
    )


class SlideContent(BaseModel):
    slide_number: int
    key_message: str = Field(description="このスライドで伝える最重要メッセージ1文（20文字以内）")
    layout_type: Literal[
        "TITLE",
        "CHAPTER_TITLE",
        "TWO_COLUMN_TEXT_AND_BLOCK",
        "GRID_1X3",
        "GRID_2X2",
        "FULL_STEP_FLOW",
        "MATURITY_BAR_FULL",
    ]
    title: str
    subtitle: Optional[str] = None
    bullet_points: List[str] = Field(default_factory=list, description="TWO_COLUMNの左カラム用。3〜4行")
    grid_cells: Optional[List[GridCell]] = Field(default=None, description="GRID系必須")
    visual: Optional[VisualElement] = Field(default=None, description="TITLE/CHAPTER_TITLE以外のTWO_COLUMNで必須")

    @model_validator(mode="after")
    def check_grid_cells(self) -> "SlideContent":
        if self.layout_type == "GRID_1X3":
            if self.grid_cells is None or len(self.grid_cells) != 3:
                raise ValueError("GRID_1X3 は grid_cells が正確に3件必要")
            for cell in self.grid_cells:
                if not cell.body:
                    raise ValueError("GridCell.body は空リスト禁止")
        if self.layout_type == "GRID_2X2":
            if self.grid_cells is None or len(self.grid_cells) != 4:
                raise ValueError("GRID_2X2 は grid_cells が正確に4件必要")
            for cell in self.grid_cells:
                if not cell.body:
                    raise ValueError("GridCell.body は空リスト禁止")
        return self


class PresentationData(BaseModel):
    theme: SlideTheme
    slides: List[SlideContent]

    @property
    def title(self) -> str:
        if self.slides:
            return self.slides[0].title
        return "presentation"
