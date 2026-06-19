import sqlite3
from dataclasses import dataclass

from domain_store import DB_PATH, init_db

DEFAULT_REFRESH_MINUTES = 10
MIN_REFRESH_MINUTES = 2
MAX_REFRESH_MINUTES = 1440


@dataclass
class UserSettings:
    auto_refresh_enabled: bool = True
    refresh_interval_minutes: int = DEFAULT_REFRESH_MINUTES
    show_teams_post: bool = True
    show_teams_chat: bool = True
    show_mail: bool = True


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_user_preferences(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(user_preferences)")}
    additions = (
        ("show_teams_post", "INTEGER NOT NULL DEFAULT 1"),
        ("show_teams_chat", "INTEGER NOT NULL DEFAULT 1"),
        ("show_mail", "INTEGER NOT NULL DEFAULT 1"),
    )
    for name, ddl in additions:
        if name not in columns:
            conn.execute(f"ALTER TABLE user_preferences ADD COLUMN {name} {ddl}")


def init_user_settings_db() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_email TEXT PRIMARY KEY,
                auto_refresh_enabled INTEGER NOT NULL DEFAULT 1,
                refresh_interval_minutes INTEGER NOT NULL DEFAULT 10,
                show_teams_post INTEGER NOT NULL DEFAULT 1,
                show_teams_chat INTEGER NOT NULL DEFAULT 1,
                show_mail INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _migrate_user_preferences(conn)


def clamp_interval(minutes: int) -> int:
    return max(MIN_REFRESH_MINUTES, min(int(minutes), MAX_REFRESH_MINUTES))


def _row_to_settings(row: sqlite3.Row) -> UserSettings:
    keys = set(row.keys())
    return UserSettings(
        auto_refresh_enabled=bool(row["auto_refresh_enabled"]),
        refresh_interval_minutes=clamp_interval(row["refresh_interval_minutes"]),
        show_teams_post=bool(row["show_teams_post"]) if "show_teams_post" in keys else True,
        show_teams_chat=bool(row["show_teams_chat"]) if "show_teams_chat" in keys else True,
        show_mail=bool(row["show_mail"]) if "show_mail" in keys else True,
    )


def get_user_settings(user_email: str) -> UserSettings:
    init_user_settings_db()
    user_email = user_email.strip().lower()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT auto_refresh_enabled, refresh_interval_minutes,
                   show_teams_post, show_teams_chat, show_mail
            FROM user_preferences
            WHERE user_email = ?
            """,
            (user_email,),
        ).fetchone()
    if row is None:
        return UserSettings()
    return _row_to_settings(row)


def save_user_settings(user_email: str, settings: UserSettings) -> UserSettings:
    init_user_settings_db()
    user_email = user_email.strip().lower()
    interval = clamp_interval(settings.refresh_interval_minutes)
    normalized = UserSettings(
        auto_refresh_enabled=settings.auto_refresh_enabled,
        refresh_interval_minutes=interval,
        show_teams_post=settings.show_teams_post,
        show_teams_chat=settings.show_teams_chat,
        show_mail=settings.show_mail,
    )
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_preferences (
                user_email,
                auto_refresh_enabled,
                refresh_interval_minutes,
                show_teams_post,
                show_teams_chat,
                show_mail,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_email) DO UPDATE SET
                auto_refresh_enabled = excluded.auto_refresh_enabled,
                refresh_interval_minutes = excluded.refresh_interval_minutes,
                show_teams_post = excluded.show_teams_post,
                show_teams_chat = excluded.show_teams_chat,
                show_mail = excluded.show_mail,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_email,
                1 if normalized.auto_refresh_enabled else 0,
                normalized.refresh_interval_minutes,
                1 if normalized.show_teams_post else 0,
                1 if normalized.show_teams_chat else 0,
                1 if normalized.show_mail else 0,
            ),
        )
    return normalized
