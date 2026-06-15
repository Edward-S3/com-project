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

    # 安全なインデックス（新規テーブルのみ）
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages (session_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_logs_at ON query_logs (logged_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_feedback_log ON feedback (log_id)",
        "CREATE INDEX IF NOT EXISTS idx_daily_usage ON daily_usage (employee_id, date_jst)",
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
    ensure_media_templates()
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


def ensure_media_templates() -> None:
    """画像生成・音声文字起こし・議事録テンプレートを欠落時のみ追加"""
    conn = get_conn()
    for name, cat, prompt, order, default_model, allow_empty in _MEDIA_TEMPLATES:
        if conn.execute("SELECT 1 FROM prompt_templates WHERE name=?", (name,)).fetchone():
            continue
        template_id = _next_template_id(conn)
        conn.execute(
            """INSERT INTO prompt_templates
               (id, name, category, system_prompt, is_active, sort_order,
                default_model, allow_empty_prompt, created_at)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (template_id, name, cat, prompt, order, default_model, allow_empty, jst_now()),
        )
    _sync_template_id_sequence(conn)
    conn.commit()
    conn.close()


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
        "SELECT id, name, category, system_prompt, default_model, allow_empty_prompt"
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
        "SELECT * FROM prompt_templates ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_template(
    name: str,
    category: str,
    system_prompt: str,
    sort_order: int = 99,
    default_model: str = "",
    template_id: int | None = None,
    allow_empty_prompt: int = 0,
) -> tuple[bool, str, int | None]:
    """テンプレート作成。(成功, エラーメッセージ, 割当 ID)"""
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
            " default_model, allow_empty_prompt, created_at)"
            " VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (template_id, name, category, system_prompt, sort_order,
             default_model, allow_empty_prompt, jst_now()),
        )
        _sync_template_id_sequence(conn)
        conn.commit()
        conn.close()
        return True, "", template_id
    except sqlite3.IntegrityError:
        return False, "同名のテンプレートが既に存在します。", None


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
) -> str | None:
    """テンプレート更新。成功時 None、失敗時エラーメッセージ"""
    target_id = tid
    if new_id is not None and new_id != tid:
        err = change_template_id(tid, new_id)
        if err:
            return err
        target_id = new_id

    conn = get_conn()
    allow_empty = 0 if allow_empty_prompt is None else int(allow_empty_prompt)
    if default_model is not None:
        conn.execute(
            "UPDATE prompt_templates SET name=?, category=?, system_prompt=?,"
            " is_active=?, sort_order=?, default_model=?, allow_empty_prompt=? WHERE id=?",
            (name, category, system_prompt, is_active, sort_order,
             default_model, allow_empty, target_id),
        )
    else:
        conn.execute(
            "UPDATE prompt_templates SET name=?, category=?, system_prompt=?,"
            " is_active=?, sort_order=?, allow_empty_prompt=? WHERE id=?",
            (name, category, system_prompt, is_active, sort_order, allow_empty, target_id),
        )
    conn.commit()
    conn.close()
    return None


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
