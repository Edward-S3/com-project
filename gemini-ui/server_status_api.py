"""
server_status_api.py — ランチャー用サーバー負荷 API（管理者認証必須）

起動:
  uvicorn server_status_api:app --host 172.16.16.10 --port 8510
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import db

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

JST = timezone(timedelta(hours=9))
TOKEN_TTL_SEC = 30 * 60
CPU_CACHE_SEC = 8

SERVICES = [
    {"alias": "faq", "unit": "faq-bot-a.service", "label": "社内FAQボット"},
    {"alias": "faqlog", "unit": "faqlog.service", "label": "FAQログ"},
    {"alias": "eval", "unit": "employee-eval.service", "label": "社員成果物評価"},
    {"alias": "fback", "unit": "fback.service", "label": "アンケート"},
    {"alias": "exam", "unit": "exam-app.service", "label": "試験システム"},
    {"alias": "nai", "unit": "gemini-ui.service", "label": "社内 AI (NAI)"},
    {"alias": "naictrl", "unit": "gemini-ui-admin.service", "label": "NAI 管理"},
    {"alias": "tts", "unit": "tts.service", "label": "音声合成"},
]

_cpu_cache: dict = {"ts": 0.0, "value": 0.0}


def _secret() -> bytes:
    key = (
        os.environ.get("SERVER_STATUS_SECRET")
        or os.environ.get("ADMIN_PASSWORD")
        or "nai-launcher-status"
    )
    return key.encode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def make_token(employee_id: str) -> str:
    payload = {
        "sub": employee_id,
        "exp": int(time.time()) + TOKEN_TTL_SEC,
        "adm": 1,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token: str) -> str | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except (json.JSONDecodeError, ValueError):
        return None
    if payload.get("adm") != 1:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    emp = payload.get("sub")
    user = db.get_user(emp) if emp else None
    if not user or not user.get("is_admin") or not user.get("is_active"):
        return None
    return emp


def _read_cpu_times() -> tuple[int, int]:
    with open("/proc/stat", encoding="utf-8") as f:
        parts = f.readline().split()
    idle = int(parts[4]) + int(parts[5])
    total = sum(int(x) for x in parts[1:8])
    return idle, total


def cpu_percent() -> float:
    now = time.time()
    if now - _cpu_cache["ts"] < CPU_CACHE_SEC:
        return _cpu_cache["value"]

    idle1, total1 = _read_cpu_times()
    time.sleep(0.35)
    idle2, total2 = _read_cpu_times()
    total_delta = total2 - total1
    if total_delta <= 0:
        value = 0.0
    else:
        value = round(100.0 * (1.0 - (idle2 - idle1) / total_delta), 1)

    _cpu_cache["ts"] = now
    _cpu_cache["value"] = value
    return value


def memory_stats() -> dict:
    info: dict[str, int] = {}
    with open("/proc/meminfo", encoding="utf-8") as f:
        for line in f:
            key, rest = line.split(":", 1)
            info[key] = int(rest.strip().split()[0])
    total_kb = info.get("MemTotal", 0)
    avail_kb = info.get("MemAvailable", info.get("MemFree", 0))
    used_kb = max(0, total_kb - avail_kb)
    percent = round(100.0 * used_kb / total_kb, 1) if total_kb else 0.0
    return {
        "total_gb": round(total_kb / 1024 / 1024, 1),
        "used_gb": round(used_kb / 1024 / 1024, 1),
        "available_gb": round(avail_kb / 1024 / 1024, 1),
        "percent": percent,
    }


def load_stats() -> dict:
    with open("/proc/loadavg", encoding="utf-8") as f:
        parts = f.read().split()
    ncpu = os.cpu_count() or 1
    load_1 = float(parts[0])
    return {
        "load_1m": load_1,
        "load_5m": float(parts[1]),
        "load_15m": float(parts[2]),
        "cpu_count": ncpu,
        "load_ratio": round(load_1 / ncpu, 2),
    }


def disk_stats() -> dict:
    usage = shutil.disk_usage("/")
    percent = round(100.0 * usage.used / usage.total, 1) if usage.total else 0.0
    return {
        "mount": "/",
        "total_gb": round(usage.total / 1024 ** 3, 1),
        "used_gb": round(usage.used / 1024 ** 3, 1),
        "free_gb": round(usage.free / 1024 ** 3, 1),
        "percent": percent,
    }


def service_status(unit: str) -> str:
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return (proc.stdout or proc.stderr or "unknown").strip()
    except Exception:
        return "unknown"


def _level(value: float, warn: float, critical: float) -> str:
    if value >= critical:
        return "critical"
    if value >= warn:
        return "warn"
    return "ok"


def collect_metrics() -> dict:
    cpu = cpu_percent()
    mem = memory_stats()
    load = load_stats()
    disk = disk_stats()
    services = [
        {
            "alias": s["alias"],
            "label": s["label"],
            "unit": s["unit"],
            "status": service_status(s["unit"]),
        }
        for s in SERVICES
    ]
    inactive = [s for s in services if s["status"] != "active"]

    levels = [
        _level(cpu, 75, 90),
        _level(mem["percent"], 80, 92),
        _level(load["load_ratio"], 0.7, 1.0),
        _level(disk["percent"], 85, 95),
    ]
    if inactive:
        levels.append("warn" if len(inactive) == 1 else "critical")

    if "critical" in levels:
        overall = "critical"
    elif "warn" in levels:
        overall = "warn"
    else:
        overall = "ok"

    return {
        "timestamp": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "overall": overall,
        "cpu_percent": cpu,
        "memory": mem,
        "load": load,
        "disk": disk,
        "services": services,
        "inactive_services": [s["label"] for s in inactive],
        "poll_interval_sec": 10,
    }


def _auth_header(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def auth_login(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "リクエスト形式が不正です。"}, status_code=400)

    emp_id = (body.get("employee_id") or "").strip()
    password = body.get("password") or ""
    if not emp_id or not password:
        return JSONResponse({"error": "社員番号とパスワードを入力してください。"}, status_code=400)

    user = db.authenticate_user(emp_id, password)
    if not user:
        return JSONResponse({"error": "社員番号またはパスワードが正しくありません。"}, status_code=401)
    if not user.get("is_admin"):
        return JSONResponse({"error": "管理者権限がありません。"}, status_code=403)

    token = make_token(emp_id)
    return JSONResponse({
        "token": token,
        "username": user.get("username", ""),
        "employee_id": emp_id,
        "expires_in": TOKEN_TTL_SEC,
    })


async def get_metrics(request: Request) -> JSONResponse:
    token = _auth_header(request)
    emp = verify_token(token) if token else None
    if not emp:
        return JSONResponse({"error": "認証が必要です。"}, status_code=401)

    try:
        data = collect_metrics()
    except Exception as ex:
        return JSONResponse({"error": f"メトリクス取得に失敗しました: {ex}"}, status_code=500)

    user = db.get_user(emp)
    data["viewer"] = emp
    data["viewer_name"] = (user or {}).get("username") or emp
    return JSONResponse(data)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


app = Starlette(
    routes=[
        Route("/api/server-status/health", health, methods=["GET"]),
        Route("/api/server-status/auth", auth_login, methods=["POST"]),
        Route("/api/server-status/metrics", get_metrics, methods=["GET"]),
    ],
)
