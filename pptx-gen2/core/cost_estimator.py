"""事前コスト見積もり・予算シナリオ生成。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from config.design_system import WEB_FETCH_MAX_WORKERS
from core.file_ingest import IngestedSource, local_stats
from core.llm_clients import LLMClientManager, UsageRecord
from core.orchestrator import Orchestrator
from core.web_source_ingest import fetch_web_page, is_youtube_url, parse_urls


@dataclass
class CostEstimate:
    total_usd: float
    breakdown: Dict[str, float] = field(default_factory=dict)
    slide_count: int = 10
    warnings: List[str] = field(default_factory=list)
    is_placeholder_pricing: bool = False


@dataclass
class ScenarioEstimate:
    scenario_id: str
    label: str
    description: str
    total_usd: float
    qa_rounds: int
    template_only: bool
    compress_slides: bool


def estimate_tokens(text: str) -> int:
    """概算トークン数(英数字≒4文字/トークン、日本語≒1.2文字/トークン)。"""
    if not text:
        return 0
    ascii_chars = len(re.findall(r"[A-Za-z0-9]", text))
    other = len(text) - ascii_chars
    return int(ascii_chars / 4 + other / 1.2)


def _load_pricing(path: Path = Path("config/pricing_rates.json")) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_scenarios(path: Path = Path("config/budget_scenarios.json")) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _pricing_unset(pricing: dict) -> bool:
    for key in ("gemini", "claude", "gpt4o", "grok"):
        p = pricing.get(key, {})
        if any(v == 0.0 for k, v in p.items() if k.endswith("_per_1m") or k.endswith("_per_sec") or k.endswith("_per_unit")):
            return True
    return False


def youtube_duration_sec(url: str) -> float:
    try:
        oembed = f"https://www.youtube.com/oembed?url={url}&format=json"
        r = requests.get(oembed, timeout=15)
        if r.ok:
            # oEmbed doesn't return duration; use conservative default 10min
            return 600.0
    except Exception:
        pass
    return 600.0


def prepare_sources_for_estimate(
    file_sources: List[IngestedSource],
    youtube_urls: List[str],
    web_urls: List[str],
    cache: Dict[str, str],
) -> List[IngestedSource]:
    all_src = list(file_sources)
    for url in youtube_urls:
        dur = youtube_duration_sec(url)
        all_src.append(
            IngestedSource(
                name=url,
                source_type="youtube",
                text="",
                metadata={"duration_sec": dur, "url": url},
            )
        )
    for url in web_urls:
        if url in cache:
            text = cache[url]
        else:
            text = fetch_web_page(url) or ""
            cache[url] = text
        all_src.append(
            IngestedSource(
                name=url,
                source_type="web",
                text=text,
                metadata={"char_count": len(text), "url": url},
            )
        )
    return all_src


def estimate_cost(
    sources: List[IngestedSource],
    slide_count: int,
    orchestrator: Orchestrator,
    *,
    qa_max_rounds: int = 5,
    template_only: bool = False,
) -> CostEstimate:
    pricing = _load_pricing()
    warnings: List[str] = []
    placeholder = _pricing_unset(pricing)
    if placeholder:
        warnings.append("料金が未設定です。config/pricing_rates.json または詳細設定で更新してください。")

    input_tokens = sum(estimate_tokens(s.text) for s in sources)
    for s in sources:
        if s.source_type in {"audio", "video", "youtube"}:
            dur = s.metadata.get("duration_sec", 0)
            rate = pricing["gemini"].get("video_tokens_per_sec", 300)
            if s.source_type == "audio":
                rate = pricing["gemini"].get("audio_tokens_per_sec", 32)
            input_tokens += int(dur * rate)

    out_cfg = pricing.get("output_tokens_per_slide", {})
    output_tokens = slide_count * (
        out_cfg.get("structured_json", 800)
        + out_cfg.get("narrative", 400)
        + (0 if template_only else out_cfg.get("layout_code", 600))
    )

    breakdown: Dict[str, float] = {}
    tasks = [
        "content_synthesis",
        "slide_structure_planning",
        "structured_json_payload",
        "japanese_narrative",
    ]
    if not template_only:
        tasks.append("slide_layout_code_generation")

    total = 0.0
    for task in tasks:
        provider = _resolve_provider(orchestrator, task)
        p = pricing.get(provider, pricing.get("gemini", {}))
        tin = input_tokens * 0.15 / 1_000_000 * p.get("input_per_1m", 0)
        tout = output_tokens * 0.2 / 1_000_000 * p.get("output_per_1m", 0)
        breakdown[task] = tin + tout
        total += tin + tout

    qa_provider = _resolve_provider(orchestrator, "design_visual_qa")
    qa_p = pricing.get(qa_provider, {})
    qa_cost = slide_count * qa_max_rounds * qa_p.get("image_per_unit", 0)
    breakdown["design_visual_qa"] = qa_cost
    total += qa_cost

    return CostEstimate(
        total_usd=round(total, 4),
        breakdown=breakdown,
        slide_count=slide_count,
        warnings=warnings,
        is_placeholder_pricing=placeholder,
    )


def _resolve_provider(orchestrator: Orchestrator, task: str) -> str:
    routing = orchestrator.routing.get(task, orchestrator.routing.get("fallback", {}))
    for p in routing.get("priority", []):
        if orchestrator.llm.available.get(p):
            return p
    return "gemini"


def build_scenarios(
    base: CostEstimate,
    orchestrator: Orchestrator,
    sources: List[IngestedSource],
    min_slides: int,
) -> List[ScenarioEstimate]:
    cfg = _load_scenarios()
    results = []
    for sc in cfg.get("scenarios", []):
        slides = base.slide_count
        if sc.get("compress_slides"):
            slides = max(min_slides, min_slides or 5)
        est = estimate_cost(
            sources,
            slides,
            orchestrator,
            qa_max_rounds=sc.get("qa_max_rounds", 5),
            template_only=sc.get("use_template_only", False),
        )
        results.append(
            ScenarioEstimate(
                scenario_id=sc["id"],
                label=sc["label"],
                description=sc["description"],
                total_usd=est.total_usd,
                qa_rounds=sc.get("qa_max_rounds", 5),
                template_only=sc.get("use_template_only", False),
                compress_slides=sc.get("compress_slides", False),
            )
        )
    return results


def actual_cost_usd(usage_records: List[UsageRecord], pricing_path: Path = Path("config/pricing_rates.json")) -> float:
    pricing = _load_pricing(pricing_path)
    total = 0.0
    for u in usage_records:
        p = pricing.get(u.provider, {})
        total += u.input_tokens / 1_000_000 * p.get("input_per_1m", 0)
        total += u.output_tokens / 1_000_000 * p.get("output_per_1m", 0)
        total += u.image_units * p.get("image_per_unit", 0)
        total += u.audio_video_seconds * p.get("video_per_sec", 0)
    return round(total, 4)


def fit_budget(
    budget_usd: float,
    sources: List[IngestedSource],
    orchestrator: Orchestrator,
    slide_count: int,
    min_slides: int,
    lever_priority: List[str],
) -> Dict[str, Any]:
    """予算内に収まるようレバーを順に適用。"""
    qa_rounds = 5
    template_only = False
    slides = slide_count
    for lever in lever_priority:
        est = estimate_cost(sources, slides, orchestrator, qa_max_rounds=qa_rounds, template_only=template_only)
        if est.total_usd <= budget_usd:
            break
        if lever == "reduce_qa":
            qa_rounds = max(1, qa_rounds - 2)
        elif lever == "compress_slides":
            slides = max(min_slides or 5, slides - 3)
        elif lever == "template_only":
            template_only = True
    est = estimate_cost(sources, slides, orchestrator, qa_max_rounds=qa_rounds, template_only=template_only)
    return {
        "slides": slides,
        "qa_max_rounds": qa_rounds,
        "template_only": template_only,
        "estimated_usd": est.total_usd,
    }
