"""変換パイプライン統合"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from config import OUTPUT_DIR, TEMP_DIR
from image_parser import parse_slide_image
from pptx_loader import LoadedPresentation, load_slide_images
from pptx_writer import write_presentation
from structure_builder import StructureValidationError, build_slide_structure

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class SlideReport:
    slide_index: int
    text_count: int
    image_count: int
    fallback: bool
    error: str | None = None


@dataclass
class ConversionReport:
    output_path: Path
    slides: list[SlideReport] = field(default_factory=list)
    fallback_count: int = 0


def _noop_progress(_msg: str, _cur: int, _total: int) -> None:
    pass


def convert_pptx(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    on_progress: ProgressCallback | None = None,
    cleanup_temp: bool = True,
) -> ConversionReport:
    """
    NotebookLM 出力 PPTX を編集可能 PPTX に変換する。

    品質前提: OCR・画像解析に基づく近似復元。完全な再現は保証しない。
    """
    progress = on_progress or _noop_progress
    input_path = Path(input_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"入力ファイルが見つかりません: {input_path}")

    if output_path is None:
        output_path = OUTPUT_DIR / f"{input_path.stem}_editable.pptx"
    else:
        output_path = Path(output_path)

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    loaded: LoadedPresentation = load_slide_images(str(input_path))
    total = len(loaded.slides)
    structures: list[dict] = []
    source_images: dict[int, object] = {}
    reports: list[SlideReport] = []

    for i, slide_img in enumerate(loaded.slides):
        idx = slide_img.slide_index
        progress(f"スライド {idx + 1}/{total}: Gemini 解析中...", i, total)
        source_images[idx] = slide_img.image

        parse_result = parse_slide_image(idx, slide_img.image, slide_img.image_dpi)
        progress(f"スライド {idx + 1}/{total}: 解析完了", i + 1, total)
        try:
            structure = build_slide_structure(parse_result.data, slide_img.image, TEMP_DIR)
        except StructureValidationError as exc:
            logger.warning("スライド %d: 構造検証失敗、フォールバック: %s", idx, exc)
            structure = {
                **parse_result.data,
                "text_blocks": [],
                "image_blocks": [],
                "use_full_image": True,
            }
            parse_result.fallback = True
            parse_result.error = str(exc)

        structures.append(structure)
        report = SlideReport(
            slide_index=idx,
            text_count=len(structure.get("text_blocks") or []),
            image_count=len(structure.get("image_blocks") or []),
            fallback=bool(parse_result.fallback or structure.get("use_full_image")),
            error=parse_result.error,
        )
        reports.append(report)
        logger.info(
            "スライド %d: text=%d images=%d fallback=%s",
            idx,
            report.text_count,
            report.image_count,
            report.fallback,
        )

    progress("PPTX 生成中...", total, total)
    write_presentation(
        structures,
        source_images,
        output_path,
        slide_width_emu=loaded.slide_width_emu,
        slide_height_emu=loaded.slide_height_emu,
    )

    if cleanup_temp and TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)

    fallback_count = sum(1 for r in reports if r.fallback)
    return ConversionReport(output_path=output_path, slides=reports, fallback_count=fallback_count)
