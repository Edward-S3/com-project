#!/usr/bin/env python3
"""NotebookLM スライド画像 PPTX → 編集可能 PPTX 変換（CLI）

品質前提: OCR・画像解析に基づく近似復元。完全な再現は保証しません。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from converter import convert_pptx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("img2pptx")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NotebookLM 出力 PPTX を編集可能な PPTX に変換します。",
    )
    parser.add_argument("--input", "-i", required=True, help="入力 .pptx ファイルパス")
    parser.add_argument("--output", "-o", default=None, help="出力 .pptx ファイルパス")
    args = parser.parse_args()

    try:
        report = convert_pptx(args.input, args.output)
    except Exception as exc:
        logger.error("変換失敗: %s", exc)
        return 1

    print(f"\n変換完了: {report.output_path}")
    print(f"スライド数: {len(report.slides)} / フォールバック: {report.fallback_count}")
    print("-" * 50)
    for slide in report.slides:
        status = "FALLBACK" if slide.fallback else "OK"
        line = (
            f"  スライド {slide.slide_index + 1}: "
            f"text={slide.text_count} images={slide.image_count} [{status}]"
        )
        if slide.error:
            line += f" — {slide.error}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
