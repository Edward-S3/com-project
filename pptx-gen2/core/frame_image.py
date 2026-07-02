"""背景フレーム画像の前処理（透明→白、黒背景→白）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from config.design_system import BACKGROUND_FRAME_PATH

# この値以下の RGB は背景とみなし白に置換（装飾ラインのシアン等は保持）
BLACK_REPLACE_THRESHOLD = 50

CACHE_NAME = "slide_background_frame_white.png"


def _replace_near_black_with_white(img, threshold: int = BLACK_REPLACE_THRESHOLD):
    from PIL import Image

    rgb = img.convert("RGB")
    pixels = list(rgb.getdata())
    cleaned = [
        (255, 255, 255) if r < threshold and g < threshold and b < threshold else (r, g, b)
        for r, g, b in pixels
    ]
    out = Image.new("RGB", rgb.size)
    out.putdata(cleaned)
    return out


def flatten_frame_to_white(source: Path, dest: Path) -> Path:
    """RGBA は白背景に合成、RGB の黒背景は白に置換。"""
    from PIL import Image

    img = Image.open(source)
    if img.mode in ("RGBA", "LA"):
        base = Image.new("RGBA", img.size, (255, 255, 255, 255))
        alpha = img.split()[-1]
        base.paste(img.convert("RGBA"), mask=alpha)
        out = base.convert("RGB")
    elif img.mode == "P" and "transparency" in img.info:
        img = img.convert("RGBA")
        base = Image.new("RGBA", img.size, (255, 255, 255, 255))
        base.paste(img, mask=img.split()[-1])
        out = base.convert("RGB")
    else:
        out = _replace_near_black_with_white(img)

    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, "PNG", optimize=True)
    return dest


def get_white_background_frame_path(source: Optional[Path] = None) -> Optional[Path]:
    """PPTX 埋め込み用の白背景フレーム画像パスを返す（必要時キャッシュ生成）。"""
    src = source or BACKGROUND_FRAME_PATH
    if not src.exists():
        return None

    cache = src.parent / CACHE_NAME
    if cache.exists() and cache.stat().st_mtime >= src.stat().st_mtime:
        return cache

    return flatten_frame_to_white(src, cache)
