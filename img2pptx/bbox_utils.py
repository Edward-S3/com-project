"""bbox 座標の正規化・画像内へのクランプ"""
from __future__ import annotations


def sanitize_bbox(bbox: dict, img_w: int, img_h: int) -> dict | None:
    """
    Gemini 出力 bbox を x/y/width/height 形式に正規化し画像内に収める。

    - width/height が xmax/ymax（右下角）の場合を補正
    - 負の幅・高さを反転
    - 画像外座標をクランプ
    """
    if not bbox or img_w <= 0 or img_h <= 0:
        return None

    try:
        x = int(round(float(bbox["x"])))
        y = int(round(float(bbox["y"])))
        w_raw = int(round(float(bbox["width"])))
        h_raw = int(round(float(bbox["height"])))
    except (KeyError, TypeError, ValueError):
        return None

    w, h = w_raw, h_raw

    # xmax/ymax 形式（右下角）の誤解釈を補正
    if w > x and h > y and (x + w > img_w or y + h > img_h):
        w = w - x
        h = h - y
    elif w > img_w or h > img_h:
        if w > x and h > y:
            w = w - x
            h = h - y

    if w <= 0 or h <= 0:
        if w_raw > x and h_raw > y:
            w = w_raw - x
            h = h_raw - y

    if w < 0:
        x += w
        w = -w
    if h < 0:
        y += h
        h = -h

    if w <= 0 or h <= 0:
        return None

    x = max(0, min(x, img_w - 1))
    y = max(0, min(y, img_h - 1))
    w = min(w, img_w - x)
    h = min(h, img_h - y)

    if w <= 0 or h <= 0:
        return None

    return {"x": x, "y": y, "width": w, "height": h}


def to_crop_box(bbox: dict, img_w: int, img_h: int, pad: int = 0) -> tuple[int, int, int, int] | None:
    """PIL Image.crop 用の (x1, y1, x2, y2) を返す。"""
    sane = sanitize_bbox(bbox, img_w, img_h)
    if not sane:
        return None
    x1 = max(0, sane["x"] - pad)
    y1 = max(0, sane["y"] - pad)
    x2 = min(img_w, sane["x"] + sane["width"] + pad)
    y2 = min(img_h, sane["y"] + sane["height"] + pad)
    if x2 <= x1:
        x2 = min(img_w, x1 + 1)
    if y2 <= y1:
        y2 = min(img_h, y1 + 1)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2
