"""V5: 生成 JSON の品質検証"""
from __future__ import annotations

from src.config import MIN_SLIDE_COUNT
from src.schemas import ChapterOutline, GridCell, PresentationData, PresentationOutline, VisualElement

MAX_VALIDATION_RETRIES = 2
LAYOUT_ROTATION_EXEMPT = frozenset({"TITLE", "CHAPTER_TITLE"})


class PresentationValidationError(Exception):
    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("\n".join(issues))


def _is_blank(value: str | None) -> bool:
    return not (value or "").strip()


def _is_placeholder(text: str) -> bool:
    t = text.strip()
    return t in {"説明", "詳細", "—", "-", "...", "TBD", "未定", "項目"}


def _validate_grid_cells(cells: list[GridCell], slide_num: int, expected: int) -> list[str]:
    issues: list[str] = []
    if len(cells) != expected:
        issues.append(f"Slide {slide_num}: grid_cells が {expected} 件ではありません")
        return issues
    for j, cell in enumerate(cells, 1):
        if _is_blank(cell.header):
            issues.append(f"Slide {slide_num} Cell {j}: header が空です")
        if not cell.body:
            issues.append(f"Slide {slide_num} Cell {j}: body(説明文) が空です")
        elif len(cell.body) < 2:
            issues.append(f"Slide {slide_num} Cell {j}: body は2行以上必要です")
        for k, line in enumerate(cell.body, 1):
            if _is_blank(line):
                issues.append(f"Slide {slide_num} Cell {j} 行{k}: 空文字です")
            elif len(line.strip()) < 12 or _is_placeholder(line):
                issues.append(
                    f"Slide {slide_num} Cell {j} 行{k}: 説明が短すぎます「{line[:20]}」（12文字以上の具体的文が必要）"
                )
    return issues


def _validate_visual(visual: VisualElement, slide_num: int) -> list[str]:
    errors: list[str] = []
    prefix = f"Slide {slide_num}: visual.kind={visual.kind}"

    if visual.kind == "BIG_STAT":
        if _is_blank(visual.stat_value):
            errors.append(f"{prefix} — stat_value 未設定")
        if _is_blank(visual.stat_label):
            errors.append(f"{prefix} — stat_label 未設定")

    elif visual.kind == "STEP_FLOW":
        items = visual.items or []
        descs = visual.item_descriptions or []
        if len(items) < 3:
            errors.append(f"{prefix} — items は3件以上必要")
        if len(descs) < len(items):
            errors.append(f"{prefix} — item_descriptions が items より少ない")
        for d in descs:
            if _is_blank(d) or len(d.strip()) < 10:
                errors.append(f"{prefix} — item_descriptions に短い説明があります")

    elif visual.kind == "MATURITY_BAR":
        stages = visual.stages or []
        descs = visual.stage_descriptions or []
        if len(stages) < 3:
            errors.append(f"{prefix} — stages は3件以上必要")
        if len(descs) < len(stages):
            errors.append(f"{prefix} — stage_descriptions が stages より少ない")

    elif visual.kind == "ICON_LIST":
        icons = visual.icon_items or []
        if len(icons) < 3:
            errors.append(f"{prefix} — icon_items は3件以上必要")
        for item in icons:
            if _is_blank(item.header) or _is_blank(item.body):
                errors.append(f"{prefix} — icon_items に空の header/body があります")

    elif visual.kind == "COMPARISON":
        for field in ("before_label", "before_body", "after_label", "after_body"):
            if _is_blank(getattr(visual, field)):
                errors.append(f"{prefix} — {field} 未設定")

    return errors


def validate_outline(outline: PresentationOutline, min_slides: int = MIN_SLIDE_COUNT) -> list[str]:
    errors: list[str] = []
    errors.extend(_validate_theme(outline.theme))
    if not outline.presentation_title.strip():
        errors.append("presentation_title が空です")
    if len(outline.chapters) < 2:
        errors.append("chapters は2章以上必要です")
    total_estimated = sum(c.estimated_slides for c in outline.chapters) + 2  # 表紙 + まとめ
    if total_estimated < min_slides:
        errors.append(
            f"推定スライド数が不足（{total_estimated} 枚）。最低 {min_slides} 枚になるよう estimated_slides を増やしてください"
        )
    for ch in outline.chapters:
        if ch.estimated_slides < 3 or ch.estimated_slides > 6:
            errors.append(f"第{ch.chapter_number}章: estimated_slides は3〜6枚にしてください（現在 {ch.estimated_slides}）")
        if _is_blank(ch.chapter_title):
            errors.append(f"第{ch.chapter_number}章: chapter_title が空です")
        if _is_blank(ch.source_context_focus):
            errors.append(f"第{ch.chapter_number}章: source_context_focus が空です")
    return errors


def validate_chapter_slides(slides: list, chapter: ChapterOutline) -> list[str]:
    errors: list[str] = []
    if not slides:
        errors.append(f"第{chapter.chapter_number}章: slides が空です")
        return errors
    if len(slides) != chapter.estimated_slides:
        errors.append(
            f"第{chapter.chapter_number}章: slides は {chapter.estimated_slides} 枚必要（現在 {len(slides)} 枚）"
        )
    if slides[0].layout_type != "CHAPTER_TITLE":
        errors.append(f"第{chapter.chapter_number}章: 1枚目は CHAPTER_TITLE である必要があります")
    for i in range(1, len(slides)):
        if (
            slides[i].layout_type == slides[i - 1].layout_type
            and slides[i].layout_type not in LAYOUT_ROTATION_EXEMPT
        ):
            errors.append(
                f"第{chapter.chapter_number}章 Slide {i + 1}: layout_type '{slides[i].layout_type}' が連続しています"
            )
    for s in slides:
        if _is_blank(s.key_message):
            errors.append(f"第{chapter.chapter_number}章 Slide: key_message が空です（{s.title}）")
        errors.extend(_validate_slide_content(s))
    return errors


def _validate_slide_content(s) -> list[str]:
    errors: list[str] = []
    if s.layout_type in ("TITLE", "CHAPTER_TITLE"):
        return errors
    slide_num = s.slide_number
    if s.layout_type in ("GRID_1X3", "GRID_2X2"):
        required = 3 if s.layout_type == "GRID_1X3" else 4
        if not s.grid_cells or len(s.grid_cells) != required:
            errors.append(f"Slide {slide_num}: grid_cells が {required} 件ではありません")
        elif s.grid_cells:
            errors.extend(_validate_grid_cells(s.grid_cells, slide_num, required))
    elif s.layout_type == "TWO_COLUMN_TEXT_AND_BLOCK":
        if len(s.bullet_points) < 3:
            errors.append(f"Slide {slide_num}: bullet_points は3行以上必要")
        if s.visual is None:
            errors.append(f"Slide {slide_num}: visual 未設定")
        else:
            errors.extend(_validate_visual(s.visual, slide_num))
    elif s.layout_type == "FULL_STEP_FLOW":
        if s.visual is None or s.visual.kind != "STEP_FLOW":
            errors.append(f"Slide {slide_num}: FULL_STEP_FLOW には visual.kind=STEP_FLOW が必要")
        elif s.visual:
            errors.extend(_validate_visual(s.visual, slide_num))
    elif s.layout_type == "MATURITY_BAR_FULL":
        if s.visual is None or s.visual.kind != "MATURITY_BAR":
            errors.append(f"Slide {slide_num}: MATURITY_BAR_FULL には visual.kind=MATURITY_BAR が必要")
        elif s.visual:
            errors.extend(_validate_visual(s.visual, slide_num))
    return errors


def _validate_theme(theme) -> list[str]:
    issues: list[str] = []
    for name, color in (
        ("dominant_color", theme.dominant_color),
        ("support_color", theme.support_color),
        ("accent_color", theme.accent_color),
    ):
        if _is_blank(color):
            issues.append(f"theme.{name} が空です")
    if theme.accent_color and _relative_luminance_safe(theme.accent_color) > 0.85:
        issues.append(
            "theme.accent_color が明るすぎます（#FFFFFF 等は禁止。コントラスト確保のため濃いアクセント色を選ぶこと）"
        )
    if theme.support_color and theme.dominant_color:
        if _contrast_ratio_safe(theme.support_color, theme.dominant_color) < 2.0:
            issues.append("theme.support_color と dominant_color のコントラストが不足しています")
    return issues


def _relative_luminance_safe(hex_color: str) -> float:
    from src.pptx_creator import _relative_luminance

    try:
        return _relative_luminance(hex_color)
    except (ValueError, TypeError):
        return 0.5


def _contrast_ratio_safe(hex_a: str, hex_b: str) -> float:
    from src.pptx_creator import _contrast_ratio

    try:
        return _contrast_ratio(hex_a, hex_b)
    except (ValueError, TypeError):
        return 1.0


def validate(data: PresentationData) -> tuple[bool, list[str]]:
    """生成データがルールを満たすか検証し、(成功フラグ, エラー一覧) を返す"""
    errors: list[str] = []
    slides = data.slides

    errors.extend(_validate_theme(data.theme))

    if len(slides) < MIN_SLIDE_COUNT:
        errors.append(f"スライド数が不足（{len(slides)} 枚）。最低 {MIN_SLIDE_COUNT} 枚必要です")

    if slides and slides[0].layout_type != "TITLE":
        errors.append("1枚目は TITLE である必要があります")

    # 1. 連続レイアウト検知
    for i in range(1, len(slides)):
        if (
            slides[i].layout_type == slides[i - 1].layout_type
            and slides[i].layout_type not in LAYOUT_ROTATION_EXEMPT
        ):
            errors.append(
                f"Slide {i + 1}: layout_type '{slides[i].layout_type}' が連続しています"
            )

    for s in slides:
        if _is_blank(s.key_message) and s.layout_type not in LAYOUT_ROTATION_EXEMPT:
            errors.append(f"Slide {s.slide_number}: key_message が空です")

        if s.layout_type in ("GRID_1X3", "GRID_2X2"):
            required = 3 if s.layout_type == "GRID_1X3" else 4
            if not s.grid_cells or len(s.grid_cells) != required:
                errors.append(f"Slide {s.slide_number}: grid_cells が {required} 件ではありません")
            elif s.grid_cells:
                errors.extend(_validate_grid_cells(s.grid_cells, s.slide_number, required))
        elif s.layout_type == "TWO_COLUMN_TEXT_AND_BLOCK":
            errors.extend(_validate_slide_content(s))
        elif s.layout_type in ("FULL_STEP_FLOW", "MATURITY_BAR_FULL"):
            errors.extend(_validate_slide_content(s))

    return len(errors) == 0, errors


def validate_presentation(data: PresentationData) -> list[str]:
    """後方互換: エラー文字列リストのみ返す（gemini_client 用）"""
    _, errors = validate(data)
    return errors
