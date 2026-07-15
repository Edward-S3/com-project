"""FastAPI application entry point and REST API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import DATA_DIR, load_settings
from app.live_bridge import live_websocket_endpoint
from app.models import SessionConfig
from app.rubrics_util import strip_hidden_facts
from app.session_service import SessionService

STATIC_DIR = Path(__file__).resolve().parent / "static"
settings = load_settings()
service = SessionService(settings)

app = FastAPI(title="1on1 Interview Simulation", version="0.3.0")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class UserCreate(BaseModel):
    name: str
    department: str
    department_detail: str = ""
    age: int
    tenure_years: int
    grade: str
    consent: bool = False


class SessionCreate(BaseModel):
    user_id: int
    scene: str
    role: str
    difficulty: str
    io_mode: str = "text"
    persona_trait: str | None = None
    gap_pattern: str | None = None


class MessageCreate(BaseModel):
    text: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    grades = json.loads((DATA_DIR / "grades.json").read_text(encoding="utf-8"))
    departments = json.loads((DATA_DIR / "departments.json").read_text(encoding="utf-8"))
    return {
        "grades": grades["grades"],
        "departments": departments["categories"],
        "scenes": [
            {"id": "initial", "label": "期初の目標設定面談"},
            {"id": "final", "label": "期末の評価面談"},
        ],
        "roles": [
            {"id": "supervisor", "label": "上司役（AI=部下）"},
            {"id": "subordinate", "label": "部下役（AI=上司）"},
        ],
        "difficulties": [
            {"id": "sme", "label": "中小企業"},
            {"id": "enterprise", "label": "大企業"},
            {"id": "consultant", "label": "コンサルタント"},
        ],
        "io_modes": [
            {"id": "text", "label": "テキスト対話"},
            {"id": "voice", "label": "音声対話"},
        ],
        "live_configured": bool(settings.model_gemini_live and settings.gemini_api_key),
    }


@app.get("/api/users")
def list_users() -> list[dict[str, Any]]:
    return service.list_users()


@app.post("/api/users")
def create_user(body: UserCreate) -> dict[str, Any]:
    if not body.consent:
        raise HTTPException(400, "同意が必要です")
    department = body.department
    if body.department_detail:
        department = f"{body.department} / {body.department_detail}"
    user_id = service.create_user(
        {
            "name": body.name,
            "department": department,
            "age": body.age,
            "tenure_years": body.tenure_years,
            "grade": body.grade,
        }
    )
    return {"id": user_id}


@app.post("/api/sessions")
def create_session(body: SessionCreate) -> dict[str, Any]:
    config = SessionConfig(
        scene=body.scene,  # type: ignore[arg-type]
        role=body.role,  # type: ignore[arg-type]
        difficulty=body.difficulty,  # type: ignore[arg-type]
        io_mode=body.io_mode,  # type: ignore[arg-type]
    )
    if body.io_mode == "voice":
        if not settings.model_gemini_live or not settings.gemini_api_key:
            raise HTTPException(
                400,
                "音声モードには GEMINI_API_KEY と MODEL_GEMINI_LIVE の設定が必要です。",
            )
    try:
        state, opening = service.start_session(
            body.user_id,
            config,
            persona_trait=body.persona_trait,
            gap_pattern=body.gap_pattern,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "session_id": state.session_id,
        "mode_code": config.mode_code,
        "opening_message": opening,
        "persona_public": strip_hidden_facts(state.persona),
        "io_mode": config.io_mode,
        "ws_path": f"/ws/live/{state.session_id}" if config.io_mode == "voice" else None,
    }


@app.websocket("/ws/live/{session_id}")
async def ws_live(websocket: WebSocket, session_id: int) -> None:
    await live_websocket_endpoint(
        websocket, session_id, settings=settings, service=service
    )


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int) -> dict[str, Any]:
    try:
        return service.get_session_public(session_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/sessions/{session_id}/messages")
def post_message(session_id: int, body: MessageCreate) -> dict[str, Any]:
    try:
        return service.send_user_message(session_id, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/sessions/{session_id}/end")
def end_session(session_id: int) -> dict[str, Any]:
    try:
        return service.end_session(session_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/users/{user_id}/sessions")
def user_sessions(user_id: int) -> list[dict[str, Any]]:
    sessions = service.db.list_user_sessions(user_id)
    for s in sessions:
        if s.get("persona_json"):
            persona = json.loads(s["persona_json"])
            s["persona_public"] = strip_hidden_facts(persona)
            del s["persona_json"]
    return sessions


@app.get("/api/sessions/{session_id}/report")
def get_report(session_id: int) -> dict[str, Any]:
    report = service.db.get_report(session_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    return report
