"""db.py — 認証（NAI 共有）と TTS 利用ログ"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import datetime
from datetime import timezone, timedelta

NAI_DB_PATH = "/opt/gemini-ui/gemini_ui.db"
TTS_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts.db")
JST = timezone(timedelta(hours=9))


def jst_now() -> str:
    return datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def _conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, key_hex = stored.split("$", 1)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def authenticate_user(employee_id: str, password: str) -> dict | None:
    """NAI（gemini_ui.db）のユーザーで認証"""
    if not os.path.isfile(NAI_DB_PATH):
        return None
    conn = _conn(NAI_DB_PATH)
    row = conn.execute(
        "SELECT * FROM users WHERE employee_id=? AND is_active=1",
        (employee_id.strip(),),
    ).fetchone()
    conn.close()
    if row and verify_password(password, row["password_hash"]):
        return dict(row)
    return None


def get_user(employee_id: str) -> dict | None:
    if not os.path.isfile(NAI_DB_PATH):
        return None
    conn = _conn(NAI_DB_PATH)
    row = conn.execute(
        "SELECT * FROM users WHERE employee_id=?", (employee_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _nai_setting(key: str, default: str = "") -> str:
    if not os.path.isfile(NAI_DB_PATH):
        return default
    conn = _conn(NAI_DB_PATH)
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def _ensure_nai_session_timeout_schema() -> None:
    if not os.path.isfile(NAI_DB_PATH):
        return
    conn = _conn(NAI_DB_PATH)
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN session_timeout_sec INTEGER DEFAULT -1"
        )
        conn.commit()
    except Exception:
        pass
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("session_timeout_default", "600"),
    )
    conn.commit()
    conn.close()


def format_session_timeout_label(seconds: int) -> str:
    if seconds <= 0:
        return "無制限"
    if seconds < 60:
        return f"{seconds}秒"
    minutes, rem = divmod(seconds, 60)
    if rem:
        return f"{minutes}分{rem}秒"
    return f"{minutes}分"


def get_effective_session_timeout_sec(employee_id: str) -> int:
    """セッションタイムアウト秒数（-1=グローバル, 0=無制限, >0=秒）"""
    user = get_user(employee_id)
    if user:
        val = int(user.get("session_timeout_sec", -1))
        if val >= 0:
            return val
    return int(_nai_setting("session_timeout_default", "600") or 600)


def user_can_access_tts(user: dict) -> bool:
    """TTS 利用許可（users.tts_enabled）"""
    if not user or not int(user.get("is_active", 0)):
        return False
    return int(user.get("tts_enabled", 0)) != 0


def init_db() -> None:
    _ensure_nai_session_timeout_schema()
    conn = _conn(TTS_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at     TEXT NOT NULL,
            employee_id   TEXT NOT NULL,
            username      TEXT DEFAULT '',
            department    TEXT DEFAULT '',
            model         TEXT NOT NULL,
            voice         TEXT NOT NULL,
            char_count    INTEGER DEFAULT 0,
            text_preview  TEXT DEFAULT '',
            style_prompt  TEXT DEFAULT '',
            status        TEXT NOT NULL,
            error_message TEXT DEFAULT '',
            elapsed_ms    INTEGER DEFAULT 0,
            client_ip     TEXT DEFAULT ''
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tts_logs_at ON usage_logs (logged_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tts_logs_emp ON usage_logs (employee_id, logged_at DESC)"
    )
    conn.commit()
    conn.close()


def log_usage(
    *,
    employee_id: str,
    username: str,
    department: str,
    model: str,
    voice: str,
    char_count: int,
    text: str,
    style_prompt: str = "",
    status: str,
    error_message: str = "",
    elapsed_ms: int = 0,
    client_ip: str = "",
) -> int:
    preview = (text or "").strip().replace("\n", " ")[:200]
    conn = _conn(TTS_DB_PATH)
    cur = conn.execute(
        """INSERT INTO usage_logs
           (logged_at, employee_id, username, department, model, voice,
            char_count, text_preview, style_prompt, status, error_message,
            elapsed_ms, client_ip)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            jst_now(), employee_id, username, department, model, voice,
            char_count, preview, (style_prompt or "").strip()[:200],
            status, (error_message or "")[:500], elapsed_ms, client_ip,
        ),
    )
    log_id = cur.lastrowid
    conn.commit()
    conn.close()
    return log_id


def get_user_logs(employee_id: str, limit: int = 20) -> list[dict]:
    conn = _conn(TTS_DB_PATH)
    rows = conn.execute(
        """SELECT * FROM usage_logs
           WHERE employee_id=?
           ORDER BY logged_at DESC LIMIT ?""",
        (employee_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_logs(limit: int = 100, employee_id: str = "") -> list[dict]:
    conn = _conn(TTS_DB_PATH)
    if employee_id.strip():
        rows = conn.execute(
            """SELECT * FROM usage_logs
               WHERE employee_id LIKE ?
               ORDER BY logged_at DESC LIMIT ?""",
            (f"%{employee_id.strip()}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM usage_logs ORDER BY logged_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
