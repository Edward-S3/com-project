"""バックグラウンド生成パイプライン。"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config.design_system import DEFAULT_PALETTE_NAME, palette_from_name, palette_choices
from core.audio_video import process_audio_video_sources, process_youtube_urls
from core.content_synthesizer import synthesize_content
from core.cost_estimator import actual_cost_usd
from core.file_ingest import ingest_files
from core.llm_clients import LLMClientManager
from core.narrative_generator import generate_narratives
from core.orchestrator import DISPLAY_NAMES, Orchestrator
from core.payload_generator import generate_payload
from core.pptx_renderer import render_presentation
from core.qa_checker import run_qa_loop
from core.slide_planner import build_slide_plan
from core.web_source_ingest import ingest_web_urls, parse_urls, youtube_urls_only

logger = logging.getLogger(__name__)


@dataclass
class PipelineState:
    job_id: str = ""
    status: str = "idle"
    current_step: str = ""
    current_model: str = ""
    current_reason: str = ""
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None
    output_path: Optional[str] = None
    preview_images: List[str] = field(default_factory=list)
    actual_cost: float = 0.0
    estimated_cost: float = 0.0
    cost_paused: bool = False
    qa_summary: str = ""
    slide_count: int = 0
    presentation_minutes: int = 0
    plan_rationale: str = ""
    last_heartbeat: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished: bool = False
    cancelled: bool = False
    task_routing_live: Dict[str, Dict[str, str]] = field(default_factory=dict)


class PipelineRunner:
    def __init__(self, log_dir: Path = Path("logs")) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.state = PipelineState()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._cost_confirm = threading.Event()
        self._cost_confirm.set()
        self.llm = LLMClientManager()
        self.orchestrator = Orchestrator(self.llm, log_dir=log_dir)

    def _update(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self.state, k, v)
        self.state.last_heartbeat = time.time()

    def _status_cb(self, task: str, provider: str, reason: str) -> None:
        display = DISPLAY_NAMES.get(provider, provider)
        step = {
            "ingest": "ファイル取り込み",
            "content_synthesis": "内容統合",
            "slide_planning": "構成設計",
            "slide_structure_planning": "構成設計",
            "structured_json_payload": "スライド構造化",
            "payload": "スライド構造化",
            "japanese_narrative": "スピーカーノート生成",
            "narrative": "スピーカーノート生成",
            "slide_layout_code_generation": "スライド描画",
            "render": "スライド描画",
            "design_visual_qa": "QAチェック",
            "qa": "QAチェック",
        }.get(task, task)
        self._update(
            current_step=task,
            current_model=display,
            current_reason=reason,
            message=f"🔄 {step}... (使用モデル: {display} / 理由: {reason})",
        )

    def _resolved_cb(self, task: str, provider: str, model: str, reason: str, manual: bool) -> None:
        """自動/手動いずれかで確定したLLMを記録しUI更新用に保持。"""
        live = dict(self.state.task_routing_live)
        display = DISPLAY_NAMES.get(provider, provider)
        live[task] = {
            "provider": provider,
            "display": display,
            "model": model or "—",
            "reason": reason,
            "mode": "手動" if manual else "自動",
        }
        step = task
        self._update(
            task_routing_live=live,
            current_model=f"{display} ({model})" if model else display,
            message=f"🔄 {task}... (使用モデル: {display} / {model or '—'} / {'手動指定' if manual else '自動選定'})",
        )

    def cancel(self) -> None:
        self._cancel.set()
        self.state.cancelled = True
        self.state.status = "cancelled"
        self.state.finished = True

    def confirm_cost_continue(self) -> None:
        self.state.cost_paused = False
        self._cost_confirm.set()

    def start(self, config: Dict[str, Any]) -> str:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("既に処理が実行中です")
        self._cancel.clear()
        self._cost_confirm.set()
        self.state = PipelineState(
            job_id=str(uuid.uuid4())[:8],
            status="running",
            started_at=time.time(),
            message="処理を開始しています...",
            task_routing_live={},
        )
        self.orchestrator.manual_overrides = config.get("manual_overrides", {})
        self.orchestrator.model_overrides = config.get("model_overrides", {})
        self.orchestrator.on_status = self._status_cb
        self.orchestrator.on_resolved = self._resolved_cb
        self.llm.reset_usage()
        self._thread = threading.Thread(target=self._run, args=(config,), daemon=True)
        self._thread.start()
        return self.state.job_id

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise InterruptedError("キャンセル")

    def _check_cost(self, config: Dict[str, Any]) -> None:
        cost = actual_cost_usd(self.llm.total_usage)
        self.state.actual_cost = cost
        limit = config.get("estimated_cost", 0) * 1.3
        if limit > 0 and cost > limit and not self.state.cost_paused:
            self.state.cost_paused = True
            self._cost_confirm.clear()
            self._update(message="想定より費用がかさんでいます。続行しますか?")
            self._cost_confirm.wait(timeout=3600)

    def _run(self, config: Dict[str, Any]) -> None:
        temp_dir = Path("temp_uploads") / self.state.job_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._execute(config, temp_dir)
        except InterruptedError:
            self._update(status="cancelled", finished=True, message="処理をキャンセルしました")
        except Exception as exc:
            logger.exception("パイプライン失敗")
            self._update(status="error", error=str(exc), finished=True, message=f"エラー: {exc}")
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _execute(self, config: Dict[str, Any], temp_dir: Path) -> None:
        self._check_cancel()
        file_paths = [Path(p) for p in config.get("file_paths", [])]
        saved = []
        for p in file_paths:
            dest = temp_dir / p.name
            shutil.copy(p, dest)
            saved.append(dest)

        self._update(current_step="ingest", progress=0.05, message="ファイル取り込み中...")
        sources = ingest_files(saved)

        yt_urls = youtube_urls_only(parse_urls(config.get("youtube_urls", "")))
        web_urls = [u for u in parse_urls(config.get("web_urls", "")) if u not in yt_urls]

        if web_urls:
            self._update(message="Webサイト取得中...")
            web_result = ingest_web_urls(web_urls)
            sources.extend(web_result.sources)
            if web_result.warnings:
                config.setdefault("warnings", []).extend(web_result.warnings)

        if yt_urls:
            self._update(message="YouTube動画処理中...")
            yt_model = self.orchestrator._resolve_task_model("audio_video_understanding", "gemini")
            yt_sources, yt_fail = process_youtube_urls(
                yt_urls,
                self.llm,
                cancel_event=self._cancel,
                model=yt_model,
                on_progress=lambda c, t: self._update(message=f"YouTube動画処理中 ({c}/{t})"),
            )
            sources.extend(yt_sources)
            if yt_fail:
                config.setdefault("warnings", []).append(f"{yt_fail}件のYouTube動画は読み込めませんでした")

        sources = process_audio_video_sources(sources, self.orchestrator, self.llm, cancel_event=self._cancel)
        self._check_cost(config)

        purpose = config.get("purpose", "")
        audience = config.get("audience", "")
        self._update(current_step="content_synthesis", progress=0.2, message="内容統合中...")
        synthesis = synthesize_content(self.orchestrator, sources, purpose, audience, cancel_event=self._cancel)
        self._check_cost(config)

        palette_name = config.get("palette", "auto")
        if palette_name == "auto":
            palette_name = synthesis.get("suggested_palette", DEFAULT_PALETTE_NAME)
            if palette_name not in palette_choices():
                palette_name = DEFAULT_PALETTE_NAME
        design = palette_from_name(palette_name)
        use_background_frame = config.get("use_background_frame", False)

        self._update(current_step="slide_planning", progress=0.3, message="構成設計中...")
        plan = build_slide_plan(
            sources,
            synthesis,
            self.orchestrator,
            use_recommend=config.get("use_recommend", False),
            use_ai_analysis=config.get("use_ai_analysis", False),
            min_slides=config.get("min_slides"),
            max_slides=config.get("max_slides"),
            minutes=config.get("minutes"),
            cancel_event=self._cancel,
        )
        slide_count = plan.recommended_slides
        if config.get("compress_slides"):
            slide_count = config.get("min_slides") or max(5, slide_count - 3)
        self.state.slide_count = slide_count
        self.state.presentation_minutes = plan.presentation_minutes
        self.state.plan_rationale = plan.rationale

        self._update(current_step="payload", progress=0.45, message="スライド構造化中...")
        slides = generate_payload(
            self.orchestrator,
            synthesis,
            plan,
            purpose,
            audience,
            slide_count=slide_count,
            cancel_event=self._cancel,
        )
        self._check_cost(config)

        self._update(current_step="narrative", progress=0.6, message="スピーカーノート生成中...")
        notes = generate_narratives(self.orchestrator, slides, plan.presentation_minutes, cancel_event=self._cancel)
        self._check_cost(config)

        out_name = f"deck_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
        output_path = Path("outputs") / out_name
        template_only = config.get("template_only", False)
        self._update(current_step="render", progress=0.75, message="スライド描画中...")
        render_presentation(
            slides,
            notes,
            design,
            self.orchestrator,
            template_only=template_only,
            use_background_frame=use_background_frame,
            cancel_event=self._cancel,
            output_path=output_path,
        )
        self._check_cost(config)

        qa_rounds = config.get("qa_max_rounds", 5)
        if qa_rounds > 0:
            self._update(current_step="qa", progress=0.85, message="QAチェック中...")
            qa = run_qa_loop(
                output_path,
                slides,
                notes,
                design,
                self.orchestrator,
                max_rounds=qa_rounds,
                template_only=template_only,
                use_background_frame=use_background_frame,
                cancel_event=self._cancel,
                on_progress=lambda c, t: self._update(message=f"QAチェック中... ({c}/{t})"),
            )
            if qa.remaining_issues:
                self.state.qa_summary = f"QAチェック: {qa.rounds_run}回実施、未解消の軽微な問題が{len(qa.remaining_issues)}件あります"
            else:
                self.state.qa_summary = f"QAチェック: {qa.rounds_run}回実施、{qa.issues_fixed}件の問題を検出・修正済み"

        self.state.actual_cost = actual_cost_usd(self.llm.total_usage)
        self._update(
            status="completed",
            progress=1.0,
            finished=True,
            output_path=str(output_path),
            message="生成が完了しました",
        )
        log_file = self.log_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "job_id": self.state.job_id,
                    "actual_cost": self.state.actual_cost,
                    "decisions": [d.__dict__ for d in self.orchestrator.decisions],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
