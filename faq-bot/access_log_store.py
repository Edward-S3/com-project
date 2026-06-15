import os
import sqlite3
from datetime import datetime, timedelta, timezone

DB_PATH = os.getenv("CHAT_HISTORY_DB", "chat_history.db")
JST = timezone(timedelta(hours=9))


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _jst_now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def init_access_log():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS access_log
           (id INTEGER PRIMARY KEY AUTOINCREMENT,
            accessed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            client_ip TEXT,
            client_hostname TEXT,
            question TEXT)"""
    )
    # 既存DBへの後方互換カラム追加
    try:
        c.execute("ALTER TABLE access_log ADD COLUMN client_hostname TEXT")
    except sqlite3.OperationalError:
        pass  # カラムが既に存在する場合は無視
    c.execute(
        """CREATE INDEX IF NOT EXISTS idx_access_log_accessed_at
           ON access_log (accessed_at DESC)"""
    )
    conn.commit()
    conn.close()


def log_access(question: str, client_ip: str, client_hostname: str = ""):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO access_log (accessed_at, client_ip, client_hostname, question) VALUES (?, ?, ?, ?)",
        (_jst_now(), client_ip, client_hostname, question),
    )
    conn.commit()
    conn.close()


def _build_filters(ip_filter="", hostname_filter="", question_filter=""):
    clauses = []
    params = []
    if ip_filter.strip():
        clauses.append("client_ip LIKE ?")
        params.append(f"%{ip_filter.strip()}%")
    if hostname_filter.strip():
        clauses.append("client_hostname LIKE ?")
        params.append(f"%{hostname_filter.strip()}%")
    if question_filter.strip():
        clauses.append("question LIKE ?")
        params.append(f"%{question_filter.strip()}%")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def get_access_logs(limit=100, offset=0, ip_filter="", hostname_filter="", question_filter=""):
    where, params = _build_filters(ip_filter, hostname_filter, question_filter)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        f"SELECT accessed_at, client_ip, client_hostname, question FROM access_log{where}"
        " ORDER BY accessed_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    results = c.fetchall()
    conn.close()
    return results


def get_access_log_count(ip_filter="", hostname_filter="", question_filter=""):
    where, params = _build_filters(ip_filter, hostname_filter, question_filter)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"SELECT COUNT(*) FROM access_log{where}", params)
    count = c.fetchone()[0]
    conn.close()
    return count


def clear_access_log():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM access_log")
    c.execute("DELETE FROM sqlite_sequence WHERE name='access_log'")
    conn.commit()
    conn.close()


def access_logs_to_csv(rows):
    lines = ["アクセス日時,IP,PC名,質問"]
    for accessed_at, client_ip, client_hostname, question in rows:
        escaped_q = question.replace('"', '""') if question else ""
        hostname = client_hostname or ""
        lines.append(f'"{accessed_at}","{client_ip}","{hostname}","{escaped_q}"')
    return "\n".join(lines)
