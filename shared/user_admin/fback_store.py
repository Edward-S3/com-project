"""アンケート (fback) アプリのユーザー DB 操作"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import smtplib
import sqlite3
import string
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

from .constants import FBACK_DB_PATH

load_dotenv("/opt/exam/.env")
load_dotenv("/opt/fback/.env")

FBACK_HOST = os.getenv("FBACK_HOST", "172.16.16.10")
FBACK_PORT = os.getenv("FBACK_PORT", "8503")


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    if not email:
        return False
    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, normalize_email(email)) is not None


def get_admin_portal_url() -> str:
    return f"http://{FBACK_HOST}:{FBACK_PORT}/?admin=true"


def is_smtp_configured() -> bool:
    keys = ("SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM")
    if not all(os.getenv(k, "").strip() for k in keys):
        return False
    user = os.getenv("SMTP_USER", "").lower()
    pwd = os.getenv("SMTP_PASSWORD", "").lower()
    placeholders = ("your_email", "your_app_password", "example.com", "xxx", "password")
    if any(p in user or p in pwd for p in placeholders):
        return False
    return True


def generate_random_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def send_user_registration_email(to_email: str, plain_password: str, company_name: str) -> tuple[bool, str]:
    if not is_smtp_configured():
        return False, "SMTPが未設定です。/opt/exam/.env の SMTP 設定を確認してください。"

    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_from = os.getenv("EMAIL_FROM")
    admin_url = get_admin_portal_url()
    to_email = normalize_email(to_email)

    subject = "【アンケートシステム】ユーザー登録完了のお知らせ"
    text_body = f"""アンケートシステムのアカウントが登録されました。

■ 管理画面URL
{admin_url}

■ ユーザーID（メールアドレス）
{to_email}

■ ログインパスワード
{plain_password}

■ 所属
{company_name}

上記のユーザーIDとパスワードで管理画面にログインしてください。
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(email_from, [to_email], msg.as_string())
        return True, f"登録完了メールを {to_email} に送信しました。"
    except Exception as exc:
        return False, f"メール送信エラー: {exc}"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(FBACK_DB_PATH, check_same_thread=False)
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


def create_user(
    email: str,
    company_name: str,
    password: str,
    role: str = "creator",
) -> tuple[bool, str]:
    email_norm = normalize_email(email)
    company_name = company_name.strip()
    if not email_norm or not company_name or not password:
        return False, "すべての項目を入力してください。"
    if not is_valid_email(email):
        return False, "ユーザーIDには有効なメールアドレスを入力してください。"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO users (username, password, company_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (email_norm, hash_password(password), company_name, role, now),
        )
        conn.commit()
        return True, f"ユーザー「{email_norm}」を登録しました。"
    except sqlite3.IntegrityError:
        return False, "そのメールアドレスは既に登録されています。"
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


def update_password(user_id: int, password: str) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE users SET password=? WHERE id=?",
        (hash_password(password), user_id),
    )
    conn.commit()
    conn.close()


def delete_user(user_id: int) -> tuple[bool, str]:
    user = get_user(user_id)
    if not user:
        return False, "ユーザーが見つかりません。"
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM survey_editors WHERE user_id=? OR granted_by=?",
            (user_id, user_id),
        )
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
        return True, f"ユーザー「{user['username']}」を削除しました。"
    except Exception as exc:
        return False, f"削除エラー: {exc}"
    finally:
        conn.close()
