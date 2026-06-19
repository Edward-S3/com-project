import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "mescheck.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS highlighted_domains (
                user_email TEXT NOT NULL,
                domain TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_email, domain)
            )
            """
        )


def normalize_domain(domain: str) -> str:
    return domain.strip().lower()


def extract_domain(email: str) -> str:
    if not email or "@" not in email:
        return ""
    return normalize_domain(email.rsplit("@", 1)[-1])


def parse_manual_domain(value: str) -> str:
    text = value.strip().lower()
    if not text:
        return ""
    if "@" in text:
        return extract_domain(text)
    text = re.sub(r"^https?://", "", text)
    text = text.split("/")[0].split("?")[0]
    if text.startswith("www."):
        text = text[4:]
    return normalize_domain(text)


def is_valid_domain(domain: str) -> bool:
    if not domain or " " in domain or "@" in domain:
        return False
    if len(domain) > 253 or len(domain) < 3:
        return False
    if domain.count(".") < 1:
        return False
    return bool(re.fullmatch(r"[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+", domain))


def list_domains_sorted(user_email: str) -> list[str]:
    return sorted(list_domains(user_email))


def list_domains(user_email: str) -> set[str]:
    init_db()
    user_email = user_email.strip().lower()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT domain FROM highlighted_domains WHERE user_email = ?",
            (user_email,),
        ).fetchall()
    return {row["domain"] for row in rows}


def add_domain(user_email: str, domain: str) -> None:
    domain = normalize_domain(domain)
    if not domain:
        return
    init_db()
    user_email = user_email.strip().lower()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO highlighted_domains (user_email, domain) VALUES (?, ?)",
            (user_email, domain),
        )


def remove_domain(user_email: str, domain: str) -> None:
    domain = normalize_domain(domain)
    if not domain:
        return
    init_db()
    user_email = user_email.strip().lower()
    with _connect() as conn:
        conn.execute(
            "DELETE FROM highlighted_domains WHERE user_email = ? AND domain = ?",
            (user_email, domain),
        )


def set_domains(user_email: str, domains: set[str], enabled: bool) -> None:
    for domain in domains:
        if enabled:
            add_domain(user_email, domain)
        else:
            remove_domain(user_email, domain)
