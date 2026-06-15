"""メンテナンスモード時の実行プロセス追跡ログ"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import db

_REDACT_KEY_RE = re.compile(
    r"(password|passwd|api[_-]?key|secret|token|authorization)",
    re.IGNORECASE,
)
_MAX_TEXT = 4000
_MAX_JSON = 12000


def is_enabled() -> bool:
    return db.get_setting("maintenance_mode") == "1"


def _sanitize_value(key: str, value: Any) -> Any:
    if _REDACT_KEY_RE.search(str(key)):
        return "[REDACTED]"
    if isinstance(value, (bytes, bytearray)):
        return f"[binary {len(value)} bytes]"
    if isinstance(value, str):
        if len(value) > _MAX_TEXT:
            return value[:_MAX_TEXT] + f"\n…（{len(value):,} 文字中、先頭 {_MAX_TEXT:,} 文字）"
        return value
    if isinstance(value, list):
        if len(value) > 50:
            return [_sanitize_value(key, v) for v in value[:50]] + [f"…他 {len(value) - 50} 件"]
        return [_sanitize_value(key, v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_value(k, v) for k, v in value.items()}
    return value


def _detail_json(detail: dict[str, Any] | None) -> str:
    if not detail:
        return ""
    safe = {k: _sanitize_value(k, v) for k, v in detail.items()}
    raw = json.dumps(safe, ensure_ascii=False, default=str)
    if len(raw) > _MAX_JSON:
        return raw[:_MAX_JSON] + "…（truncated）"
    return raw


def start_trace(
    *,
    employee_id: str = "",
    session_id: str = "",
    context: dict[str, Any] | None = None,
) -> str | None:
    if not is_enabled():
        return None
    trace_id = str(uuid.uuid4())
    db.insert_maintenance_log(
        trace_id=trace_id,
        employee_id=employee_id,
        session_id=session_id,
        phase="init",
        step="trace_start",
        detail_json=_detail_json(context or {}),
        elapsed_ms=0,
    )
    return trace_id


def log_step(
    trace_id: str | None,
    phase: str,
    step: str,
    detail: dict[str, Any] | None = None,
    *,
    elapsed_ms: int = 0,
) -> None:
    if not trace_id or not is_enabled():
        return
    db.insert_maintenance_log(
        trace_id=trace_id,
        employee_id="",
        session_id="",
        phase=phase or "-",
        step=step or "-",
        detail_json=_detail_json(detail),
        elapsed_ms=max(0, int(elapsed_ms)),
    )


def end_trace(
    trace_id: str | None,
    *,
    status: str = "ok",
    detail: dict[str, Any] | None = None,
    elapsed_ms: int = 0,
) -> None:
    if not trace_id:
        return
    payload = {"status": status, **(detail or {})}
    log_step(trace_id, "done", "trace_end", payload, elapsed_ms=elapsed_ms)


def set_mode(enabled: bool) -> tuple[int, str]:
    """運用モード切替。通常運用へ戻す際はログを全削除。戻り値: (削除件数, メッセージ)"""
    if enabled:
        db.set_setting("maintenance_mode", "1")
        return 0, "メンテナンスモードを有効にしました。利用者の実行プロセスが記録されます。"
    deleted = db.purge_maintenance_logs()
    db.set_setting("maintenance_mode", "0")
    return deleted, f"通常運用に切り替えました。メンテナンスログ {deleted:,} 件を削除しました。"
