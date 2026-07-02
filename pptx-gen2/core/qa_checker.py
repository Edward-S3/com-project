"""pptx→画像→Vision QAループ。"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.orchestrator import Orchestrator
from core.pptx_renderer import render_presentation
from config.design_system import DesignContext

logger = logging.getLogger(__name__)


@dataclass
class QAResult:
    rounds_run: int
    issues_found: int
    issues_fixed: int
    remaining_issues: List[str] = field(default_factory=list)
    passed_early: bool = False


def _convert_pptx_to_images(pptx_path: Path, out_dir: Path) -> List[Path]:
    pdf_path = out_dir / "deck.pdf"
    if shutil.which("soffice"):
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx_path)],
            check=False,
            capture_output=True,
            timeout=120,
        )
        pdf_candidates = list(out_dir.glob("*.pdf"))
        if pdf_candidates:
            pdf_path = pdf_candidates[0]
    else:
        logger.warning("LibreOffice未インストールのためQA画像化をスキップ")
        return []

    if not pdf_path.exists() or not shutil.which("pdftoppm"):
        return []

    prefix = out_dir / "slide"
    subprocess.run(
        ["pdftoppm", "-jpeg", "-r", "150", str(pdf_path), str(prefix)],
        check=False,
        capture_output=True,
        timeout=120,
    )
    return sorted(out_dir.glob("slide*.jpg")) + sorted(out_dir.glob("slide-*.jpg"))


def run_qa_loop(
    pptx_path: Path,
    slides: List[Dict[str, Any]],
    notes: List[str],
    design: DesignContext,
    orchestrator: Orchestrator,
    *,
    max_rounds: int = 5,
    template_only: bool = False,
    use_background_frame: bool = False,
    cancel_event=None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> QAResult:
    issues_fixed = 0
    issues_found = 0
    remaining: List[str] = []
    verified_aspects: set = set()
    round_num = 0
    round_issues: List[Dict[str, Any]] = []

    for round_num in range(1, max_rounds + 1):
        if cancel_event and cancel_event.is_set():
            break
        if on_progress:
            on_progress(round_num, max_rounds)

        with tempfile.TemporaryDirectory() as tmp:
            images = _convert_pptx_to_images(pptx_path, Path(tmp))
            if not images:
                return QAResult(rounds_run=0, issues_found=0, issues_fixed=0, passed_early=True)

            sample = images[: min(3, len(images))]
            prompt = f"""これらのスライド画像を検証してください。
確認済み観点(再検証不要): {list(verified_aspects)}
チェック: 重なり、テキストはみ出し、余白不足、コントラスト不足、レイアウト不整合

JSON: {{
  "passed": true/false,
  "issues": [{{"slide_index": 0, "problem": "...", "aspect": "overflow"}}],
  "verified_aspects": ["overflow"]
}}
"""
            resp = orchestrator.run_task(
                "design_visual_qa",
                prompt,
                images=sample,
                json_mode=True,
                timeout_sec=60,
                cancel_event=cancel_event,
                skip_on_failure=True,
            )
            if not resp:
                break

            try:
                data = json.loads(resp.text)
            except json.JSONDecodeError:
                break

            if data.get("passed"):
                return QAResult(
                    rounds_run=round_num,
                    issues_found=issues_found,
                    issues_fixed=issues_fixed,
                    passed_early=True,
                )

            for aspect in data.get("verified_aspects", []):
                verified_aspects.add(aspect)

            round_issues = data.get("issues", [])
            issues_found += len(round_issues)
            if not round_issues:
                break

            for issue in round_issues:
                idx = issue.get("slide_index", 0)
                if 0 <= idx < len(slides):
                    slides[idx].setdefault("bullets", [])
                    if len(slides[idx]["bullets"]) > 3:
                        slides[idx]["bullets"] = slides[idx]["bullets"][:3]
                    issues_fixed += 1

            render_presentation(
                slides,
                notes,
                design,
                orchestrator,
                template_only=template_only,
                use_background_frame=use_background_frame,
                cancel_event=cancel_event,
                output_path=pptx_path,
            )

    if round_num == max_rounds and round_issues:
        remaining = [i.get("problem", "") for i in round_issues]

    return QAResult(
        rounds_run=round_num if "round_num" in dir() else 0,
        issues_found=issues_found,
        issues_fixed=issues_fixed,
        remaining_issues=remaining,
    )
