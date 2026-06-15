"""
db.py — SQLite データベース操作層
チャットセッション / メッセージ / クエリログ / ユーザー /
プロンプトテンプレート / フィードバック / 日次利用 / システム設定
"""
import base64
import os
import hashlib
import secrets
import sqlite3
import datetime
from datetime import timezone, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini_ui.db")
JST = timezone(timedelta(hours=9))


def jst_now() -> str:
    return datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def today_jst() -> str:
    return datetime.datetime.now(JST).strftime("%Y-%m-%d")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


# ══════════════════════════════════════════════════════════
# スキーマ初期化
# ══════════════════════════════════════════════════════════

def init_db() -> None:
    conn = get_conn()
    c = conn.cursor()

    # ── ユーザー ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id  TEXT UNIQUE NOT NULL,
            username     TEXT NOT NULL,
            department   TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            is_admin     INTEGER DEFAULT 0,
            daily_limit  INTEGER DEFAULT -1,
            is_active    INTEGER DEFAULT 1,
            created_at   TEXT NOT NULL
        )
    """)

    # ── チャットセッション ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT UNIQUE NOT NULL,
            employee_id TEXT DEFAULT '',
            title       TEXT DEFAULT '新しいチャット',
            model       TEXT DEFAULT 'gemini-2.0-flash',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)

    # ── チャットメッセージ ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            log_id      INTEGER DEFAULT NULL,
            created_at  TEXT NOT NULL
        )
    """)

    # ── クエリログ ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS query_logs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at      TEXT NOT NULL,
            session_id     TEXT NOT NULL,
            employee_id    TEXT DEFAULT '',
            username       TEXT DEFAULT '',
            department     TEXT DEFAULT '',
            model          TEXT NOT NULL,
            system_prompt  TEXT DEFAULT '',
            question       TEXT NOT NULL,
            answer         TEXT NOT NULL,
            has_attachment INTEGER DEFAULT 0,
            used_search    INTEGER DEFAULT 0,
            input_tokens   INTEGER DEFAULT 0,
            output_tokens  INTEGER DEFAULT 0,
            client_ip      TEXT DEFAULT '',
            elapsed_ms     INTEGER DEFAULT 0
        )
    """)

    # ── プロンプトテンプレート ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS prompt_templates (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT UNIQUE NOT NULL,
            category      TEXT DEFAULT '汎用',
            system_prompt TEXT NOT NULL,
            is_active     INTEGER DEFAULT 1,
            sort_order    INTEGER DEFAULT 0,
            created_at    TEXT NOT NULL
        )
    """)

    # ── フィードバック ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            log_id     INTEGER NOT NULL,
            rating     INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # ── 日次利用カウント ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_usage (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT NOT NULL,
            date_jst    TEXT NOT NULL,
            count       INTEGER DEFAULT 0,
            UNIQUE(employee_id, date_jst)
        )
    """)

    # ── システム設定 ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # ── メンテナンスモード実行ログ ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_debug_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id    TEXT NOT NULL,
            logged_at   TEXT NOT NULL,
            employee_id TEXT DEFAULT '',
            session_id  TEXT DEFAULT '',
            phase       TEXT NOT NULL,
            step        TEXT NOT NULL,
            detail_json TEXT DEFAULT '',
            elapsed_ms  INTEGER DEFAULT 0
        )
    """)

    # 安全なインデックス（新規テーブルのみ）
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages (session_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_logs_at ON query_logs (logged_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_feedback_log ON feedback (log_id)",
        "CREATE INDEX IF NOT EXISTS idx_daily_usage ON daily_usage (employee_id, date_jst)",
        "CREATE INDEX IF NOT EXISTS idx_maint_trace ON maintenance_debug_logs (trace_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_maint_at ON maintenance_debug_logs (logged_at DESC)",
    ]:
        try:
            c.execute(stmt)
        except Exception:
            pass

    conn.commit()

    # ── 既存テーブルへの後方互換カラム追加 ──
    migrations = [
        ("chat_sessions",  "employee_id TEXT DEFAULT ''"),
        ("chat_messages",  "log_id INTEGER DEFAULT NULL"),
        ("query_logs",     "employee_id TEXT DEFAULT ''"),
        ("query_logs",     "username TEXT DEFAULT ''"),
        ("query_logs",     "department TEXT DEFAULT ''"),
        ("query_logs",     "has_attachment INTEGER DEFAULT 0"),
        ("query_logs",     "used_search INTEGER DEFAULT 0"),
        # ユーザーごとの権限カラム
        ("users", "web_search_enabled INTEGER DEFAULT -1"),  # -1=グローバル設定, 0=禁止, 1=許可
        ("users", "allowed_models TEXT DEFAULT ''"),          # 空=全モデル, カンマ区切りで制限
        ("users", "plain_password TEXT DEFAULT ''"),          # 管理者参照用プレーンテキスト
        ("users", "default_model TEXT DEFAULT ''"),           # 空=グローバル設定
        ("users", "upload_max_mb INTEGER DEFAULT -1"),        # -1=グローバル, 0=無制限
        ("users", "upload_allowed_types TEXT DEFAULT ''"),    # 空=グローバル
        ("users", "password_change_allowed INTEGER DEFAULT 1"),  # 1=許可, 0=制限
        ("users", "nai_enabled INTEGER DEFAULT 1"),   # 1=NAI利用可
        ("users", "tts_enabled INTEGER DEFAULT 0"),   # 1=TTS利用可
        ("users", "session_timeout_sec INTEGER DEFAULT -1"),  # -1=グローバル, 0=無制限, >0=秒
        ("prompt_templates", "default_model TEXT DEFAULT ''"),
        ("prompt_templates", "allow_empty_prompt INTEGER DEFAULT 0"),
        ("prompt_templates", "template_kind TEXT DEFAULT 'standard'"),
        ("prompt_templates", "handler_config TEXT DEFAULT '{}'"),
    ]
    for table, col_def in migrations:
        col_name = col_def.split()[0]
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            conn.commit()
        except Exception:
            pass  # カラムが既に存在する場合は無視

    # employee_id カラム追加後にインデックスを作成
    conn2 = get_conn()
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_sessions_emp ON chat_sessions (employee_id, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_logs_emp ON query_logs (employee_id)",
    ]:
        try:
            conn2.execute(stmt)
        except Exception:
            pass
    conn2.commit()
    conn2.close()

    _seed_default_settings()
    _seed_default_templates()
    ensure_core_templates()
    _migrate_template_kinds()
    ensure_media_templates()
    ensure_excel_extra_template()
    ensure_web_research_templates()
    _migrate_upload_audio_types()
    _migrate_upload_office_types()
    _migrate_audio_template_empty_prompt()
    _migrate_template_ids_from_one()
    _migrate_password_refs()
    _migrate_admin_app_access()


def _migrate_admin_app_access() -> None:
    """管理者は TTS 利用も許可（既存管理者の後方互換）"""
    conn = get_conn()
    conn.execute(
        "UPDATE users SET tts_enabled=1 WHERE is_admin=1 AND COALESCE(tts_enabled, 0)=0"
    )
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════
# パスワードユーティリティ
# ══════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"{salt}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, key_hex = stored.split("$", 1)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


# ── 管理者参照用パスワード（Fernet 暗号化） ─────────────

_ENC_PREFIX = "enc:v1:"


def _password_ref_fernet():
    from cryptography.fernet import Fernet

    secret = (
        os.getenv("PASSWORD_REF_KEY")
        or os.getenv("ADMIN_PASSWORD")
        or "change-me"
    ).encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt_password_ref(plain: str) -> str:
    """管理者参照用パスワードを暗号化して DB 保存形式にする"""
    if not plain:
        return ""
    token = _password_ref_fernet().encrypt(plain.encode()).decode()
    return f"{_ENC_PREFIX}{token}"


def decrypt_password_ref(stored: str) -> str:
    """DB 値を復号（レガシー平文はそのまま返す）"""
    if not stored:
        return ""
    if stored.startswith(_ENC_PREFIX):
        try:
            token = stored[len(_ENC_PREFIX):].encode()
            return _password_ref_fernet().decrypt(token).decode()
        except Exception:
            return ""
    return stored


def _migrate_password_refs() -> None:
    """既存の平文 plain_password を暗号化形式へ移行"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT employee_id, plain_password FROM users WHERE plain_password != ''"
    ).fetchall()
    for row in rows:
        pw = row["plain_password"]
        if pw and not pw.startswith(_ENC_PREFIX):
            conn.execute(
                "UPDATE users SET plain_password=? WHERE employee_id=?",
                (encrypt_password_ref(pw), row["employee_id"]),
            )
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════
# システム設定
# ══════════════════════════════════════════════════════════

DEFAULTS: dict[str, str] = {
    "model":               "gemini-3.5-flash",
    "temperature":         "0.7",
    "max_output_tokens":   "8192",
    "system_prompt":       "あなたは社内業務をサポートするAIアシスタントです。\n丁寧かつ正確な回答を日本語で提供してください。\n必要に応じてMarkdown形式を使って見やすく整形してください。",
    "daily_limit_default": "50",
    "web_search_allowed":  "1",
    "upload_max_mb_default":      "50",
    "upload_allowed_types_default": "jpg,jpeg,png,gif,webp,pdf,txt,csv,md,xlsx,docx,pptx,mp3,wav,aac,flac,m4a,ogg",
    "session_timeout_default": "600",  # 秒（10分）。0=無制限
    "maintenance_mode": "0",  # 1=メンテナンス（実行プロセスログ記録）
}


def _seed_default_settings() -> None:
    conn = get_conn()
    for k, v in DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
        )
    conn.commit()
    conn.close()


def get_setting(key: str) -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else DEFAULTS.get(key, "")


def set_setting(key: str, value: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_all_settings() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    result = dict(DEFAULTS)
    result.update({r["key"]: r["value"] for r in rows})
    return result


def maintenance_mode_enabled() -> bool:
    return get_setting("maintenance_mode") == "1"


def get_user_execution_snapshot(employee_id: str) -> dict:
    """メンテナンスログ用 — 実行時点のユーザー許可設定（機密情報は含めない）"""
    user = get_user(employee_id)
    if not user:
        return {"employee_id": employee_id, "user_found": False}

    allowed_list = get_user_allowed_models(employee_id)
    allowed_raw = (user.get("allowed_models") or "").strip()
    ws_user = int(user.get("web_search_enabled", -1))
    ws_map = {-1: "グローバル設定に従う", 0: "禁止", 1: "許可"}
    daily_user = int(user.get("daily_limit", -1))

    return {
        "employee_id": employee_id,
        "username": user.get("username") or "",
        "department": user.get("department") or "",
        "is_admin": bool(user.get("is_admin")),
        "is_active": bool(user.get("is_active")),
        "allowed_models": allowed_list if allowed_list else "（全モデル）",
        "allowed_models_raw": allowed_raw or "（空=全モデル）",
        "default_model_user": (user.get("default_model") or "").strip() or "（グローバル）",
        "effective_default_model": get_effective_default_model(employee_id),
        "web_search_user_setting": ws_map.get(ws_user, str(ws_user)),
        "web_search_effective": get_effective_web_search_allowed(employee_id),
        "daily_limit_user": daily_user if daily_user >= 0 else "（グローバル）",
        "daily_limit_effective": get_effective_daily_limit(employee_id),
        "upload_limit_label": format_user_upload_limit(employee_id, user),
        "upload_types_effective": get_effective_upload_types(employee_id),
        "session_timeout_label": format_session_timeout_label(
            get_effective_session_timeout_sec(employee_id),
        ),
        "password_change_allowed": bool(int(user.get("password_change_allowed", 1))),
        "nai_enabled": bool(int(user.get("nai_enabled", 1))),
        "tts_enabled": bool(int(user.get("tts_enabled", 0))),
    }


def insert_maintenance_log(
    *,
    trace_id: str,
    employee_id: str = "",
    session_id: str = "",
    phase: str,
    step: str,
    detail_json: str = "",
    elapsed_ms: int = 0,
) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO maintenance_debug_logs"
        " (trace_id, logged_at, employee_id, session_id, phase, step, detail_json, elapsed_ms)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            trace_id, jst_now(), employee_id or "", session_id or "",
            phase, step, detail_json or "", max(0, int(elapsed_ms)),
        ),
    )
    conn.commit()
    conn.close()


def purge_maintenance_logs() -> int:
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM maintenance_debug_logs").fetchone()[0]
    conn.execute("DELETE FROM maintenance_debug_logs")
    conn.commit()
    conn.close()
    return int(count or 0)


def count_maintenance_logs() -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) FROM maintenance_debug_logs").fetchone()
    conn.close()
    return int(row[0] if row else 0)


def list_maintenance_traces(limit: int = 100) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT trace_id,
               MIN(logged_at) AS started_at,
               MAX(logged_at) AS ended_at,
               MAX(employee_id) AS employee_id,
               MAX(session_id) AS session_id,
               COUNT(*) AS step_count
        FROM maintenance_debug_logs
        GROUP BY trace_id
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_maintenance_logs_for_trace(trace_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, logged_at, phase, step, detail_json, elapsed_ms"
        " FROM maintenance_debug_logs WHERE trace_id=?"
        " ORDER BY id",
        (trace_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════
# ユーザー管理
# ══════════════════════════════════════════════════════════

def ensure_admin_user(admin_password: str) -> None:
    """管理者アカウント admin が存在しない場合は初期アカウントを作成する"""
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM users WHERE employee_id=?", ("admin",)
    ).fetchone()
    conn.close()
    if row is None:
        create_user(
            employee_id="admin",
            username="管理者",
            department="システム管理",
            password=admin_password,
            is_admin=True,
            daily_limit=0,
            nai_enabled=1,
            tts_enabled=1,
        )


def create_user(
    employee_id: str,
    username: str,
    department: str,
    password: str,
    is_admin: bool = False,
    daily_limit: int = -1,
    web_search_enabled: int = -1,
    allowed_models: str = "",
    upload_max_mb: int = -1,
    upload_allowed_types: str = "",
    password_change_allowed: int = 1,
    nai_enabled: int = 1,
    tts_enabled: int = 0,
    session_timeout_sec: int = -1,
) -> bool:
    try:
        conn = get_conn()
        if is_admin:
            nai_enabled = 1
            tts_enabled = 1
        conn.execute(
            """INSERT INTO users
               (employee_id, username, department, password_hash, plain_password,
                is_admin, daily_limit, web_search_enabled, allowed_models,
                upload_max_mb, upload_allowed_types, password_change_allowed,
                nai_enabled, tts_enabled, session_timeout_sec, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (employee_id, username, department,
             hash_password(password), encrypt_password_ref(password),
             1 if is_admin else 0, daily_limit,
             web_search_enabled, allowed_models,
             upload_max_mb, upload_allowed_types, password_change_allowed,
             nai_enabled, tts_enabled, session_timeout_sec, jst_now()),
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False


def authenticate_user(employee_id: str, password: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE employee_id=? AND is_active=1", (employee_id,)
    ).fetchone()
    conn.close()
    if row and verify_password(password, row["password_hash"]):
        return dict(row)
    return None


def get_user(employee_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE employee_id=?", (employee_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_users() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, employee_id, username, department, is_admin, daily_limit,
                  is_active, web_search_enabled, allowed_models, plain_password,
                  default_model, upload_max_mb, upload_allowed_types,
                  password_change_allowed, nai_enabled, tts_enabled,
                  session_timeout_sec, created_at
           FROM users ORDER BY created_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def user_can_access_nai(employee_id: str) -> bool:
    user = get_user(employee_id)
    if not user or not int(user.get("is_active", 0)):
        return False
    return int(user.get("nai_enabled", 1)) != 0


def user_can_access_tts(employee_id: str) -> bool:
    user = get_user(employee_id)
    if not user or not int(user.get("is_active", 0)):
        return False
    return int(user.get("tts_enabled", 0)) != 0


def update_user(
    employee_id: str,
    username: str = None,
    department: str = None,
    is_admin: int = None,
    daily_limit: int = None,
    is_active: int = None,
    new_password: str = None,
    web_search_enabled: int = None,
    allowed_models: str = None,
    default_model: str = None,
    upload_max_mb: int = None,
    upload_allowed_types: str = None,
    password_change_allowed: int = None,
    nai_enabled: int = None,
    tts_enabled: int = None,
    session_timeout_sec: int = None,
) -> None:
    conn = get_conn()
    fields, params = [], []
    if username is not None:
        fields.append("username=?"); params.append(username)
    if department is not None:
        fields.append("department=?"); params.append(department)
    if is_admin is not None:
        fields.append("is_admin=?"); params.append(is_admin)
    if daily_limit is not None:
        fields.append("daily_limit=?"); params.append(daily_limit)
    if is_active is not None:
        fields.append("is_active=?"); params.append(is_active)
    if web_search_enabled is not None:
        fields.append("web_search_enabled=?"); params.append(web_search_enabled)
    if allowed_models is not None:
        fields.append("allowed_models=?"); params.append(allowed_models)
    if default_model is not None:
        fields.append("default_model=?"); params.append(default_model)
    if upload_max_mb is not None:
        fields.append("upload_max_mb=?"); params.append(upload_max_mb)
    if upload_allowed_types is not None:
        fields.append("upload_allowed_types=?"); params.append(upload_allowed_types)
    if password_change_allowed is not None:
        fields.append("password_change_allowed=?"); params.append(password_change_allowed)
    if nai_enabled is not None:
        fields.append("nai_enabled=?"); params.append(nai_enabled)
    if tts_enabled is not None:
        fields.append("tts_enabled=?"); params.append(tts_enabled)
    if session_timeout_sec is not None:
        fields.append("session_timeout_sec=?"); params.append(session_timeout_sec)
    if new_password is not None and new_password != "":
        fields.append("password_hash=?"); params.append(hash_password(new_password))
        fields.append("plain_password=?"); params.append(encrypt_password_ref(new_password))
    if fields:
        params.append(employee_id)
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE employee_id=?", params)
        conn.commit()
    conn.close()


def user_can_change_password(employee_id: str) -> bool:
    """利用者自身によるパスワード変更が許可されているか"""
    user = get_user(employee_id)
    if not user:
        return False
    return int(user.get("password_change_allowed", 1)) != 0


def change_password(employee_id: str, new_password: str) -> None:
    """ユーザー自身によるパスワード変更（管理者参照用プレーンテキストも更新）"""
    if not user_can_change_password(employee_id):
        raise ValueError("このアカウントではパスワード変更が制限されています。")
    conn = get_conn()
    conn.execute(
        "UPDATE users SET password_hash=?, plain_password=? WHERE employee_id=?",
        (hash_password(new_password), encrypt_password_ref(new_password), employee_id),
    )
    conn.commit()
    conn.close()


def delete_user(employee_id: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE employee_id=?", (employee_id,))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════
# 日次利用制限
# ══════════════════════════════════════════════════════════

def get_daily_count(employee_id: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT count FROM daily_usage WHERE employee_id=? AND date_jst=?",
        (employee_id, today_jst()),
    ).fetchone()
    conn.close()
    return row["count"] if row else 0


def increment_daily_count(employee_id: str) -> int:
    conn = get_conn()
    conn.execute(
        """INSERT INTO daily_usage (employee_id, date_jst, count) VALUES (?, ?, 1)
           ON CONFLICT(employee_id, date_jst) DO UPDATE SET count=count+1""",
        (employee_id, today_jst()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT count FROM daily_usage WHERE employee_id=? AND date_jst=?",
        (employee_id, today_jst()),
    ).fetchone()
    conn.close()
    return row["count"] if row else 1


def get_effective_daily_limit(employee_id: str) -> int:
    """
    ユーザー固有設定(-1=未設定)の場合はグローバルデフォルトを返す。
    0 = 無制限。
    """
    user = get_user(employee_id)
    if user and user["daily_limit"] >= 0:
        return user["daily_limit"]
    return int(get_setting("daily_limit_default") or 50)


def format_session_timeout_label(seconds: int) -> str:
    """セッションタイムアウト秒数の表示用ラベル"""
    if seconds <= 0:
        return "無制限"
    if seconds < 60:
        return f"{seconds}秒"
    minutes, rem = divmod(seconds, 60)
    if rem:
        return f"{minutes}分{rem}秒"
    return f"{minutes}分"


def get_effective_session_timeout_sec(employee_id: str) -> int:
    """
    セッションタイムアウト秒数。
    ユーザー固有: -1=グローバル設定, 0=無制限, >0=秒数
    """
    user = get_user(employee_id)
    if user:
        val = int(user.get("session_timeout_sec", -1))
        if val >= 0:
            return val
    return int(get_setting("session_timeout_default") or 600)


def format_user_session_timeout(employee_id: str, user: dict | None = None) -> str:
    """ユーザーに適用されるセッションタイムアウトの説明文"""
    user = user or get_user(employee_id) or {}
    effective = get_effective_session_timeout_sec(employee_id)
    own = int(user.get("session_timeout_sec", -1))
    if own >= 0:
        return format_session_timeout_label(own)
    global_sec = int(get_setting("session_timeout_default") or 600)
    return f"{format_session_timeout_label(global_sec)}（グローバル設定）"


def get_effective_web_search_allowed(employee_id: str) -> bool:
    """
    ユーザー固有設定:
      1  = 許可（グローバル設定に関わらず）
      0  = 禁止
     -1  = グローバル設定に従う
    """
    user = get_user(employee_id)
    if user:
        val = user.get("web_search_enabled", -1)
        if val == 1:
            return True
        if val == 0:
            return False
    return get_setting("web_search_allowed") == "1"


def get_user_allowed_models(employee_id: str) -> list[str]:
    """
    空リスト = 全モデル使用可。
    それ以外は許可モデルのリスト。
    """
    user = get_user(employee_id)
    if user and user.get("allowed_models", "").strip():
        return [m.strip() for m in user["allowed_models"].split(",") if m.strip()]
    return []


def get_effective_default_model(employee_id: str) -> str:
    """ユーザー固有 → グローバル設定の順でデフォルトモデルを返す"""
    user = get_user(employee_id)
    if user and user.get("default_model", "").strip():
        return user["default_model"].strip()
    return get_setting("model") or DEFAULTS["model"]


def format_user_default_model_label(
    employee_id: str, model_labels: dict[str, str] | None = None,
) -> str:
    """ユーザー管理・チャット共通のデフォルト LLM 表示ラベル"""
    user = get_user(employee_id)
    raw = (user.get("default_model") or "").strip() if user else ""
    eff = get_effective_default_model(employee_id)
    if raw:
        return model_labels.get(raw, raw) if model_labels else raw
    eff_label = model_labels.get(eff, eff) if model_labels else eff
    return f"グローバル（{eff_label}）"


def _pick_fallback_model(available: set[str]) -> str:
    for m in ("gemini-3.5-flash", "gemini-2.5-flash", "gemini-3.1-flash-lite"):
        if m in available:
            return m
    return next(iter(available)) if available else DEFAULTS["model"]


def sync_llm_settings_with_available(available_model_ids: set[str]) -> dict:
    """
    利用不可になったモデル参照を DB から除去する。
    - users.default_model → 空（グローバル設定に従う）
    - users.allowed_models → 利用可能なもののみ残す
    - prompt_templates.default_model → 空
    - settings.model → フォールバックモデル
    """
    summary: dict = {
        "users_default_reset": [],
        "users_allowed_trimmed": [],
        "templates_default_reset": [],
        "global_model_changed": None,
    }
    conn = get_conn()
    c = conn.cursor()

    for row in c.execute(
        "SELECT employee_id, default_model, allowed_models FROM users"
    ).fetchall():
        emp = row["employee_id"]
        dm = (row["default_model"] or "").strip()
        if dm and dm not in available_model_ids:
            c.execute(
                "UPDATE users SET default_model=? WHERE employee_id=?",
                ("", emp),
            )
            summary["users_default_reset"].append(emp)

        am_raw = (row["allowed_models"] or "").strip()
        if am_raw:
            kept = [m.strip() for m in am_raw.split(",") if m.strip() in available_model_ids]
            new_am = ",".join(kept)
            if new_am != am_raw:
                c.execute(
                    "UPDATE users SET allowed_models=? WHERE employee_id=?",
                    (new_am, emp),
                )
                summary["users_allowed_trimmed"].append(emp)

    for row in c.execute(
        "SELECT id, name, default_model FROM prompt_templates"
    ).fetchall():
        dm = (row["default_model"] or "").strip()
        if dm and dm not in available_model_ids:
            c.execute(
                "UPDATE prompt_templates SET default_model=? WHERE id=?",
                ("", row["id"]),
            )
            summary["templates_default_reset"].append(row["name"])

    global_row = c.execute(
        "SELECT value FROM settings WHERE key=?", ("model",)
    ).fetchone()
    global_model = (global_row["value"] if global_row else DEFAULTS["model"]).strip()
    if global_model and global_model not in available_model_ids:
        fallback = _pick_fallback_model(available_model_ids)
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("model", fallback),
        )
        summary["global_model_changed"] = {"from": global_model, "to": fallback}

    conn.commit()
    conn.close()
    return summary


def get_effective_upload_max_mb(employee_id: str) -> int:
    """ユーザー固有 → グローバル設定。0=無制限"""
    user = get_user(employee_id)
    if user and user.get("upload_max_mb", -1) >= 0:
        return int(user["upload_max_mb"])
    return int(get_setting("upload_max_mb_default") or 50)


def format_upload_limit_mb(mb: int) -> str:
    if mb <= 0:
        return "無制限"
    return f"{mb} MB"


def format_user_upload_limit(employee_id: str, user: dict | None = None) -> str:
    """ユーザー一覧表示用の添付容量ラベル"""
    row = user or get_user(employee_id)
    user_mb = int(row.get("upload_max_mb", -1)) if row else -1
    if user_mb >= 0:
        return format_upload_limit_mb(user_mb)
    global_mb = int(get_setting("upload_max_mb_default") or 50)
    return f"グローバル（{format_upload_limit_mb(global_mb)}）"


def get_effective_upload_types(employee_id: str) -> list[str]:
    user = get_user(employee_id)
    if user and user.get("upload_allowed_types", "").strip():
        return [t.strip().lower() for t in user["upload_allowed_types"].split(",") if t.strip()]
    raw = get_setting("upload_allowed_types_default") or DEFAULTS["upload_allowed_types_default"]
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def get_template_by_id(tid: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM prompt_templates WHERE id=?", (tid,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ══════════════════════════════════════════════════════════
# プロンプトテンプレート
# ══════════════════════════════════════════════════════════

_DEFAULT_TEMPLATES = [
    ("汎用アシスタント", "汎用",
     "あなたは社内業務をサポートするAIアシスタントです。\n丁寧かつ正確な回答を日本語で提供してください。\nMarkdown形式を活用して見やすく整形してください。", 0),
    ("翻訳（日本語→英語）", "翻訳",
     "あなたはプロの翻訳者です。入力された日本語を自然で正確な英語に翻訳してください。\n訳文のみを出力し、説明や注釈は不要です。", 1),
    ("翻訳（英語→日本語）", "翻訳",
     "あなたはプロの翻訳者です。入力された英語を自然で正確な日本語に翻訳してください。\n訳文のみを出力し、説明や注釈は不要です。", 2),
    ("文章要約", "要約",
     "あなたはテキスト要約の専門家です。入力された文章を簡潔に要約してください。\n要点を箇条書きで整理し、重要な数字や固有名詞は正確に残してください。", 3),
    ("コードレビュー", "開発",
     "あなたはシニアソフトウェアエンジニアです。提供されたコードをレビューし、\n品質・パフォーマンス・セキュリティの観点から改善点を指摘してください。\n改善例も合わせてコードブロックで示してください。", 4),
    ("文書作成支援", "文書",
     "あなたはビジネス文書作成の専門家です。指示に従い、正式なビジネス文書・報告書・提案書等を\n日本のビジネス慣習に則った敬語・フォーマットで作成してください。", 5),
    ("メール文章作成", "文書",
     "あなたはビジネスメール作成の専門家です。指示に従い、簡潔かつ丁寧なビジネスメールを作成してください。\n件名・宛名・本文・署名の形式で出力してください。", 6),
    ("データ分析支援", "分析",
     "あなたはデータアナリストです。提供されたデータや数値を分析し、\n傾向・課題・改善提案を論理的に説明してください。\n必要に応じて表や数式を使って説明してください。", 7),
]


def _seed_default_templates() -> None:
    """初回のみデフォルトテンプレートを投入（削除後の再投入はしない）"""
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM prompt_templates").fetchone()[0]
    if count > 0:
        conn.close()
        return
    for i, (name, cat, prompt, order) in enumerate(_DEFAULT_TEMPLATES, start=1):
        conn.execute(
            """INSERT INTO prompt_templates
               (id, name, category, system_prompt, is_active, sort_order, created_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (i, name, cat, prompt, order, jst_now()),
        )
    _sync_template_id_sequence(conn)
    conn.commit()
    conn.close()


_MEDIA_TEMPLATES = [
    (
        "イメージデータ生成", "画像",
        "あなたは画像生成の専門アシスタントです。\n"
        "ユーザーが日本語で説明した内容に基づき、高品質な画像を生成してください。\n"
        "生成後は画像の簡潔な説明を日本語で添えてください。\n"
        "社内利用にふさわしい内容のみ生成し、不適切な要求は丁寧に断ってください。",
        14, "gemini-2.5-flash-image", 0,
    ),
    (
        "音声文字起こし", "音声",
        "あなたは音声文字起こしの専門家です。\n"
        "添付された音声データを正確に文字起こししてください。\n"
        "省略や要約はせず、聞こえた通りにテキスト化してください。\n"
        "話者が複数いる場合は、話者A・話者Bのように改行して読みやすく整形してください。",
        15, "gemini-3.5-flash", 1,
    ),
    (
        "議事録作成", "音声",
        "あなたは議事録作成の専門家です。\n"
        "添付された音声（会議・打合せの録音）を文字起こしし、以下の形式で議事録を作成してください。\n\n"
        "## 議事録\n"
        "- **日時**：（音声から推測できる場合のみ記載）\n"
        "- **参加者**：（分かる範囲で）\n"
        "- **議題**：（要約）\n\n"
        "### 議事内容\n"
        "（時系列で要点を整理）\n\n"
        "### 決定事項\n"
        "（箇条書き）\n\n"
        "### アクションアイテム\n"
        "（担当者・期限が分かる場合は併記）\n\n"
        "聞き取れない部分は「（聞き取り不明）」と明記し、推測で補完しないでください。",
        16, "gemini-3.5-flash", 1,
    ),
]


_EXCEL_EXTRA_TEMPLATE = (
    "Excel集計・分析",
    "Excel",
    "Excel データを pandas で加工した結果に基づき、"
    "分析・傾向・インサイト・提言を日本語で提供する。",
    17,
    "gemini-3.1-pro-preview",
    0,
)


def ensure_excel_extra_template() -> None:
    """Excel 集計・分析テンプレート（excel_extra kind）を欠落時のみ追加"""
    import template_registry as tr

    name, cat, prompt, order, default_model, allow_empty = _EXCEL_EXTRA_TEMPLATE
    conn = get_conn()
    row = conn.execute(
        "SELECT id, name FROM prompt_templates WHERE template_kind=?",
        (tr.KIND_EXCEL_EXTRA,),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE prompt_templates SET name=?, category=?, default_model=?,"
            " template_kind=? WHERE id=?",
            (name, cat, default_model, tr.KIND_EXCEL_EXTRA, row["id"]),
        )
        conn.commit()
        conn.close()
        return
    by_name = conn.execute(
        "SELECT id FROM prompt_templates WHERE name=?", (name,),
    ).fetchone()
    if by_name:
        conn.execute(
            "UPDATE prompt_templates SET category=?, default_model=?,"
            " template_kind=?, is_active=1 WHERE id=?",
            (cat, default_model, tr.KIND_EXCEL_EXTRA, by_name["id"]),
        )
        conn.commit()
        conn.close()
        return
    template_id = _next_template_id(conn)
    conn.execute(
        """INSERT INTO prompt_templates
           (id, name, category, system_prompt, is_active, sort_order,
            default_model, allow_empty_prompt, template_kind, handler_config,
            created_at)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
        (
            template_id, name, cat, prompt, order, default_model, allow_empty,
            tr.KIND_EXCEL_EXTRA, tr.handler_config_json({}), jst_now(),
        ),
    )
    _sync_template_id_sequence(conn)
    conn.commit()
    conn.close()


_WEB_RESEARCH_DEFAULT_MODEL = "gemini-3.5-flash"

_WEB_RESEARCH_TEMPLATES = [
    (
        "資料×Web比較調査",
        "調査",
        "あなたは社内調査・リサーチの専門アシスタントです。\n"
        "ユーザーが添付した資料（PDF / Word / Excel / テキスト等）を"
        "一次情報として精読し、サイドバーの「最新情報をWeb検索」が"
        "有効な場合は Google 検索で公開情報を調べ、両方を突き合わせて"
        "回答してください。\n\n"
        "【回答のルール】\n"
        "1. 資料の内容と Web 情報は明確に区別する"
        "（「添付資料より」「Web検索より」など）\n"
        "2. Web 情報は可能な限り出典・時点を示す\n"
        "3. 資料や検索結果にない事実は推測で補完しない\n"
        "4. 差分・ギャップ・リスク・次のアクションを整理する\n"
        "5. 比較表や箇条書きで読みやすくまとめる\n\n"
        "添付がない場合は、Web 検索とプロンプトのみで回答してください。",
        12,
    ),
    (
        "競合・市場調査（資料付き）",
        "調査",
        "あなたは競合分析・市場調査の専門アシスタントです。\n"
        "添付資料（自社製品資料・仕様書・社内メモ等）を前提に、"
        "Web 検索が有効なときは競合製品・価格帯・市場動向・"
        "直近ニュースを調べ、比較分析を日本語で提供してください。\n\n"
        "【出力構成（推奨）】\n"
        "- 調査サマリー（3〜5行）\n"
        "- 自社・対象（添付資料ベース）\n"
        "- 競合・市場（Web ベース、出典付き）\n"
        "- 比較（強み・弱み・差別化ポイント）\n"
        "- 示唆・提言\n\n"
        "数値は根拠のあるもののみ記載し、不確実な点はその旨を明記してください。",
        13,
    ),
    (
        "規程・法令チェック",
        "コンプライアンス",
        "あなたは社内規程と法規制の整合性を確認するアシスタントです。\n"
        "添付された社内規程・マニュアル・契約書案等を読み、"
        "Web 検索が有効なときは関連する法令・ガイドライン・"
        "行政通達の最新情報を調べ、抵触・更新が必要な箇所を"
        "指摘してください。\n\n"
        "【注意】\n"
        "- 法的助言の最終判断は専門家に委ねる旨を冒頭で述べる\n"
        "- 条文・規程の該当箇所を引用しつつ、Web 情報は出典を示す\n"
        "- 確信が持てない解釈は「要確認」と明記する\n"
        "- 優先度（高・中・低）付きの修正提案リストを末尾に付ける",
        14,
    ),
    (
        "提案書レビュー（外部情報付き）",
        "文書",
        "あなたはビジネス提案書のレビュー専門家です。\n"
        "添付の提案書・企画書・見積関連資料を読み、"
        "Web 検索が有効なときは顧客・業界・競合・"
        "公開されている統計や事例を調べ、提案内容の"
        "説得力・正確性・リスクをレビューしてください。\n\n"
        "【レビュー観点】\n"
        "- 事実関係（Web で裏取りできる主張）\n"
        "- 顧客・業界への適合性\n"
        "- 論理構成・訴求ポイント\n"
        "- 不足情報・曖昧な表現\n"
        "- 改善提案（具体的な文言案があれば尚よい）\n\n"
        "添付資料の機密内容は要約に留め、過度に外部に漏らす表現は避けてください。",
        15,
    ),
    (
        "技術仕様調査（資料＋Web）",
        "開発",
        "あなたは技術調査・仕様比較の専門アシスタントです。\n"
        "添付の技術資料・仕様書・API ドキュメント・"
        "設計メモを読み、Web 検索が有効なときは"
        "公式ドキュメント・リリースノート・標準仕様・"
        "既知の制限事項を調べ、統合した技術回答を"
        "日本語で提供してください。\n\n"
        "【回答のルール】\n"
        "- バージョン・日付・出典を可能な限り明記\n"
        "- 添付資料と Web 情報の矛盾があれば指摘する\n"
        "- 実装時の注意点・互換性・セキュリティを含める\n"
        "- コード例は必要最小限にし、正確性を優先する",
        16,
    ),
]


def ensure_web_research_templates() -> None:
    """添付 + Web 検索向けテンプレートを欠落時のみ追加・既存はプロンプト同期"""
    import template_registry as tr

    conn = get_conn()
    for name, cat, prompt, order in _WEB_RESEARCH_TEMPLATES:
        row = conn.execute(
            "SELECT id FROM prompt_templates WHERE name=?", (name,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE prompt_templates SET category=?, system_prompt=?,"
                " default_model=?, sort_order=?, template_kind=?, is_active=1"
                " WHERE id=?",
                (
                    cat, prompt, _WEB_RESEARCH_DEFAULT_MODEL, order,
                    tr.KIND_STANDARD, row["id"],
                ),
            )
            continue
        template_id = _next_template_id(conn)
        conn.execute(
            """INSERT INTO prompt_templates
               (id, name, category, system_prompt, is_active, sort_order,
                default_model, allow_empty_prompt, template_kind, handler_config,
                created_at)
               VALUES (?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?)""",
            (
                template_id, name, cat, prompt, order,
                _WEB_RESEARCH_DEFAULT_MODEL, tr.KIND_STANDARD,
                tr.handler_config_json({}), jst_now(),
            ),
        )
    _sync_template_id_sequence(conn)
    conn.commit()
    conn.close()


def ensure_media_templates() -> None:
    """画像生成・音声文字起こし・議事録テンプレートを欠落時のみ追加"""
    import template_registry as tr

    kind_by_category = {"画像": tr.KIND_IMAGE, "音声": tr.KIND_AUDIO}
    conn = get_conn()
    for name, cat, prompt, order, default_model, allow_empty in _MEDIA_TEMPLATES:
        if conn.execute("SELECT 1 FROM prompt_templates WHERE name=?", (name,)).fetchone():
            continue
        template_id = _next_template_id(conn)
        kind = kind_by_category.get(cat, tr.KIND_STANDARD)
        conn.execute(
            """INSERT INTO prompt_templates
               (id, name, category, system_prompt, is_active, sort_order,
                default_model, allow_empty_prompt, template_kind, handler_config,
                created_at)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
            (
                template_id, name, cat, prompt, order, default_model, allow_empty,
                kind, tr.handler_config_json({}), jst_now(),
            ),
        )
    _sync_template_id_sequence(conn)
    conn.commit()
    conn.close()


def _migrate_template_kinds() -> None:
    """名称・カテゴリから template_kind を推定し、Excel 表示名を統一"""
    if get_setting("_template_kinds_migrated") == "1":
        return
    import template_registry as tr

    conn = get_conn()
    rows = conn.execute("SELECT * FROM prompt_templates").fetchall()
    excel_extra_ids: list[int] = []
    for row in rows:
        tmpl = dict(row)
        kind = tr.infer_kind_from_legacy(tmpl)
        existing_kind = (tmpl.get("template_kind") or "").strip()
        if not existing_kind or existing_kind == tr.KIND_STANDARD:
            conn.execute(
                "UPDATE prompt_templates SET template_kind=? WHERE id=?",
                (kind, tmpl["id"]),
            )
        elif existing_kind:
            kind = tr.normalize_kind(existing_kind)
        if kind == tr.KIND_EXCEL_EXTRA:
            excel_extra_ids.append(int(tmpl["id"]))

    if excel_extra_ids:
        primary_id = min(excel_extra_ids)
        for dup_id in excel_extra_ids:
            if dup_id != primary_id:
                conn.execute("DELETE FROM prompt_templates WHERE id=?", (dup_id,))
        name, cat, _prompt, _order, default_model, _allow = _EXCEL_EXTRA_TEMPLATE
        conn.execute(
            "UPDATE prompt_templates SET name=?, category=?, default_model=?,"
            " template_kind=?, is_active=1 WHERE id=?",
            (name, cat, default_model, tr.KIND_EXCEL_EXTRA, primary_id),
        )

    conn.commit()
    conn.close()
    set_setting("_template_kinds_migrated", "1")


def _migrate_audio_template_empty_prompt() -> None:
    """音声系テンプレートに空プロンプト実行を一度だけ有効化"""
    if get_setting("_audio_empty_prompt_migrated") == "1":
        return
    conn = get_conn()
    for name in ("音声文字起こし", "議事録作成"):
        conn.execute(
            "UPDATE prompt_templates SET allow_empty_prompt=1 WHERE name=?",
            (name,),
        )
    conn.execute(
        "UPDATE prompt_templates SET allow_empty_prompt=1 WHERE template_kind=?",
        ("audio",),
    )
    conn.commit()
    conn.close()
    set_setting("_audio_empty_prompt_migrated", "1")


def template_allows_empty_prompt(tmpl: dict | None) -> bool:
    return bool(tmpl and int(tmpl.get("allow_empty_prompt") or 0) == 1)


def _migrate_upload_audio_types() -> None:
    """既存 DB の許可形式に音声拡張子を追加（未設定時のみ）"""
    if get_setting("_upload_audio_types_migrated") == "1":
        return
    raw = get_setting("upload_allowed_types_default") or DEFAULTS["upload_allowed_types_default"]
    existing = {t.strip().lower() for t in raw.split(",") if t.strip()}
    audio_exts = ["mp3", "wav", "aac", "flac", "m4a", "ogg"]
    merged = list(dict.fromkeys(
        [t.strip().lower() for t in raw.split(",") if t.strip()] + audio_exts
    ))
    if set(audio_exts) - existing:
        set_setting("upload_allowed_types_default", ",".join(merged))
    set_setting("_upload_audio_types_migrated", "1")


def _migrate_upload_office_types() -> None:
    """既存 DB の許可形式に Office 拡張子を追加"""
    if get_setting("_upload_office_types_migrated") == "1":
        return
    raw = get_setting("upload_allowed_types_default") or DEFAULTS["upload_allowed_types_default"]
    existing = {t.strip().lower() for t in raw.split(",") if t.strip()}
    office_exts = ["xlsx", "docx", "pptx"]
    merged = list(dict.fromkeys(
        [t.strip().lower() for t in raw.split(",") if t.strip()] + office_exts
    ))
    if set(office_exts) - existing:
        set_setting("upload_allowed_types_default", ",".join(merged))
    set_setting("_upload_office_types_migrated", "1")


def ensure_core_templates() -> None:
    """汎用テンプレートが欠けていれば復元（他の削除済みテンプレートは再投入しない）"""
    core_name = _DEFAULT_TEMPLATES[0][0]
    conn = get_conn()
    if conn.execute("SELECT 1 FROM prompt_templates WHERE name=?", (core_name,)).fetchone():
        conn.close()
        return
    name, cat, prompt, order = _DEFAULT_TEMPLATES[0]
    new_id = _next_template_id(conn)
    conn.execute(
        """INSERT INTO prompt_templates
           (id, name, category, system_prompt, is_active, sort_order, default_model, created_at)
           VALUES (?, ?, ?, ?, 1, ?, '', ?)""",
        (new_id, name, cat, prompt, order, jst_now()),
    )
    _sync_template_id_sequence(conn)
    conn.commit()
    conn.close()


def _next_template_id(conn: sqlite3.Connection) -> int:
    """次に採番するテンプレート ID（既存最大 ID + 1）"""
    max_id = conn.execute("SELECT MAX(id) FROM prompt_templates").fetchone()[0]
    return (max_id or 0) + 1


def renumber_templates_from_one() -> None:
    """全テンプレート ID を sort_order, id 順で 1 から振り直す"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM prompt_templates ORDER BY sort_order, id"
    ).fetchall()
    if not rows:
        conn.close()
        return

    old_ids = [r["id"] for r in rows]
    if old_ids == list(range(1, len(old_ids) + 1)):
        conn.close()
        return

    for i, old_id in enumerate(old_ids, start=1):
        conn.execute(
            "UPDATE prompt_templates SET id=? WHERE id=?",
            (1_000_000 + i, old_id),
        )
    for i in range(1, len(old_ids) + 1):
        conn.execute(
            "UPDATE prompt_templates SET id=? WHERE id=?",
            (i, 1_000_000 + i),
        )
    _sync_template_id_sequence(conn)
    conn.commit()
    conn.close()


def _migrate_template_ids_from_one() -> None:
    """既存 DB のテンプレート ID を 1 始まりに一度だけ振り直す"""
    if get_setting("_template_ids_from_one") == "1":
        return
    renumber_templates_from_one()
    set_setting("_template_ids_from_one", "1")


def _sync_template_id_sequence(conn: sqlite3.Connection) -> None:
    """手動 ID 指定後に AUTOINCREMENT カウンタを同期"""
    max_id = conn.execute("SELECT MAX(id) FROM prompt_templates").fetchone()[0] or 0
    conn.execute(
        "INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES ('prompt_templates', ?)",
        (max_id,),
    )


def template_id_exists(tid: int, exclude_id: int | None = None) -> bool:
    conn = get_conn()
    if exclude_id is not None:
        row = conn.execute(
            "SELECT 1 FROM prompt_templates WHERE id=? AND id!=?", (tid, exclude_id)
        ).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM prompt_templates WHERE id=?", (tid,)).fetchone()
    conn.close()
    return row is not None


def change_template_id(old_id: int, new_id: int) -> str | None:
    """テンプレート ID を変更。成功時 None、失敗時エラーメッセージ"""
    if old_id == new_id:
        return None
    if new_id < 1:
        return "ID は 1 以上の整数を指定してください。"
    if template_id_exists(new_id):
        return f"ID {new_id} は既に使用されています。"
    conn = get_conn()
    cur = conn.execute("UPDATE prompt_templates SET id=? WHERE id=?", (new_id, old_id))
    if cur.rowcount == 0:
        conn.close()
        return f"ID {old_id} のテンプレートが見つかりません。"
    _sync_template_id_sequence(conn)
    conn.commit()
    conn.close()
    return None


def get_active_templates() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, category, system_prompt, default_model, allow_empty_prompt,"
        " template_kind, handler_config"
        " FROM prompt_templates WHERE is_active=1 ORDER BY sort_order, id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def template_usable_for_user(tmpl: dict, allowed_model_ids: list[str] | None) -> bool:
    """テンプレートの default_model がユーザーの許可モデルと整合するか"""
    if not allowed_model_ids:
        return True
    dm = (tmpl.get("default_model") or "").strip()
    if not dm:
        return True
    return dm in allowed_model_ids


def get_active_templates_for_user(employee_id: str) -> list[dict]:
    """ユーザーが利用可能なモデルに合うテンプレートのみ返す"""
    templates = get_active_templates()
    allowed = get_user_allowed_models(employee_id)
    if not allowed:
        return templates
    return [t for t in templates if template_usable_for_user(t, allowed)]


def get_default_template_for_user(employee_id: str, prefer_id: int = 1) -> dict | None:
    """許可モデルに合うテンプレートのうち prefer_id を優先して返す"""
    templates = get_active_templates_for_user(employee_id)
    if not templates:
        return None
    for t in templates:
        if t.get("id") == prefer_id:
            return t
    return templates[0]


def get_all_templates() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM prompt_templates ORDER BY sort_order, id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def suggest_copy_template_name(source_name: str) -> str:
    """既存テンプレート名のコピー用名称（重複回避）"""
    base = (source_name or "").strip() or "テンプレート"
    conn = get_conn()
    for i in range(1, 100):
        suffix = "（コピー）" if i == 1 else f"（コピー{i}）"
        candidate = f"{base}{suffix}"
        if not conn.execute(
            "SELECT 1 FROM prompt_templates WHERE name=?", (candidate,),
        ).fetchone():
            conn.close()
            return candidate
    conn.close()
    return f"{base}（コピー_{jst_now()}）"


def create_template(
    name: str,
    category: str,
    system_prompt: str,
    sort_order: int = 99,
    default_model: str = "",
    template_id: int | None = None,
    allow_empty_prompt: int = 0,
    *,
    is_active: int = 1,
    template_kind: str = "standard",
    handler_config: str = "{}",
) -> tuple[bool, str, int | None]:
    """テンプレート作成。(成功, エラーメッセージ, 割当 ID)"""
    import template_registry as tr

    kind = tr.normalize_kind(template_kind)
    try:
        conn = get_conn()
        if template_id is None:
            template_id = _next_template_id(conn)
        elif template_id < 1:
            conn.close()
            return False, "ID は 1 以上の整数を指定してください。", None
        elif conn.execute("SELECT 1 FROM prompt_templates WHERE id=?", (template_id,)).fetchone():
            conn.close()
            return False, f"ID {template_id} は既に使用されています。", None

        conn.execute(
            "INSERT INTO prompt_templates"
            " (id, name, category, system_prompt, is_active, sort_order,"
            " default_model, allow_empty_prompt, template_kind, handler_config,"
            " created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                template_id, name, category, system_prompt, int(is_active), sort_order,
                default_model, allow_empty_prompt, kind, handler_config, jst_now(),
            ),
        )
        _sync_template_id_sequence(conn)
        conn.commit()
        conn.close()
        return True, "", template_id
    except sqlite3.IntegrityError:
        return False, "同名のテンプレートが既に存在します。", None


def copy_template_as_draft(source_id: int) -> tuple[bool, str, int | None]:
    """既存テンプレートを複製して無効（下書き）テンプレートを作成"""
    conn = get_conn()
    row = conn.execute(
        "SELECT name, category, system_prompt, sort_order, default_model,"
        " allow_empty_prompt, template_kind, handler_config"
        " FROM prompt_templates WHERE id=?",
        (source_id,),
    ).fetchone()
    conn.close()
    if not row:
        return False, "コピー元のテンプレートが見つかりません。", None
    src = dict(row)
    draft_name = suggest_copy_template_name(src["name"])
    return create_template(
        draft_name,
        src.get("category") or "汎用",
        src.get("system_prompt") or "",
        int(src.get("sort_order") or 99),
        src.get("default_model") or "",
        allow_empty_prompt=int(src.get("allow_empty_prompt") or 0),
        is_active=0,
        template_kind=src.get("template_kind") or "standard",
        handler_config=src.get("handler_config") or "{}",
    )


def update_template(
    tid: int,
    name: str,
    category: str,
    system_prompt: str,
    is_active: int,
    sort_order: int,
    default_model: str | None = None,
    new_id: int | None = None,
    allow_empty_prompt: int | None = None,
    template_kind: str | None = None,
    handler_config: str | None = None,
) -> str | None:
    """テンプレート更新。成功時 None、失敗時エラーメッセージ"""
    import template_registry as tr

    target_id = tid
    if new_id is not None and new_id != tid:
        err = change_template_id(tid, new_id)
        if err:
            return err
        target_id = new_id

    conn = get_conn()
    allow_empty = 0 if allow_empty_prompt is None else int(allow_empty_prompt)
    if default_model is None:
        row = conn.execute(
            "SELECT default_model FROM prompt_templates WHERE id=?", (target_id,),
        ).fetchone()
        default_model = (row["default_model"] if row else "") or ""

    kind = (
        tr.normalize_kind(template_kind)
        if template_kind is not None
        else tr.get_template_kind(dict(conn.execute(
            "SELECT * FROM prompt_templates WHERE id=?", (target_id,),
        ).fetchone() or {}))
    )
    if handler_config is None:
        row = conn.execute(
            "SELECT handler_config FROM prompt_templates WHERE id=?", (target_id,),
        ).fetchone()
        hcfg = (row["handler_config"] if row else "{}") or "{}"
    else:
        hcfg = handler_config

    conn.execute(
        "UPDATE prompt_templates SET name=?, category=?, system_prompt=?,"
        " is_active=?, sort_order=?, default_model=?, allow_empty_prompt=?,"
        " template_kind=?, handler_config=? WHERE id=?",
        (
            name, category, system_prompt, is_active, sort_order,
            default_model, allow_empty, kind, hcfg, target_id,
        ),
    )
    conn.commit()
    conn.close()
    return None


def create_special_template(
    template_kind: str,
    name: str,
    category: str,
    system_prompt: str,
    sort_order: int = 99,
    default_model: str = "",
    allow_empty_prompt: int = 0,
    handler_config: dict | None = None,
    *,
    is_active: int = 1,
) -> tuple[bool, str, int | None]:
    """特殊テンプレート作成（ウィザード用）"""
    import template_registry as tr

    kind = tr.normalize_kind(template_kind)
    if kind not in tr.SPECIAL_KINDS:
        return False, "特殊テンプレート種別が不正です。", None
    config = tr.default_handler_config(kind, handler_config)
    return create_template(
        name.strip(),
        category.strip(),
        system_prompt.strip(),
        sort_order,
        default_model,
        allow_empty_prompt=allow_empty_prompt,
        is_active=is_active,
        template_kind=kind,
        handler_config=tr.handler_config_json(config),
    )


def delete_template(tid: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM prompt_templates WHERE id=?", (tid,))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════
# チャットセッション
# ══════════════════════════════════════════════════════════

def create_session(session_id: str, model: str, employee_id: str = "",
                   title: str = "新しいチャット") -> None:
    now = jst_now()
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO chat_sessions"
        " (session_id, employee_id, title, model, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, employee_id, title, model, now, now),
    )
    conn.commit()
    conn.close()


def update_session_title(session_id: str, title: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE chat_sessions SET title=?, updated_at=? WHERE session_id=?",
        (title[:60], jst_now(), session_id),
    )
    conn.commit()
    conn.close()


def touch_session(session_id: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE chat_sessions SET updated_at=? WHERE session_id=?",
        (jst_now(), session_id),
    )
    conn.commit()
    conn.close()


def delete_session(session_id: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM chat_sessions WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()


def get_sessions(employee_id: str, limit: int = 60) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT session_id, title, model, created_at, updated_at"
        " FROM chat_sessions WHERE employee_id=? ORDER BY updated_at DESC LIMIT ?",
        (employee_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════
# チャットメッセージ
# ══════════════════════════════════════════════════════════

def save_message(session_id: str, role: str, content: str,
                 log_id: int | None = None) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO chat_messages (session_id, role, content, log_id, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, log_id, jst_now()),
    )
    conn.commit()
    conn.close()


def get_messages(session_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT cm.role, cm.content, cm.log_id, cm.created_at,
                  f.rating as feedback_rating
           FROM chat_messages cm
           LEFT JOIN feedback f ON f.log_id = cm.log_id
           WHERE cm.session_id=? ORDER BY cm.id""",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════
# クエリログ
# ══════════════════════════════════════════════════════════

def log_query(
    session_id: str, employee_id: str, username: str, department: str,
    model: str, system_prompt: str, question: str, answer: str,
    has_attachment: bool = False, used_search: bool = False,
    input_tokens: int = 0, output_tokens: int = 0,
    client_ip: str = "", elapsed_ms: int = 0,
) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO query_logs
           (logged_at, session_id, employee_id, username, department,
            model, system_prompt, question, answer,
            has_attachment, used_search,
            input_tokens, output_tokens, client_ip, elapsed_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (jst_now(), session_id, employee_id, username, department,
         model, system_prompt, question, answer,
         1 if has_attachment else 0, 1 if used_search else 0,
         input_tokens, output_tokens, client_ip, elapsed_ms),
    )
    log_id = cur.lastrowid
    conn.commit()
    conn.close()
    return log_id


def get_query_logs(
    limit: int = 200, offset: int = 0,
    question_filter: str = "", model_filter: str = "",
    employee_filter: str = "", dept_filter: str = "",
    date_from: str = "", date_to: str = "",
) -> list[dict]:
    clauses, params = [], []
    if question_filter.strip():
        clauses.append("question LIKE ?"); params.append(f"%{question_filter.strip()}%")
    if model_filter.strip():
        clauses.append("model=?"); params.append(model_filter.strip())
    if employee_filter.strip():
        clauses.append("(employee_id LIKE ? OR username LIKE ?)");
        params += [f"%{employee_filter.strip()}%"] * 2
    if dept_filter.strip():
        clauses.append("department LIKE ?"); params.append(f"%{dept_filter.strip()}%")
    if date_from.strip():
        clauses.append("logged_at >= ?"); params.append(date_from.strip())
    if date_to.strip():
        clauses.append("logged_at <= ?"); params.append(date_to.strip() + " 23:59:59")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = get_conn()
    rows = conn.execute(
        f"SELECT * FROM query_logs{where} ORDER BY logged_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_query_log_count(
    question_filter: str = "", model_filter: str = "",
    employee_filter: str = "", dept_filter: str = "",
    date_from: str = "", date_to: str = "",
) -> int:
    clauses, params = [], []
    if question_filter.strip():
        clauses.append("question LIKE ?"); params.append(f"%{question_filter.strip()}%")
    if model_filter.strip():
        clauses.append("model=?"); params.append(model_filter.strip())
    if employee_filter.strip():
        clauses.append("(employee_id LIKE ? OR username LIKE ?)");
        params += [f"%{employee_filter.strip()}%"] * 2
    if dept_filter.strip():
        clauses.append("department LIKE ?"); params.append(f"%{dept_filter.strip()}%")
    if date_from.strip():
        clauses.append("logged_at >= ?"); params.append(date_from.strip())
    if date_to.strip():
        clauses.append("logged_at <= ?"); params.append(date_to.strip() + " 23:59:59")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = get_conn()
    count = conn.execute(
        f"SELECT COUNT(*) FROM query_logs{where}", params
    ).fetchone()[0]
    conn.close()
    return count


def logs_to_csv(rows: list[dict]) -> str:
    header = ("利用日時(JST),社員番号,氏名,部署,モデル,"
              "問い合わせ内容,生成された回答,添付,Web検索,"
              "入力Token,出力Token,クライアントIP,処理時間(ms)")
    lines = [header]
    for r in rows:
        def esc(v):
            return '"' + str(v or "").replace('"', '""') + '"'
        lines.append(
            f'{esc(r["logged_at"])},{esc(r["employee_id"])},{esc(r["username"])},'
            f'{esc(r["department"])},{esc(r["model"])},'
            f'{esc(r["question"])},{esc(r["answer"])},'
            f'{r.get("has_attachment",0)},{r.get("used_search",0)},'
            f'{r.get("input_tokens",0)},{r.get("output_tokens",0)},'
            f'{esc(r.get("client_ip",""))},{r.get("elapsed_ms",0)}'
        )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# フィードバック
# ══════════════════════════════════════════════════════════

def save_feedback(log_id: int, rating: int) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO feedback (log_id, rating, created_at) VALUES (?, ?, ?)",
        (log_id, rating, jst_now()),
    )
    conn.commit()
    conn.close()


def get_feedback_stats() -> dict:
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    good  = conn.execute("SELECT COUNT(*) FROM feedback WHERE rating=1").fetchone()[0]
    bad   = conn.execute("SELECT COUNT(*) FROM feedback WHERE rating=-1").fetchone()[0]
    recent = conn.execute(
        """SELECT q.logged_at, q.username, q.question, f.rating
           FROM feedback f JOIN query_logs q ON q.id=f.log_id
           ORDER BY f.created_at DESC LIMIT 50"""
    ).fetchall()
    conn.close()
    return {"total": total, "good": good, "bad": bad, "recent": [dict(r) for r in recent]}


# ══════════════════════════════════════════════════════════
# 統計
# ══════════════════════════════════════════════════════════

def get_stats() -> dict:
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM query_logs").fetchone()[0]
    today = conn.execute(
        "SELECT COUNT(*) FROM query_logs WHERE logged_at >= ?", (today_jst(),)
    ).fetchone()[0]
    by_model = conn.execute(
        "SELECT model, COUNT(*) cnt FROM query_logs GROUP BY model ORDER BY cnt DESC"
    ).fetchall()
    by_dept = conn.execute(
        "SELECT department, COUNT(*) cnt, SUM(input_tokens+output_tokens) tokens"
        " FROM query_logs WHERE department!='' GROUP BY department ORDER BY cnt DESC"
    ).fetchall()
    tokens = conn.execute(
        "SELECT SUM(input_tokens), SUM(output_tokens) FROM query_logs"
    ).fetchone()
    user_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
    conn.close()
    return {
        "total": total, "today": today,
        "by_model": [dict(r) for r in by_model],
        "by_dept": [dict(r) for r in by_dept],
        "input_tokens": tokens[0] or 0,
        "output_tokens": tokens[1] or 0,
        "user_count": user_count,
    }


def get_daily_usage_summary(days: int = 14) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT date_jst, SUM(count) as total
           FROM daily_usage
           GROUP BY date_jst
           ORDER BY date_jst DESC
           LIMIT ?""",
        (days,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]
