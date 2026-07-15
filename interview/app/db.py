"""SQLite persistence for users, sessions, turns, and reports."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class User:
    id: int
    name: str
    department: str
    age: int
    tenure_years: int
    grade: str
    created_at: str


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    department TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    tenure_years INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    scene TEXT NOT NULL,
                    role TEXT NOT NULL,
                    difficulty TEXT NOT NULL,
                    io_mode TEXT NOT NULL,
                    persona_json TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    end_reason TEXT,
                    models_json TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    seq INTEGER NOT NULL,
                    speaker TEXT NOT NULL,
                    text TEXT NOT NULL,
                    audio_flag INTEGER NOT NULL DEFAULT 0,
                    warnings_json TEXT,
                    ts TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    scores_json TEXT NOT NULL,
                    good_points_json TEXT NOT NULL,
                    improvements_json TEXT NOT NULL,
                    overall_grade TEXT,
                    overall_evaluation TEXT,
                    summary TEXT NOT NULL,
                    model_answer TEXT,
                    avg_score REAL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                """
            )

    def create_user(
        self,
        name: str,
        department: str,
        age: int,
        tenure_years: int,
        grade: str,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (name, department, age, tenure_years, grade, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, department, age, tenure_years, grade, _utc_now()),
            )
            return int(cursor.lastrowid)

    def get_user(self, user_id: int) -> User | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                return None
            return User(
                id=row["id"],
                name=row["name"],
                department=row["department"],
                age=row["age"],
                tenure_years=row["tenure_years"],
                grade=row["grade"],
                created_at=row["created_at"],
            )

    def create_session(
        self,
        user_id: int,
        scene: str,
        role: str,
        difficulty: str,
        io_mode: str,
        persona: dict[str, Any] | None = None,
        models: dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions (
                    user_id, scene, role, difficulty, io_mode, persona_json,
                    started_at, models_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    scene,
                    role,
                    difficulty,
                    io_mode,
                    json.dumps(persona, ensure_ascii=False) if persona else None,
                    _utc_now(),
                    json.dumps(models, ensure_ascii=False) if models else None,
                ),
            )
            return int(cursor.lastrowid)

    def add_turn(
        self,
        session_id: int,
        seq: int,
        speaker: str,
        text: str,
        audio_flag: bool = False,
        warnings: list[str] | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO turns (
                    session_id, seq, speaker, text, audio_flag, warnings_json, ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    seq,
                    speaker,
                    text,
                    int(audio_flag),
                    json.dumps(warnings, ensure_ascii=False) if warnings else None,
                    _utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_turns(self, session_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
            return [dict(row) for row in rows]

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            data = dict(row)
            if data.get("persona_json"):
                data["persona"] = json.loads(data["persona_json"])
            data.pop("persona_json", None)
            if data.get("models_json"):
                data["models"] = json.loads(data["models_json"])
            data.pop("models_json", None)
            return data

    def get_user_profile(self, user_id: int) -> dict[str, Any]:
        user = self.get_user(user_id)
        if user is None:
            raise ValueError("User not found")
        return {
            "id": user.id,
            "name": user.name,
            "department": user.department,
            "age": user.age,
            "tenure_years": user.tenure_years,
            "grade": user.grade,
        }

    def end_session(self, session_id: int, reason: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ?",
                (_utc_now(), reason, session_id),
            )

    def list_user_sessions(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*, r.avg_score, r.overall_grade
                FROM sessions s
                LEFT JOIN reports r ON r.session_id = s.id
                WHERE s.user_id = ?
                ORDER BY s.started_at DESC
                """,
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_report(
        self,
        session_id: int,
        scores: dict[str, Any],
        good_points: list[Any],
        improvements: list[Any],
        overall_grade: str | None,
        overall_evaluation: str,
        summary: str,
        model_answer: str | None,
        avg_score: float | None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        scores_payload = dict(scores)
        if extra:
            scores_payload["_meta"] = extra
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reports (
                    session_id, scores_json, good_points_json, improvements_json,
                    overall_grade, overall_evaluation, summary, model_answer, avg_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    json.dumps(scores_payload, ensure_ascii=False),
                    json.dumps(good_points, ensure_ascii=False),
                    json.dumps(improvements, ensure_ascii=False),
                    overall_grade,
                    overall_evaluation,
                    summary,
                    model_answer,
                    avg_score,
                ),
            )
            return int(cursor.lastrowid)

    def get_report(self, session_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM reports WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["scores"] = json.loads(data["scores_json"])
            data["good_points"] = json.loads(data["good_points_json"])
            data["improvements"] = json.loads(data["improvements_json"])
            data.pop("scores_json", None)
            data.pop("good_points_json", None)
            data.pop("improvements_json", None)
            return data
