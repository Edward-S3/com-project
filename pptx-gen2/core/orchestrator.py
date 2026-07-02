"""工程別LLMルーティング・フォールバック。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.llm_clients import LLMClientManager, LLMResponse
from core.model_registry import resolve_model

logger = logging.getLogger(__name__)

DISPLAY_NAMES = {
    "gemini": "Gemini",
    "claude": "Claude",
    "gpt4o": "GPT-4o",
    "grok": "Grok",
    "auto": "自動",
}


@dataclass
class RoutingDecision:
    task_name: str
    provider: str
    reason: str
    manual: bool = False


class Orchestrator:
    def __init__(
        self,
        llm: LLMClientManager,
        routing_path: Path = Path("config/task_routing.json"),
        log_dir: Path = Path("logs"),
    ) -> None:
        self.llm = llm
        self.routing_path = routing_path
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.routing = self._load_routing()
        self.manual_overrides: Dict[str, str] = {}
        self.model_overrides: Dict[str, str] = {}
        self.decisions: List[RoutingDecision] = []
        self.on_status: Optional[Callable[[str, str, str], None]] = None
        self.on_resolved: Optional[Callable[[str, str, str, str, bool], None]] = None

    def _load_routing(self) -> Dict[str, Any]:
        with open(self.routing_path, encoding="utf-8") as f:
            return json.load(f)

    def save_routing(self) -> None:
        with open(self.routing_path, "w", encoding="utf-8") as f:
            json.dump(self.routing, f, ensure_ascii=False, indent=2)

    def set_manual_override(self, task_name: str, value: str) -> None:
        if value == "auto":
            self.manual_overrides.pop(task_name, None)
        else:
            self.manual_overrides[task_name] = value

    def set_model_override(self, task_name: str, value: str) -> None:
        if value in ("auto", ""):
            self.model_overrides.pop(task_name, None)
        else:
            self.model_overrides[task_name] = value

    def _resolve_task_model(self, task_name: str, provider: str) -> str:
        override = self.model_overrides.get(task_name)
        return resolve_model(provider, override)

    def _select_provider(self, task_name: str) -> RoutingDecision:
        if task_name in self.manual_overrides:
            provider = self.manual_overrides[task_name]
            if not self.llm.available.get(provider):
                raise RuntimeError(f"{DISPLAY_NAMES.get(provider, provider)} のAPIキーが未設定です")
            reason = "ユーザー手動指定"
            decision = RoutingDecision(task_name, provider, reason, manual=True)
            self.decisions.append(decision)
            return decision

        task_cfg = self.routing.get(task_name) or self.routing.get("fallback", {})
        priority: List[str] = task_cfg.get("priority", [])
        reason = task_cfg.get("reason", "")
        for provider in priority:
            if self.llm.available.get(provider):
                decision = RoutingDecision(task_name, provider, reason)
                self.decisions.append(decision)
                return decision

        raise RuntimeError(f"タスク {task_name} に利用可能なモデルがありません")

    def _log(self, message: str) -> None:
        log_file = self.log_dir / f"{datetime.now().strftime('%Y%m%d')}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} {message}\n")

    def run_task(
        self,
        task_name: str,
        prompt: str,
        *,
        system: Optional[str] = None,
        images: Optional[List[Path]] = None,
        json_mode: bool = False,
        timeout_sec: float = 120,
        cancel_event=None,
        skip_on_failure: bool = False,
    ) -> Optional[LLMResponse]:
        task_cfg = self.routing.get(task_name) or self.routing.get("fallback", {})
        candidates: List[str] = []
        if task_name in self.manual_overrides:
            candidates = [self.manual_overrides[task_name]]
        else:
            candidates = list(task_cfg.get("priority", []))
            candidates.extend(self.routing.get("fallback", {}).get("priority", []))

        seen = set()
        ordered = []
        for p in candidates:
            if p not in seen and self.llm.available.get(p):
                ordered.append(p)
                seen.add(p)

        last_error: Optional[Exception] = None
        for provider in ordered:
            reason = task_cfg.get("reason", "フォールバック")
            model = self._resolve_task_model(task_name, provider)
            if self.on_status:
                self.on_status(task_name, provider, reason)
            try:
                resp = self.llm.generate(
                    provider,
                    prompt,
                    system=system,
                    images=images,
                    json_mode=json_mode,
                    timeout_sec=timeout_sec,
                    cancel_event=cancel_event,
                    model=model,
                )
                self._log(f"OK task={task_name} provider={provider} model={resp.model}")
                manual = task_name in self.manual_overrides
                self.decisions.append(RoutingDecision(task_name, provider, reason, manual=manual))
                if self.on_resolved:
                    self.on_resolved(task_name, provider, resp.model, reason, manual)
                return resp
            except InterruptedError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("タスク %s で %s が失敗: %s", task_name, provider, exc)
                self._log(f"FAIL task={task_name} provider={provider} error={exc}")

        if skip_on_failure:
            logger.error("タスク %s をスキップ: %s", task_name, last_error)
            return None
        raise RuntimeError(f"タスク {task_name} の全候補が失敗しました: {last_error}")
