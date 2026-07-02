"""中間 JSON 構造の生成・検証・画像クロップ"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from config import TEMP_DIR

from bbox_utils import sanitize_bbox, to_crop_box

logger = logging.getLogger(__name__)

VALID_ROLES = {"title", "body", "caption", "other"}


class StructureValidationError(Exception):
    pass


def _validate_bbox(bbox: dict, label: str) -> None:
    for key in ("x", "y", "width", "height"):
        if key not in bbox:
            raise StructureValidationError(f"{label}: bbox.{key} が不足しています")
    if bbox["width"] <= 0 or bbox["height"] <= 0:
        raise StructureValidationError(f"{label}: bbox の width/height が不正です")


def validate_slide_structure(data: dict) -> None:
    """中間 JSON の必須フィールドを検証する。"""
    required = ("slide_index", "slide_width_px", "slide_height_px", "image_dpi")
    for key in required:
        if key not in data:
            raise StructureValidationError(f"必須フィールド '{key}' がありません")

    for block in data.get("text_blocks") or []:
        if not str(block.get("content") or "").strip():
            raise StructureValidationError(f"text_block {block.get('id')}: content が空です")
        role = block.get("role", "other")
        if role not in VALID_ROLES:
            raise StructureValidationError(f"text_block {block.get('id')}: role が不正です")
        _validate_bbox(block.get("bbox") or {}, f"text_block {block.get('id')}")

    for block in data.get("image_blocks") or []:
        _validate_bbox(block.get("bbox") or {}, f"image_block {block.get('id')}")


def _sanitize_slide_bboxes(data: dict, img_w: int, img_h: int) -> dict:
    """全 bbox を画像内に正規化し、不正ブロックを除外する。"""
    out = dict(data)
    text_blocks = []
    for block in data.get("text_blocks") or []:
        bbox = sanitize_bbox(block.get("bbox") or {}, img_w, img_h)
        content = str(block.get("content") or "").strip()
        if bbox and content:
            text_blocks.append({**block, "bbox": bbox, "content": content})
    image_blocks = []
    for block in data.get("image_blocks") or []:
        bbox = sanitize_bbox(block.get("bbox") or {}, img_w, img_h)
        if bbox:
            image_blocks.append({**block, "bbox": bbox})
    out["text_blocks"] = text_blocks
    out["image_blocks"] = image_blocks
    return out


def build_slide_structure(data: dict, source_image: Image.Image, temp_dir: Path | None = None) -> dict:
    """
    検証済み中間 JSON を返す。image_blocks はクロップ画像を temp に保存し cropped_path を付与する。
    """
    if data.get("use_full_image"):
        return dict(data)

    data = _sanitize_slide_bboxes(data, source_image.width, source_image.height)
    validate_slide_structure(data)
    out = dict(data)
    work_dir = temp_dir or TEMP_DIR
    work_dir.mkdir(parents=True, exist_ok=True)

    slide_index = int(data["slide_index"])
    image_blocks = []
    for i, block in enumerate(data.get("image_blocks") or []):
        crop_box = to_crop_box(block["bbox"], source_image.width, source_image.height)
        if crop_box is None:
            logger.warning("スライド %d: 画像ブロック %s のクロップ座標が不正のためスキップ", slide_index, block.get("id"))
            continue
        x1, y1, x2, y2 = crop_box
        cropped = source_image.crop((x1, y1, x2, y2))
        block_id = block.get("id") or f"img_{i}"
        crop_path = work_dir / f"slide_{slide_index}_{block_id}.png"
        cropped.save(crop_path, format="PNG")
        image_blocks.append({**block, "id": block_id, "cropped_path": str(crop_path)})
        logger.info("スライド %d: 画像クロップ %s -> %s", slide_index, block_id, crop_path.name)

    out["image_blocks"] = image_blocks
    return out
