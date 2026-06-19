"""試験 (exam) アプリのユーザー DB 操作"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime

from .constants import EXAM_DB_PATH


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(EXAM_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def list_users(exclude_id: int | None = None) -> list[dict]:
    conn = _conn()
    if exclude_id is not None:
        rows = conn.execute(
            "SELECT id, username, company_name, role, created_at FROM users "
            "WHERE id != ? ORDER BY id DESC",
            (exclude_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, username, company_name, role, created_at FROM users ORDER BY id DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user(user_id: int) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT id, username, company_name, role, created_at FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_user(username: str, company_name: str, password: str, role: str = "creator") -> tuple[bool, str]:
    username = username.strip()
    company_name = company_name.strip()
    if not username or not company_name or not password:
        return False, "すべての項目を入力してください。"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO users (username, password, company_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, hash_password(password), company_name, role, now),
        )
        conn.commit()
        return True, f"ユーザー「{username}」を登録しました。"
    except sqlite3.IntegrityError:
        return False, "そのログインIDは既に使用されています。"
    finally:
        conn.close()


def update_user(
    user_id: int,
    company_name: str,
    role: str,
    password: str | None = None,
) -> tuple[bool, str]:
    company_name = company_name.strip()
    if not company_name:
        return False, "所属名は空欄にできません。"
    conn = _conn()
    try:
        if password:
            conn.execute(
                "UPDATE users SET company_name=?, role=?, password=? WHERE id=?",
                (company_name, role, hash_password(password), user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET company_name=?, role=? WHERE id=?",
                (company_name, role, user_id),
            )
        conn.commit()
        user = get_user(user_id)
        name = user["username"] if user else str(user_id)
        return True, f"ユーザー「{name}」を更新しました。"
    except Exception as exc:
        return False, f"更新エラー: {exc}"
    finally:
        conn.close()


def delete_user(user_id: int) -> tuple[bool, str]:
    user = get_user(user_id)
    if not user:
        return False, "ユーザーが見つかりません。"
    conn = _conn()
    try:
        conn.execute("DELETE FROM exam_editors WHERE user_id=? OR granted_by=?", (user_id, user_id))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
        return True, f"ユーザー「{user['username']}」を削除しました。"
    except Exception as exc:
        return False, f"削除エラー: {exc}"
    finally:
        conn.close()
