import streamlit as st
import streamlit.components.v1 as components
import sqlite3
import json
import os
import sys
import hashlib
from datetime import datetime
import uuid
import copy
import io
import re
import tempfile
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import pandas as pd
from dotenv import load_dotenv

_SHARED_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shared"))
if _SHARED_ROOT not in sys.path:
    sys.path.insert(0, _SHARED_ROOT)
from user_admin import render_exam_users

# google.generativeai の安全なインポート
try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ModuleNotFoundError:
    genai = None
    HAS_GEMINI = False

# fpdf の安全なインポート
try:
    from fpdf import FPDF
    HAS_FPDF = True
except ModuleNotFoundError:
    FPDF = None
    HAS_FPDF = False

# --- 1. 環境設定と初期化 ---
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "nakaboshi_admin0123")
if GEMINI_API_KEY and HAS_GEMINI and genai:
    genai.configure(api_key=GEMINI_API_KEY)

# データベースファイルのパス
DB_PATH = 'exam_app.db'

# 受験URLのホスト（WindowsクライアントからアクセスするLinuxサーバーのアドレス）
EXAM_HOST = os.getenv("EXAM_HOST", "172.16.16.10")
EXAM_PORT = int(os.getenv("EXAM_PORT", "8505"))

# 日本語フォントの設定（Matplotlib用文字化け対策）
FONT_PATHS = [
    "fback/IPAexfont00401/ipaexg.ttf",
    "/opt/fback/assets/ipaexg.ttf",
    "c:/AIwork/Antigravity2/fback/IPAexfont00401/ipaexg.ttf"
]
FONT_PATH = None
for path in FONT_PATHS:
    if os.path.exists(path):
        FONT_PATH = path
        break

if FONT_PATH:
    fm.fontManager.addfont(FONT_PATH)
    plt.rcParams['font.family'] = 'IPAexGothic'
else:
    plt.rcParams['font.family'] = 'sans-serif'

# --- 2. データベース操作 ---
def get_db_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # ユーザーテーブル
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            company_name TEXT,
            role TEXT,
            created_at DATETIME
        )
    ''')
    
    # 試験問題テーブル
    c.execute('''
        CREATE TABLE IF NOT EXISTS exams (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            status TEXT,
            limit_time INTEGER,
            schema TEXT,
            created_by INTEGER,
            created_at DATETIME,
            grading_config TEXT,
            FOREIGN KEY(created_by) REFERENCES users(id)
        )
    ''')
    c.execute("PRAGMA table_info(exams)")
    exam_cols = {row[1] for row in c.fetchall()}
    if "grading_config" not in exam_cols:
        c.execute("ALTER TABLE exams ADD COLUMN grading_config TEXT")
    
    # 受験回答・採点結果テーブル
    c.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id TEXT,
            examinee_name TEXT,
            examinee_email TEXT,
            answers TEXT,
            score REAL,
            total_points REAL,
            results TEXT,
            email_sent INTEGER DEFAULT 0,
            submitted_at DATETIME,
            schema_snapshot TEXT,
            FOREIGN KEY(exam_id) REFERENCES exams(id)
        )
    ''')
    c.execute("PRAGMA table_info(submissions)")
    submission_cols = {row[1] for row in c.fetchall()}
    if "schema_snapshot" not in submission_cols:
        c.execute("ALTER TABLE submissions ADD COLUMN schema_snapshot TEXT")

    # 試験編集権限（作成者が他ユーザーに付与）
    c.execute('''
        CREATE TABLE IF NOT EXISTS exam_editors (
            exam_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            granted_by INTEGER NOT NULL,
            created_at DATETIME,
            PRIMARY KEY (exam_id, user_id),
            FOREIGN KEY(exam_id) REFERENCES exams(id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(granted_by) REFERENCES users(id)
        )
    ''')
    
    # 初期管理者および初期作成者アカウントの作成
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 管理者 (admin / ADMIN_PASSWORD)
        c.execute(
            "INSERT INTO users (username, password, company_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            ('admin', hash_password(ADMIN_PASSWORD), 'システム管理部', 'admin', now)
        )
        # 問題作成者 (creator1 / creator123)
        c.execute(
            "INSERT INTO users (username, password, company_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            ('creator1', hash_password('creator123'), '第一教育事業部', 'creator', now)
        )
        
        # サンプル試験問題の作成
        sample_schema = [
            {
                "id": "q1",
                "type": "択一選択",
                "question": "Pythonにおいて、リストの末尾に要素を追加するメソッドはどれですか？",
                "options": ["add()", "append()", "push()", "insert()"],
                "category": "プログラミング基礎",
                "points": 20.0,
                "correct_answer": "append()",
                "explanation": "Pythonのリスト型に要素を追加するには append() メソッドを使用します。add() は集合(set)型、push() はスタック操作（他の言語）で一般的です。"
            },
            {
                "id": "q2",
                "type": "複数選択",
                "question": "インターネット通信において、セキュリティを向上させるプロトコルをすべて選んでください。",
                "options": ["HTTPS", "FTP", "SSH", "HTTP"],
                "category": "セキュリティ",
                "points": 20.0,
                "correct_answer": ["HTTPS", "SSH"],
                "explanation": "HTTPSはWeb通信、SSHはリモートログイン用で、どちらも暗号化を行いセキュリティを向上させます。FTPやHTTPは暗号化されません。"
            },
            {
                "id": "q3",
                "type": "○×式",
                "question": "Relational Databaseにおいて、主キー(Primary Key)の値は重複してもよい。",
                "options": ["○ (正しい)", "× (誤り)"],
                "category": "データベース",
                "points": 20.0,
                "correct_answer": "× (誤り)",
                "explanation": "主キー(Primary Key)はテーブル内の行を一意に識別するためのものであるため、重複やNULL値は認められません。"
            },
            {
                "id": "q4",
                "type": "テキスト（記述式）",
                "question": "APIとは何の略称ですか？（英単語で答えてください）",
                "category": "Web技術",
                "points": 20.0,
                "correct_answer": "Application Programming Interface",
                "explanation": "APIは「Application Programming Interface」の略です。アプリケーション同士をつなぐ役割を持ちます。"
            },
            {
                "id": "q5",
                "type": "テキストエリア（長文記述）",
                "question": "クライアント・サーバーシステムにおいて、「クッキー(Cookie)」の役割と、それがなぜセッション維持に必要なのか説明してください。",
                "category": "Web技術",
                "points": 20.0,
                "correct_answer": "HTTPプロトコルは状態を保持しない（ステートレス）ため、サーバーはリクエストが誰から来たか判別できません。そのため、サーバーが発行したCookieをクライアントに保存させ、次回以降の通信で自動送信させることで、同一ユーザーのセッションであることを維持・確認します。",
                "explanation": "HTTPのステートレス性を補うため、サーバー側でセッションIDを生成し、それをクライアントのCookieに保存して識別を行う仕組みを説明している必要があります。"
            }
        ]
        
        c.execute(
            "INSERT INTO exams (id, title, description, status, limit_time, schema, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ('EX-SAMPLE', 'IT・Web開発基礎知識テスト', 'ITの基礎スキルやWeb技術に関する総合確認テストです。選択式、記述式を含みます。', '公開', 15, json.dumps(sample_schema, ensure_ascii=False), 2, now)
        )
        
    conn.commit()
    conn.close()


def get_exam_url(exam_id: str) -> str:
    # Nginx配下で Streamlit が /exam にマウントされている前提
    return f"http://{EXAM_HOST}/exam/?ID={exam_id}"


def render_exam_url_box(exam_id: str):
    """受験URLを視認性の高いスタイルで表示し、クリップボードコピーを提供する。"""
    url = get_exam_url(exam_id)
    btn_id = "copy_" + exam_id.replace("-", "_").replace(".", "_")
    st.markdown("**受験者への配布用URL**")
    st.markdown(
        f'<div class="exam-url-display">'
        f'<a class="exam-url-link" href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
        f"</div>",
        unsafe_allow_html=True,
    )
    url_js = url.replace("\\", "\\\\").replace("'", "\\'")
    components.html(
        f"""
        <div style="font-family: sans-serif;">
          <button id="{btn_id}" type="button" style="
            background: linear-gradient(90deg, #6366F1, #8B5CF6);
            color: #fff; border: none; border-radius: 8px;
            padding: 8px 16px; font-weight: 600; cursor: pointer;
          ">URLをクリップボードにコピー</button>
          <span id="{btn_id}_msg" style="margin-left: 10px; color: #10B981; font-size: 14px;"></span>
        </div>
        <script>
        (function() {{
          var url = '{url_js}';
          var btn = document.getElementById('{btn_id}');
          var msg = document.getElementById('{btn_id}_msg');
          btn.onclick = function() {{
            function done() {{ msg.textContent = 'コピーしました'; }}
            function fallback() {{
              var ta = document.createElement('textarea');
              ta.value = url;
              ta.style.position = 'fixed';
              ta.style.left = '-9999px';
              document.body.appendChild(ta);
              ta.focus();
              ta.select();
              try {{
                document.execCommand('copy');
                done();
              }} catch (e) {{
                msg.textContent = 'コピーに失敗しました。URLを手動で選択してください。';
                msg.style.color = '#F43F5E';
              }}
              document.body.removeChild(ta);
            }}
            if (navigator.clipboard && navigator.clipboard.writeText) {{
              navigator.clipboard.writeText(url).then(done).catch(fallback);
            }} else {{
              fallback();
            }}
          }};
        }})();
        </script>
        """,
        height=56,
    )


def user_can_edit_exam(user_id, role, exam_id):
    """試験の編集権限（作成者 or exam_editors に登録されたユーザー）。"""
    return user_can_access_exam(user_id, role, exam_id)


def user_can_access_exam(user_id, role, exam_id):
    """試験の編集および受験結果・分析の参照権限（作成者 or exam_editors）。"""
    if role == "admin":
        return True
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT created_by FROM exams WHERE id = ?", (exam_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    if row[0] == user_id:
        conn.close()
        return True
    c.execute(
        "SELECT 1 FROM exam_editors WHERE exam_id = ? AND user_id = ?",
        (exam_id, user_id),
    )
    allowed = c.fetchone() is not None
    conn.close()
    return allowed


def get_analyzable_exams(user_id, role):
    """結果・分析画面で選択可能な試験一覧（作成試験 + 権限付与された試験）。"""
    conn = get_db_connection()
    c = conn.cursor()
    if role == "admin":
        c.execute("""
            SELECT e.id, e.title, e.schema, e.created_by, u.username
            FROM exams e
            JOIN users u ON e.created_by = u.id
            ORDER BY e.created_at DESC
        """)
    else:
        c.execute("""
            SELECT DISTINCT e.id, e.title, e.schema, e.created_by, u.username
            FROM exams e
            JOIN users u ON e.created_by = u.id
            LEFT JOIN exam_editors ee ON e.id = ee.exam_id AND ee.user_id = ?
            WHERE e.created_by = ? OR ee.user_id = ?
            ORDER BY e.created_at DESC
        """, (user_id, user_id, user_id))
    rows = c.fetchall()
    conn.close()
    return rows


def user_owns_exam(user_id, exam_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT created_by FROM exams WHERE id = ?", (exam_id,))
    row = c.fetchone()
    conn.close()
    return row and row[0] == user_id


def get_editable_exams(user_id, role):
    conn = get_db_connection()
    c = conn.cursor()
    if role == "admin":
        c.execute("""
            SELECT e.id, e.title, e.description, e.status, e.limit_time, e.created_at,
                   e.created_by, u.username
            FROM exams e
            JOIN users u ON e.created_by = u.id
            ORDER BY e.created_at DESC
        """)
    else:
        c.execute("""
            SELECT DISTINCT e.id, e.title, e.description, e.status, e.limit_time, e.created_at,
                   e.created_by, u.username
            FROM exams e
            JOIN users u ON e.created_by = u.id
            LEFT JOIN exam_editors ee ON e.id = ee.exam_id AND ee.user_id = ?
            WHERE e.created_by = ? OR ee.user_id = ?
            ORDER BY e.created_at DESC
        """, (user_id, user_id, user_id))
    rows = c.fetchall()
    conn.close()
    return rows


def count_submissions_for_exam(exam_id):
    """対象試験の提出データ件数を返す。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM submissions WHERE exam_id = ?", (exam_id,))
    n = c.fetchone()[0]
    conn.close()
    return int(n or 0)


def delete_submission(submission_id, exam_id=None):
    """受験提出データを削除する。
    exam_id を渡すと「その試験に属する提出」のみ削除する安全ガードが入る。
    戻り値: (削除件数:int, 削除した受験者氏名:str|None)
    """
    conn = get_db_connection()
    c = conn.cursor()
    if exam_id is None:
        c.execute(
            "SELECT examinee_name, exam_id FROM submissions WHERE id = ?",
            (submission_id,),
        )
    else:
        c.execute(
            "SELECT examinee_name, exam_id FROM submissions WHERE id = ? AND exam_id = ?",
            (submission_id, exam_id),
        )
    row = c.fetchone()
    if not row:
        conn.close()
        return 0, None
    name = row[0]
    if exam_id is None:
        c.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
    else:
        c.execute(
            "DELETE FROM submissions WHERE id = ? AND exam_id = ?",
            (submission_id, exam_id),
        )
    deleted = c.rowcount or 0
    conn.commit()
    conn.close()
    return deleted, name


def fetch_exam_record(exam_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, title, description, status, limit_time, schema, created_by, grading_config FROM exams WHERE id = ?",
        (exam_id,),
    )
    row = c.fetchone()
    conn.close()
    return row


# 試験ステータスの正規化用定数
EXAM_STATUS_PUBLISHED = "公開"
EXAM_STATUS_UNPUBLISHED = "非公開"


def normalize_exam_status(raw):
    """status カラムの値を正規化する（NULL/空文字や未知の値は「公開」扱い）。"""
    if raw is None:
        return EXAM_STATUS_PUBLISHED
    val = str(raw).strip()
    if val == EXAM_STATUS_UNPUBLISHED:
        return EXAM_STATUS_UNPUBLISHED
    return EXAM_STATUS_PUBLISHED


def is_exam_published(raw_status):
    """raw_status（DBから取得した status カラム値）が公開状態か判定する。"""
    return normalize_exam_status(raw_status) == EXAM_STATUS_PUBLISHED


def set_exam_status(exam_id, new_status):
    """試験の公開状態（status カラム）を更新する。
    戻り値: 更新件数(int)
    """
    new_status = normalize_exam_status(new_status)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE exams SET status = ? WHERE id = ?", (new_status, exam_id))
    updated = c.rowcount or 0
    conn.commit()
    conn.close()
    return updated


def delete_exam(exam_id):
    """試験本体および関連データ（編集権限・受験提出）を一括削除する。
    戻り値: (削除した試験タイトル:str|None, 削除した受験提出件数:int)
    """
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT title FROM exams WHERE id = ?", (exam_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None, 0
    title = row[0]
    c.execute("SELECT COUNT(*) FROM submissions WHERE exam_id = ?", (exam_id,))
    sub_count = int(c.fetchone()[0] or 0)
    c.execute("DELETE FROM submissions WHERE exam_id = ?", (exam_id,))
    c.execute("DELETE FROM exam_editors WHERE exam_id = ?", (exam_id,))
    c.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    conn.commit()
    conn.close()
    return title, sub_count


def user_can_delete_exam(user_id, role, exam_id):
    """試験の削除権限。管理者 or 作成者本人のみ許可する。"""
    if role == "admin":
        return True
    return bool(user_owns_exam(user_id, exam_id))


def fetch_grading_config(exam_id):
    row = fetch_exam_record(exam_id)
    if not row:
        return normalize_grading_config(None)
    return parse_grading_config(row[7])


def parse_grading_config(raw):
    if not raw:
        return normalize_grading_config(None)
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        data = None
    return normalize_grading_config(data)


def normalize_grading_config(cfg):
    """試験全体の合格・段階評価設定を正規化する。"""
    if not cfg or not isinstance(cfg, dict):
        return {"mode": "none"}
    mode = cfg.get("mode", "none")
    if mode not in ("none", "pass_fail", "tiers"):
        mode = "none"
    score_type = cfg.get("score_type", "absolute")
    if score_type not in ("absolute", "percent"):
        score_type = "absolute"
    if mode == "none":
        return {"mode": "none"}
    if mode == "pass_fail":
        return {
            "mode": "pass_fail",
            "score_type": score_type,
            "pass_threshold": float(cfg.get("pass_threshold", 60)),
            "pass_label": str(cfg.get("pass_label", "合格") or "合格").strip(),
            "fail_label": str(cfg.get("fail_label", "不合格") or "不合格").strip(),
        }
    tiers = []
    for t in cfg.get("tiers") or []:
        if not isinstance(t, dict):
            continue
        label = str(t.get("label", "")).strip()
        if not label:
            continue
        try:
            min_score = float(t.get("min_score", 0))
        except (TypeError, ValueError):
            min_score = 0.0
        tiers.append({"min_score": min_score, "label": label})
    tiers.sort(key=lambda x: x["min_score"], reverse=True)
    if not tiers:
        return {"mode": "none"}
    return {"mode": "tiers", "score_type": score_type, "tiers": tiers}


DEFAULT_TIER_PRESETS = [
    {"min_score": 90, "label": "評価A"},
    {"min_score": 80, "label": "評価B"},
    {"min_score": 75, "label": "評価C"},
    {"min_score": 60, "label": "評価D"},
    {"min_score": 0, "label": "評価E（不合格）"},
]


def score_value_for_grading(score, total_points, grading_config):
    """評価判定に用いる数値（絶対点または百分率）。"""
    s = float(score or 0)
    if grading_config.get("score_type") == "percent":
        tp = float(total_points or 0)
        return (s / tp * 100.0) if tp > 0 else 0.0
    return s


def evaluate_score_grade(score, total_points, grading_config):
    """得点から総合評価ラベルを算出。設定なしの場合は None。"""
    cfg = normalize_grading_config(grading_config)
    if cfg["mode"] == "none":
        return None
    value = score_value_for_grading(score, total_points, cfg)
    if cfg["mode"] == "pass_fail":
        th = float(cfg["pass_threshold"])
        return cfg["pass_label"] if value >= th else cfg["fail_label"]
    for tier in cfg["tiers"]:
        if value >= float(tier["min_score"]):
            return tier["label"]
    return cfg["tiers"][-1]["label"] if cfg.get("tiers") else None


def grading_threshold_unit_label(grading_config):
    cfg = normalize_grading_config(grading_config)
    if cfg["mode"] == "none":
        return ""
    return "％" if cfg.get("score_type") == "percent" else "点"


def render_grade_badge(grade_label, grading_config):
    """総合評価を目立つバッジとして表示。"""
    if not grade_label:
        return
    cfg = normalize_grading_config(grading_config)
    color = "#4338CA"
    bg = "rgba(99,102,241,0.10)"
    border = "#C7D2FE"
    if cfg["mode"] == "pass_fail":
        if grade_label == cfg.get("pass_label"):
            color = "#047857"
            bg = "rgba(16,185,129,0.10)"
            border = "#A7F3D0"
        elif grade_label == cfg.get("fail_label"):
            color = "#B91C1C"
            bg = "rgba(244,63,94,0.10)"
            border = "#FECACA"
    st.markdown(
        f"<div style='text-align:center; margin:12px 0;'>"
        f"<span style='display:inline-block; font-size:28px; font-weight:bold; color:{color}; "
        f"padding:12px 28px; border-radius:12px; background:{bg}; border:1px solid {border};'>"
        f"総合評価: {grade_label}</span></div>",
        unsafe_allow_html=True,
    )


def prime_grading_session(prefix, grading_config):
    cfg = normalize_grading_config(grading_config)
    st.session_state[f"{prefix}_grading_mode"] = cfg["mode"]
    st.session_state[f"{prefix}_score_type"] = cfg.get("score_type", "absolute")
    if cfg["mode"] == "pass_fail":
        st.session_state[f"{prefix}_pass_threshold"] = float(cfg["pass_threshold"])
        st.session_state[f"{prefix}_pass_label"] = cfg["pass_label"]
        st.session_state[f"{prefix}_fail_label"] = cfg["fail_label"]
    if cfg["mode"] == "tiers":
        st.session_state[f"{prefix}_tiers"] = copy.deepcopy(cfg["tiers"])
    elif f"{prefix}_tiers" not in st.session_state:
        st.session_state[f"{prefix}_tiers"] = copy.deepcopy(DEFAULT_TIER_PRESETS)
    st.session_state[f"{prefix}_grading_initialized"] = True


def collect_grading_config_from_session(prefix):
    mode = st.session_state.get(f"{prefix}_grading_mode", "none")
    if mode == "none":
        return {"mode": "none"}
    cfg = {
        "mode": mode,
        "score_type": st.session_state.get(f"{prefix}_score_type", "absolute"),
    }
    if mode == "pass_fail":
        cfg["pass_threshold"] = float(st.session_state.get(f"{prefix}_pass_threshold", 60))
        cfg["pass_label"] = st.session_state.get(f"{prefix}_pass_label", "合格")
        cfg["fail_label"] = st.session_state.get(f"{prefix}_fail_label", "不合格")
    elif mode == "tiers":
        tiers = []
        for t in st.session_state.get(f"{prefix}_tiers", []):
            label = str(t.get("label", "")).strip()
            if not label:
                continue
            try:
                min_score = float(t.get("min_score", 0))
            except (TypeError, ValueError):
                min_score = 0.0
            tiers.append({"min_score": min_score, "label": label})
        cfg["tiers"] = tiers
    return normalize_grading_config(cfg)


def render_grading_config_editor(prefix, grading_config=None):
    """試験作成・編集フォーム用：合格判定・段階評価の設定UI。"""
    if grading_config is not None and not st.session_state.get(f"{prefix}_grading_initialized"):
        prime_grading_session(prefix, grading_config)
    if f"{prefix}_grading_mode" not in st.session_state:
        prime_grading_session(prefix, {"mode": "none"})

    st.markdown("**総合評価・合格判定の設定**")
    st.caption(
        "Aパターン: 合格基準の数値と表示ラベルを2種類、任意に指定。"
        " Bパターン: 基準点の数（段数）・各基準の数値・表示ラベルをすべて任意に設定（「基準点を追加」「削除」）。"
        " サンプル読み込みはあくまで初期例です。"
    )
    mode_labels = {
        "none": "なし（得点のみ表示）",
        "pass_fail": "Aパターン：合格／不合格",
        "tiers": "Bパターン：段階評価（複数基準点）",
    }
    st.radio(
        "評価方式",
        ["none", "pass_fail", "tiers"],
        format_func=lambda m: mode_labels[m],
        key=f"{prefix}_grading_mode",
    )
    mode = st.session_state[f"{prefix}_grading_mode"]
    if mode == "none":
        return

    unit_labels = {"absolute": "得点（点）", "percent": "正解率（％）"}
    st.radio(
        "基準の単位",
        ["absolute", "percent"],
        format_func=lambda u: unit_labels[u],
        key=f"{prefix}_score_type",
        horizontal=True,
    )
    unit = grading_threshold_unit_label({"mode": mode, "score_type": st.session_state[f"{prefix}_score_type"]})

    if mode == "pass_fail":
        c1, c2, c3 = st.columns(3)
        with c1:
            st.number_input(
                f"合格基準（{unit}以上）",
                min_value=0.0,
                max_value=100.0 if unit == "％" else 10000.0,
                step=1.0,
                key=f"{prefix}_pass_threshold",
            )
        with c2:
            st.text_input(
                "基準以上の表示（任意）",
                key=f"{prefix}_pass_label",
                placeholder="例: 合格 / Pass",
            )
        with c3:
            st.text_input(
                "基準未満の表示（任意）",
                key=f"{prefix}_fail_label",
                placeholder="例: 不合格 / Fail",
            )
    elif mode == "tiers":
        if f"{prefix}_tiers" not in st.session_state:
            st.session_state[f"{prefix}_tiers"] = copy.deepcopy(DEFAULT_TIER_PRESETS)
        st.caption(
            f"段数・各基準の数値（{unit}）・表示ラベルはすべて自由です。"
            f" 得点が基準以上のうち最も高い段のラベルを表示します（例: 3段だけ・基準 85/70/50 なども可）。"
        )
        tiers = st.session_state[f"{prefix}_tiers"]
        remove_idx = None
        for i, tier in enumerate(tiers):
            col_min, col_label, col_del = st.columns([2, 3, 1])
            with col_min:
                st.number_input(
                    f"基準{i + 1}",
                    min_value=0.0,
                    max_value=100.0 if unit == "％" else 10000.0,
                    step=1.0,
                    value=float(tier.get("min_score", 0)),
                    key=f"{prefix}_tier_min_{i}",
                )
            with col_label:
                st.text_input(
                    "表示ラベル（任意の文字列）",
                    value=tier.get("label", ""),
                    placeholder="例: ARank",
                    key=f"{prefix}_tier_label_{i}",
                )
            with col_del:
                if len(tiers) > 1 and st.button("削除", key=f"{prefix}_tier_del_{i}"):
                    remove_idx = i
            tiers[i] = {
                "min_score": st.session_state[f"{prefix}_tier_min_{i}"],
                "label": st.session_state[f"{prefix}_tier_label_{i}"],
            }
        st.session_state[f"{prefix}_tiers"] = tiers
        if remove_idx is not None:
            tiers.pop(remove_idx)
            st.session_state[f"{prefix}_tiers"] = tiers
            st.session_state[f"{prefix}_grading_initialized"] = True
            st.rerun()
        if st.button("基準点を追加", key=f"{prefix}_tier_add"):
            tiers.append({"min_score": 0, "label": ""})
            st.session_state[f"{prefix}_tiers"] = tiers
            st.rerun()
        if st.button("サンプル（90/80/75/60/0点）を読み込む", key=f"{prefix}_tier_preset"):
            st.session_state[f"{prefix}_tiers"] = copy.deepcopy(DEFAULT_TIER_PRESETS)
            st.session_state[f"{prefix}_grading_initialized"] = True
            st.rerun()


# ============================================================
# 選択肢の新形式（ラベル + 説明文）サポート
# ============================================================
# データ構造:
#   q["options"]       : ラベルのリスト (例: ["A", "B", "C"])
#   q["option_texts"]  : 各ラベルに対応する説明文のリスト (例: ["50%", "20%", "5%"])
#   q["option_explanations"] : 各ラベルに対する補足解説 (キーはラベル)
#   q["correct_answer"] : 択一=ラベル文字列 / 複数=ラベル文字列のリスト
# 旧形式（後方互換）:
#   q["options"]       : 表示文字列そのまま (例: ["A. テキスト1", "B. テキスト2"])
#   q["option_texts"]  : 存在しない or 長さ不一致
#   q["correct_answer"]: 表示文字列そのまま (択一) / 表示文字列のリスト (複数)

LABEL_KIND_ALPHA = "A-Z (アルファベット)"
LABEL_KIND_NUM = "1-99 (数字)"
LABEL_KIND_ROMAN = "i-x (ローマ数字小)"
LABEL_KIND_KANA = "ア-ヲ (カタカナ)"
LABEL_KIND_MANUAL = "手動入力"
LABEL_KIND_OPTIONS = [LABEL_KIND_ALPHA, LABEL_KIND_NUM, LABEL_KIND_ROMAN, LABEL_KIND_KANA, LABEL_KIND_MANUAL]


def suggest_option_labels(kind, count):
    """ラベル方式に応じて count 個のラベル候補を返す。"""
    count = max(0, int(count))
    if kind == LABEL_KIND_ALPHA:
        out = []
        for i in range(count):
            if i < 26:
                out.append(chr(ord("A") + i))
            else:
                out.append(f"A{i - 25}")
        return out
    if kind == LABEL_KIND_NUM:
        return [str(i + 1) for i in range(count)]
    if kind == LABEL_KIND_ROMAN:
        base = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
        return [base[i] if i < len(base) else str(i + 1) for i in range(count)]
    if kind == LABEL_KIND_KANA:
        kana = list(
            "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲ"
        )
        return [kana[i] if i < len(kana) else str(i + 1) for i in range(count)]
    return ["" for _ in range(count)]


def is_new_option_format(q):
    """option_texts 配列が options と同じ長さで存在すれば新形式。"""
    if not isinstance(q, dict):
        return False
    opts = q.get("options") or []
    texts = q.get("option_texts")
    return isinstance(texts, list) and len(texts) == len(opts) and len(opts) > 0


def format_option_display(label, text):
    """ラベル + 説明文を表示用に結合する。"""
    label = "" if label is None else str(label).strip()
    text = "" if text is None else str(text).strip()
    if label and text:
        return f"{label}. {text}"
    return label or text


def get_option_display_for(q, value):
    """設問 q の選択肢の値（label or 旧形式の表示文字列）から、表示文字列を組み立てる。
    値が options に含まれない場合は値そのままを返す。"""
    if value is None:
        return ""
    opts = q.get("options") or []
    if is_new_option_format(q):
        if value in opts:
            idx = opts.index(value)
            return format_option_display(value, q["option_texts"][idx])
        return str(value)
    return str(value)


def ensure_option_texts(q):
    """option_texts を options と同じ長さに揃える。新形式・旧形式どちらでも安全。
    旧形式の場合は option_texts を作成せず（残さず）終了する。"""
    if q.get("type") not in ("択一選択", "複数選択"):
        return
    opts = q.get("options") or []
    texts = q.get("option_texts")
    if isinstance(texts, list):
        if len(texts) < len(opts):
            texts = list(texts) + [""] * (len(opts) - len(texts))
        elif len(texts) > len(opts):
            texts = texts[: len(opts)]
        q["option_texts"] = texts


def options_total_label_kind_default(q):
    """新形式の場合、ラベル群から推定される方式を返す。判別できなければ手動。"""
    if not is_new_option_format(q):
        return LABEL_KIND_ALPHA
    labels = q.get("options") or []
    if not labels:
        return LABEL_KIND_ALPHA
    if all(str(l).isdigit() for l in labels):
        return LABEL_KIND_NUM
    if all(len(str(l)) == 1 and str(l).isalpha() and str(l).isascii() for l in labels):
        return LABEL_KIND_ALPHA
    return LABEL_KIND_MANUAL


TEXT_QUESTION_TYPES = ("テキスト（記述式）", "テキストエリア（長文記述）")


def normalize_questions_schema(questions):
    """設問IDの欠落補完と選択肢解説辞書の同期。"""
    for q in questions:
        if not q.get("id"):
            q["id"] = f"q_{uuid.uuid4().hex[:4]}"
        if q["type"] in ["択一選択", "複数選択"]:
            ensure_option_texts(q)
            ensure_option_explanations(q)
        elif q["type"] in ["○×式"]:
            ensure_option_explanations(q)
        # 記述式の「必須」フラグは未設定なら False
        if q["type"] in TEXT_QUESTION_TYPES:
            q["required"] = bool(q.get("required", False))
    return questions


def build_schema_snapshot(schema):
    """提出時点のスキーマを採点表示に必要な最小限の情報で保存する。"""
    snapshot = []
    for q in schema:
        item = {
            "id": q.get("id"),
            "type": q.get("type"),
            "question": q.get("question"),
            "category": q.get("category"),
            "points": q.get("points"),
            "options": list(q.get("options") or []),
            "correct_answer": q.get("correct_answer"),
            "explanation": q.get("explanation", ""),
            "option_explanations": dict(q.get("option_explanations") or {}),
        }
        if isinstance(q.get("option_texts"), list):
            item["option_texts"] = list(q["option_texts"])
        if q.get("type") in TEXT_QUESTION_TYPES:
            item["required"] = bool(q.get("required", False))
        snapshot.append(item)
    return snapshot


def load_submission_schema(row, fallback_schema):
    """submissions 1件分の表示用スキーマを取得する。
    schema_snapshot があればそれを最優先、無ければ results に保存された
    question_id をキーに現スキーマから引き当て、最後にインデックス整合を fallback とする。
    戻り値: (display_schema, source) 。source は 'snapshot' / 'current' / 'mixed' / 'none'。
    """
    fallback_by_id = {q.get("id"): q for q in (fallback_schema or []) if q.get("id")}

    snap_raw = None
    if isinstance(row, dict):
        snap_raw = row.get("schema_snapshot")
        results_raw = row.get("results")
    else:
        snap_raw = getattr(row, "schema_snapshot", None) if hasattr(row, "schema_snapshot") else None
        results_raw = row["results"] if "results" in row.index else None

    if snap_raw:
        try:
            snap = json.loads(snap_raw)
            if isinstance(snap, list) and snap:
                return snap, "snapshot"
        except (json.JSONDecodeError, TypeError):
            pass

    try:
        results = json.loads(results_raw) if results_raw else []
    except (json.JSONDecodeError, TypeError):
        results = []

    rebuilt = []
    matched_any = False
    for res in results:
        qid = res.get("question_id") if isinstance(res, dict) else None
        cur_q = fallback_by_id.get(qid)
        if cur_q:
            matched_any = True
            rebuilt.append(cur_q)
        else:
            rebuilt.append({
                "id": qid,
                "type": "択一選択",
                "question": "（旧版設問・データなし）",
                "category": "",
                "points": float(res.get("earned", 0) or 0) if isinstance(res, dict) else 0,
                "options": [],
                "correct_answer": "",
                "explanation": "",
                "option_explanations": {},
            })
    if rebuilt:
        return rebuilt, ("current" if matched_any and len(rebuilt) == len(results) else "mixed")
    return list(fallback_schema or []), "none"


def pair_results_with_schema(display_schema, results):
    """display_schema の各設問に対応する result を question_id で結合する。
    一致が見つからなければ None。インデックス突合は行わない。"""
    by_qid = {}
    for r in results or []:
        if isinstance(r, dict):
            qid = r.get("question_id")
            if qid:
                by_qid[qid] = r
    return [by_qid.get(q.get("id")) for q in display_schema]


def _qtype_slug(qtype):
    return {
        "択一選択": "sel",
        "複数選択": "mul",
        "○×式": "tf",
        "テキスト（記述式）": "txt",
        "テキストエリア（長文記述）": "lng",
    }.get(qtype, "oth")


def clear_edit_widget_session_state(edit_id=None):
    """編集画面のウィジェット用 session_state をクリア（再編集時の競合防止）。"""
    static_keys = {"edit_title_inp", "edit_desc_inp", "edit_limit_inp"}
    prefix = f"edit_{edit_id}_" if edit_id else None
    for k in list(st.session_state.keys()):
        if not isinstance(k, str):
            continue
        if k in static_keys:
            del st.session_state[k]
        elif prefix and k.startswith(prefix):
            del st.session_state[k]


def prime_edit_form_widgets(title, desc, limit_time):
    """編集フォームのウィジェット初期値を session_state に直接設定。"""
    st.session_state.edit_title_inp = title
    st.session_state.edit_desc_inp = desc or ""
    st.session_state.edit_limit_inp = int(limit_time or 0)


def load_exam_into_builder(exam_id, as_copy=False):
    row = fetch_exam_record(exam_id)
    if not row:
        return False
    ex_id, title, desc, status, limit_time, schema_str, created_by, grading_raw = row
    schema = normalize_questions_schema(json.loads(schema_str))
    grading_cfg = parse_grading_config(grading_raw)
    if as_copy:
        st.session_state.exam_form_mode = "create"
        st.session_state.editing_exam_id = None
        st.session_state.questions_builder = copy.deepcopy(schema)
        st.session_state.new_exam_title = f"{title}（コピー）"
        st.session_state.new_exam_desc = desc or ""
        st.session_state.new_exam_limit = int(limit_time)
        st.session_state.new_exam_id = f"EX-{uuid.uuid4().hex[:6].upper()}"
        st.session_state.pop("new_grading_grading_initialized", None)
        prime_grading_session("new_grading", grading_cfg)
    else:
        clear_edit_widget_session_state(edit_id=ex_id)
        st.session_state.exam_form_mode = "edit"
        st.session_state.editing_exam_id = ex_id
        st.session_state.edit_questions_builder = copy.deepcopy(schema)
        st.session_state.edit_exam_title = title
        st.session_state.edit_exam_desc = desc or ""
        st.session_state.edit_exam_limit = int(limit_time or 0)
        prime_edit_form_widgets(title, desc, limit_time)
        edit_prefix = f"edit_grading_{ex_id}"
        st.session_state.pop(f"{edit_prefix}_grading_initialized", None)
        prime_grading_session(edit_prefix, grading_cfg)
    return True


def ensure_option_explanations(q):
    """選択肢ごとの解説辞書を選択肢リストと同期する。"""
    opts = q.get("options") or []
    raw = q.get("option_explanations")
    if not isinstance(raw, dict):
        raw = {}
    q["option_explanations"] = {o: raw.get(o, "") for o in opts}


def get_selected_options(student_answer, q_type):
    """選択された選択肢キー（新形式ではラベル、旧形式では表示文字列）のリスト。"""
    if q_type == "複数選択":
        if isinstance(student_answer, list):
            return [str(x) for x in student_answer]
        return [str(student_answer)] if student_answer not in (None, "") else []
    if student_answer in (None, ""):
        return []
    return [str(student_answer)]


def format_answer_display(ans):
    """汎用版（質問情報が無いケース用）。"""
    if isinstance(ans, list):
        return ", ".join(str(x) for x in ans) if ans else "（未回答）"
    if ans in (None, ""):
        return "（未回答）"
    return str(ans)


def format_answer_display_for_q(q, ans):
    """設問定義 q に基づいて、回答 ans を表示用文字列に整形する。
    択一・複数の新形式ではラベルから "label. text" に展開する。旧形式は ans をそのまま使う。
    """
    qtype = (q or {}).get("type", "")
    if qtype in ("択一選択", "複数選択") and is_new_option_format(q):
        if isinstance(ans, list):
            if not ans:
                return "（未回答）"
            return ", ".join(get_option_display_for(q, x) for x in ans)
        if ans in (None, ""):
            return "（未回答）"
        return get_option_display_for(q, ans)
    return format_answer_display(ans)


def render_option_explanation_inputs(q, key_prefix, qid, tslug):
    """択一・複数・○×：各選択肢への補足解説入力欄（設問あたり1回だけ呼ぶこと）。
    新形式（ラベル/説明分離）の場合は「A. 50%」の解説、というラベルで表示する。"""
    if q["type"] not in ["択一選択", "複数選択", "○×式"]:
        return
    ensure_option_explanations(q)
    if not q.get("options"):
        return
    st.caption(
        "各選択肢の補足解説（任意・受験者がその選択肢を選んだ場合に正誤結果と共に表示）"
    )
    safe_qid = str(qid).replace("-", "_")
    new_format = is_new_option_format(q)
    for j, opt in enumerate(q["options"]):
        if new_format:
            label_for_input = format_option_display(opt, q["option_texts"][j])
        else:
            label_for_input = opt
        q["option_explanations"][opt] = st.text_input(
            f"「{label_for_input}」の補足解説",
            value=q["option_explanations"].get(opt, ""),
            key=f"{key_prefix}_optexp_{safe_qid}_{tslug}_{j}",
            placeholder="例: この選択肢は〜を意味します（空欄なら非表示）",
        )


def render_option_explanations_display(q_info, res):
    """受験者が選んだ選択肢に解説があれば表示。"""
    q_type = q_info.get("type", "")
    if q_type not in ["択一選択", "複数選択", "○×式"]:
        return
    opt_exp = q_info.get("option_explanations") or {}
    if not isinstance(opt_exp, dict):
        return
    for opt in get_selected_options(res.get("student_answer"), q_type):
        text = (opt_exp.get(opt) or "").strip()
        if text:
            display_label = get_option_display_for(q_info, opt)
            st.info(f"💡 選択肢「{display_label}」: {text}")


def render_question_explanation_text(q_info):
    exp = (q_info.get("explanation") or "").strip()
    if exp:
        st.write(f"📝 解説: {exp}")


def render_question_result_block(q_info, res, question_no=None):
    """設問1件分の採点結果表示（画面・明細共通）。"""
    prefix = f"問 {question_no}: " if question_no else ""
    q_text = q_info["question"]
    is_correct = res.get("is_correct", False)
    earned = res.get("earned", 0)
    feedback = res.get("feedback", "")
    status_text = "正解" if is_correct else "不正解（または部分点）"
    color = "#10B981" if is_correct else "#F43F5E"
    st.markdown(
        f"<div style='border-left: 4px solid {color}; padding-left: 15px; margin-bottom: 16px;'>",
        unsafe_allow_html=True,
    )
    st.markdown(f"**{prefix}{q_text}**")
    st.write(f"獲得点数: {earned}点 / {q_info['points']}点 ({status_text})")
    st.write(f"回答: {format_answer_display_for_q(q_info, res.get('student_answer'))}")
    st.write(f"模範解答: {format_answer_display_for_q(q_info, q_info.get('correct_answer'))}")
    if feedback:
        st.info(f"AI講評: {feedback}")
    render_option_explanations_display(q_info, res)
    render_question_explanation_text(q_info)
    st.markdown("</div>", unsafe_allow_html=True)


def build_option_explanations_html(q_info, res):
    """メール用：選択肢解説のHTML断片。"""
    q_type = q_info.get("type", "")
    if q_type not in ["択一選択", "複数選択", "○×式"]:
        return ""
    opt_exp = q_info.get("option_explanations") or {}
    if not isinstance(opt_exp, dict):
        return ""
    parts = []
    for opt in get_selected_options(res.get("student_answer"), q_type):
        text = (opt_exp.get(opt) or "").strip()
        if text:
            display_label = get_option_display_for(q_info, opt)
            parts.append(
                f'<p style="margin: 5px 0; color: #4338CA; background: #EEF2FF; padding: 8px; border-radius: 4px;">'
                f"<strong>選択肢「{display_label}」:</strong> {text}</p>"
            )
    return "".join(parts)


def encode_csv_dataframe(df, encoding_label):
    """DataFrameをCSVバイト列に変換（UTF-8 または Shift_JIS）。"""
    if encoding_label == "UTF-8":
        return df.to_csv(index=False).encode("utf-8-sig")
    return df.to_csv(index=False).encode("cp932", errors="replace")


VALID_QUESTION_TYPES = [
    "択一選択",
    "複数選択",
    "○×式",
    "テキスト（記述式）",
    "テキストエリア（長文記述）",
]

CSV_QUESTION_COLUMNS = [
    "設問ID",
    "設問形式",
    "問題文",
    "カテゴリ",
    "配点",
    "必須",
    "選択肢ラベル",
    "選択肢",
    "正解",
    "問題解説",
    "選択肢解説",
]

# exams.schema（JSON配列の各設問オブジェクト）と CSV 列の対応定義
CSV_DB_SCHEMA_SPEC = [
    {
        "CSV列名": "設問ID",
        "DBフィールド": "id",
        "必須": "-",
        "説明": "既存設問を編集する場合に指定（空欄なら自動採番）。既存IDと一致すれば置換、それ以外は新規。",
    },
    {
        "CSV列名": "設問形式",
        "DBフィールド": "type",
        "必須": "○",
        "説明": "択一選択 / 複数選択 / ○×式 / テキスト（記述式） / テキストエリア（長文記述）",
    },
    {
        "CSV列名": "問題文",
        "DBフィールド": "question",
        "必須": "○",
        "説明": "問題文テキスト",
    },
    {
        "CSV列名": "カテゴリ",
        "DBフィールド": "category",
        "必須": "-",
        "説明": "分野名（空欄時は「一般」）",
    },
    {
        "CSV列名": "配点",
        "DBフィールド": "points",
        "必須": "-",
        "説明": "数値（空欄時は 20）",
    },
    {
        "CSV列名": "必須",
        "DBフィールド": "required",
        "必須": "-",
        "説明": "記述式の必須指定（○/×, true/false, はい/いいえ, 1/0 のいずれか）。択一・複数・○×式は常に必須として扱われる。",
    },
    {
        "CSV列名": "選択肢ラベル",
        "DBフィールド": "options",
        "必須": "△",
        "説明": "新形式の選択肢ラベル（A,B,Cや1,2,3など）。カンマ区切り。択一・複数で記述する場合に使用。空欄なら「選択肢」列を旧形式として読み取る。",
    },
    {
        "CSV列名": "選択肢",
        "DBフィールド": "option_texts",
        "必須": "△",
        "説明": "新形式では各ラベルに対応する説明文をカンマ区切り（例: 50%,20%,5%）。旧形式の場合は表示用テキスト自体を入れる。",
    },
    {
        "CSV列名": "正解",
        "DBフィールド": "correct_answer",
        "必須": "△",
        "説明": "新形式ではラベル指定（択一: A / 複数: A,C）。旧形式では選択肢文字列。記述式は模範解答文字列。",
    },
    {
        "CSV列名": "問題解説",
        "DBフィールド": "explanation",
        "必須": "-",
        "説明": "設問全体の解説（採点結果メール・画面に表示）",
    },
    {
        "CSV列名": "選択肢解説",
        "DBフィールド": "option_explanations",
        "必須": "-",
        "説明": "選択肢と同順のカンマ区切り。JSONオブジェクト {選択肢:解説} に変換",
    },
]

CSV_TEMPLATE_SPEC_ROW = {
    "設問ID": "【DB:id】任意（空欄→自動採番）。既存IDで上書き、新規IDは追加扱い",
    "設問形式": "【DB:type】択一選択|複数選択|○×式|テキスト（記述式）|テキストエリア（長文記述）",
    "問題文": "【DB:question】必須",
    "カテゴリ": "【DB:category】任意（空欄→一般）",
    "配点": "【DB:points】数値（空欄→20）",
    "必須": "【DB:required】記述式の必須指定。○/×, true/false, はい/いいえ, 1/0",
    "選択肢ラベル": "【DB:options(新形式)】A,B,Cや1,2,3など。空欄なら旧形式扱い",
    "選択肢": "【DB:option_texts/options】新形式=各ラベルの説明文。旧形式=表示文字列。カンマ区切り",
    "正解": "【DB:correct_answer】新形式: ラベル(A,A,C…) / 旧形式: 選択肢文字列",
    "問題解説": "【DB:explanation】任意",
    "選択肢解説": "【DB:option_explanations】選択肢と同順カンマ区切り",
}

CSV_SAMPLE_QUESTION_ROWS = [
    {
        "設問ID": "",
        "設問形式": "択一選択",
        "問題文": "サンプル：日本の人口に占める高齢者の割合は約何%か",
        "カテゴリ": "一般",
        "配点": "20",
        "必須": "",
        "選択肢ラベル": "A,B,C",
        "選択肢": "50%,20%,10%",
        "正解": "B",
        "問題解説": "問題全体の解説（任意）",
        "選択肢解説": "高すぎる例,概ね正しい,低すぎる例",
    },
    {
        "設問ID": "",
        "設問形式": "複数選択",
        "問題文": "Webで使われるセキュアな通信プロトコルをすべて選んでください",
        "カテゴリ": "一般",
        "配点": "20",
        "必須": "",
        "選択肢ラベル": "A,B,C,D",
        "選択肢": "HTTP,HTTPS,FTP,SSH",
        "正解": "B,D",
        "問題解説": "暗号化されているかが判断ポイント",
        "選択肢解説": ",HTTPS は TLS で暗号化,,SSH は安全なリモートアクセス用",
    },
    {
        "設問ID": "",
        "設問形式": "○×式",
        "問題文": "主キーは重複してよい",
        "カテゴリ": "DB",
        "配点": "10",
        "必須": "",
        "選択肢ラベル": "",
        "選択肢": "",
        "正解": "× (誤り)",
        "問題解説": "主キーは一意である必要があります",
        "選択肢解説": "",
    },
    {
        "設問ID": "",
        "設問形式": "テキスト（記述式）",
        "問題文": "APIの略称を英語で答えよ",
        "カテゴリ": "Web",
        "配点": "20",
        "必須": "○",
        "選択肢ラベル": "",
        "選択肢": "",
        "正解": "Application Programming Interface",
        "問題解説": "",
        "選択肢解説": "",
    },
    {
        "設問ID": "",
        "設問形式": "テキストエリア（長文記述）",
        "問題文": "Cookieの役割とセッション維持の理由を説明せよ",
        "カテゴリ": "Web",
        "配点": "20",
        "必須": "×",
        "選択肢ラベル": "",
        "選択肢": "",
        "正解": "HTTPはステートレスなためセッション識別にCookieを利用する",
        "問題解説": "ステートレス性とセッションIDの説明が要点",
        "選択肢解説": "",
    },
]


def build_questions_csv_column_spec(encoding_label):
    """DB構造対応表（列定義）CSVを生成する。"""
    df = pd.DataFrame(CSV_DB_SCHEMA_SPEC)
    return encode_csv_dataframe(df, encoding_label)


def build_questions_csv_template(encoding_label, template_kind="sample"):
    """
    設問インポート用CSVテンプレートを生成する。
    template_kind: "empty"=ヘッダのみ, "structure"=列説明行+ヘッダ, "sample"=説明行+サンプル5問
    """
    rows = []
    if template_kind in ("structure", "sample"):
        rows.append(CSV_TEMPLATE_SPEC_ROW)
    if template_kind == "sample":
        rows.extend(CSV_SAMPLE_QUESTION_ROWS)
    if rows:
        df = pd.DataFrame(rows, columns=CSV_QUESTION_COLUMNS)
    else:
        df = pd.DataFrame(columns=CSV_QUESTION_COLUMNS)
    return encode_csv_dataframe(df, encoding_label)


def _csv_row_is_skipped(qtype, question_text):
    """テンプレートの説明行・コメント行を取り込み対象外とする。"""
    if qtype.startswith("#") or qtype.startswith("【"):
        return True
    if question_text.startswith("#") or question_text.startswith("【"):
        return True
    if qtype in ("（説明）", "説明", "-", "―"):
        return True
    return False


def _csv_cell_split(value, separator=","):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []
    return [p.strip() for p in text.split(separator) if p.strip()]


def _csv_cell_split_keep_empty(value, separator=","):
    """空セル位置を保ったまま分割する（選択肢と1:1で対応させたい列向け）。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value)
    if not text.strip():
        return []
    return [p.strip() for p in text.split(separator)]


def _csv_row_val(row, col, default=""):
    if col not in row.index:
        return default
    val = row[col]
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return str(val).strip()


def _csv_parse_bool(value):
    """CSV のセル値を真偽値に解釈する。空欄は False。"""
    if value is None:
        return False
    text = str(value).strip().lower()
    if not text:
        return False
    return text in {"○", "◯", "○ (正しい)", "true", "yes", "y", "はい", "必須", "1", "on"}


def _csv_row_to_question(row, row_no):
    """CSVの1行を設問オブジェクトに変換する。"""
    qtype = _csv_row_val(row, "設問形式")
    if qtype not in VALID_QUESTION_TYPES:
        raise ValueError(
            f"設問形式が不正です: 「{qtype}」。次のいずれかを指定してください: {', '.join(VALID_QUESTION_TYPES)}"
        )

    question_text = _csv_row_val(row, "問題文")
    if not question_text:
        raise ValueError("問題文が空です。")

    category = _csv_row_val(row, "カテゴリ") or "一般"
    points_raw = _csv_row_val(row, "配点") or "20"
    try:
        points = float(points_raw)
    except ValueError as exc:
        raise ValueError(f"配点が数値ではありません: {points_raw}") from exc

    explanation = _csv_row_val(row, "問題解説")
    labels = _csv_cell_split(_csv_row_val(row, "選択肢ラベル"))
    texts = _csv_cell_split(_csv_row_val(row, "選択肢"))
    correct_raw = _csv_row_val(row, "正解")
    option_explanations = {}

    # 設問ID 列があり、かつ値が入っていればそれを採用（既存設問の上書き編集に使う）。
    # 空欄なら新規 UUID を採番。
    explicit_id = _csv_row_val(row, "設問ID")
    q_id = explicit_id if explicit_id else f"q_{uuid.uuid4().hex[:4]}"

    q = {
        "id": q_id,
        "type": qtype,
        "question": question_text,
        "category": category,
        "points": points,
        "options": [],
        "correct_answer": "",
        "explanation": explanation,
        "option_explanations": {},
    }

    if qtype in ["択一選択", "複数選択"]:
        use_new_format = bool(labels)
        if use_new_format:
            if not texts:
                raise ValueError(
                    "「選択肢ラベル」を指定した場合は、対応する「選択肢」（説明文）もカンマ区切りで指定してください。"
                )
            if len(labels) != len(texts):
                raise ValueError(
                    f"選択肢ラベル({len(labels)}件)と選択肢({len(texts)}件)の個数が一致しません。"
                )
            q["options"] = labels
            q["option_texts"] = texts
            if qtype == "複数選択":
                correct_list = _csv_cell_split(correct_raw)
                if not correct_list:
                    raise ValueError("複数選択では「正解」にラベルをカンマ区切りで指定してください（例: A,C）。")
                for c in correct_list:
                    if c not in labels:
                        raise ValueError(f"正解「{c}」が選択肢ラベルに含まれていません。")
                q["correct_answer"] = correct_list
            else:
                if not correct_raw:
                    raise ValueError("択一選択では「正解」列（ラベル）が必須です。")
                if correct_raw not in labels:
                    raise ValueError(f"正解「{correct_raw}」が選択肢ラベルに含まれていません。")
                q["correct_answer"] = correct_raw
            opt_exp_parts = _csv_cell_split_keep_empty(_csv_row_val(row, "選択肢解説"))
            for i, lab in enumerate(labels):
                if i < len(opt_exp_parts) and opt_exp_parts[i]:
                    option_explanations[lab] = opt_exp_parts[i]
        else:
            # 旧形式（後方互換）：「選択肢」列のみ・選択肢文字列で記述
            if not texts:
                raise ValueError("択一選択・複数選択では「選択肢ラベル」または「選択肢」列が必須です。")
            q["options"] = texts
            if qtype == "複数選択":
                correct_list = _csv_cell_split(correct_raw)
                if not correct_list:
                    raise ValueError("複数選択では「正解」に正解選択肢をカンマ区切りで指定してください。")
                q["correct_answer"] = correct_list
            else:
                if not correct_raw:
                    raise ValueError("択一選択では「正解」列が必須です。")
                if correct_raw not in texts:
                    raise ValueError(f"正解「{correct_raw}」が選択肢に含まれていません。")
                q["correct_answer"] = correct_raw
            opt_exp_parts = _csv_cell_split_keep_empty(_csv_row_val(row, "選択肢解説"))
            for i, opt in enumerate(texts):
                if i < len(opt_exp_parts) and opt_exp_parts[i]:
                    option_explanations[opt] = opt_exp_parts[i]
    elif qtype == "○×式":
        q["options"] = ["○ (正しい)", "× (誤り)"]
        if correct_raw in ("○", "正しい", "○ (正しい)"):
            q["correct_answer"] = "○ (正しい)"
        elif correct_raw in ("×", "誤り", "× (誤り)"):
            q["correct_answer"] = "× (誤り)"
        elif correct_raw in q["options"]:
            q["correct_answer"] = correct_raw
        else:
            raise ValueError('正解は「○ (正しい)」または「× (誤り)」を指定してください。')
        tf_exp_parts = _csv_cell_split_keep_empty(_csv_row_val(row, "選択肢解説"))
        for i, opt in enumerate(q["options"]):
            if i < len(tf_exp_parts) and tf_exp_parts[i]:
                option_explanations[opt] = tf_exp_parts[i]
    else:
        if not correct_raw:
            raise ValueError("記述式では「正解」（模範解答）列が必須です。")
        q["correct_answer"] = correct_raw
        q["required"] = _csv_parse_bool(_csv_row_val(row, "必須"))

    q["option_explanations"] = option_explanations
    return q


def parse_questions_from_csv(uploaded_file, encoding_label):
    """アップロードされたCSVから設問リストを生成する。戻り値: (questions, errors)。"""
    encoding = "utf-8-sig" if encoding_label == "UTF-8" else "cp932"
    try:
        raw = uploaded_file.getvalue()
        df = pd.read_csv(io.BytesIO(raw), encoding=encoding, dtype=str).fillna("")
    except Exception as exc:
        return [], [f"CSVの読み込みに失敗しました: {exc}"]

    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in ("設問形式", "問題文") if c not in df.columns]
    if missing:
        return [], [f"必須列がありません: {', '.join(missing)}。テンプレートCSVを参照してください。"]

    questions = []
    errors = []
    for idx, row in df.iterrows():
        if all(str(row.get(c, "")).strip() == "" for c in df.columns):
            continue
        qtype_preview = _csv_row_val(row, "設問形式")
        question_preview = _csv_row_val(row, "問題文")
        if _csv_row_is_skipped(qtype_preview, question_preview):
            continue
        row_no = int(idx) + 2
        try:
            questions.append(_csv_row_to_question(row, row_no))
        except ValueError as exc:
            errors.append(f"{row_no}行目: {exc}")

    if not questions and not errors:
        errors.append("取り込める設問がありません。")
    return questions, errors


def render_csv_template_download(section_id):
    """DB構造に沿ったテンプレート・列定義書のダウンロード（文字コード選択付き）。"""
    st.markdown("**テンプレートのダウンロード（DB `exams.schema` 形式）**")
    st.caption(
        "取り込み先は exams テーブルの schema 列（JSON配列）です。"
        " 1行が1設問に対応し、取り込み時に id は自動採番されます。"
    )
    tpl_encoding = st.radio(
        "テンプレートの文字コード",
        ["Shift_JIS", "UTF-8"],
        index=0,
        horizontal=True,
        key=f"csv_tpl_enc_{section_id}",
    )

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.download_button(
            label="空テンプレート（ヘッダのみ）",
            data=build_questions_csv_template(tpl_encoding, "empty"),
            file_name=f"exam_questions_empty_{tpl_encoding}.csv",
            mime="text/csv",
            key=f"csv_tpl_empty_{section_id}",
        )
    with col_b:
        st.download_button(
            label="構造説明付きテンプレート",
            data=build_questions_csv_template(tpl_encoding, "structure"),
            file_name=f"exam_questions_structure_{tpl_encoding}.csv",
            mime="text/csv",
            key=f"csv_tpl_struct_{section_id}",
        )
    with col_c:
        st.download_button(
            label="サンプル設問付きテンプレート",
            data=build_questions_csv_template(tpl_encoding, "sample"),
            file_name=f"exam_questions_sample_{tpl_encoding}.csv",
            mime="text/csv",
            key=f"csv_tpl_sample_{section_id}",
        )

    st.download_button(
        label="列定義書（DB対応表）をダウンロード",
        data=build_questions_csv_column_spec(tpl_encoding),
        file_name=f"exam_questions_db_mapping_{tpl_encoding}.csv",
        mime="text/csv",
        key=f"csv_tpl_mapping_{section_id}",
    )

    with st.expander("DB（exams.schema）とCSV列の対応表を表示", expanded=False):
        st.dataframe(pd.DataFrame(CSV_DB_SCHEMA_SPEC), use_container_width=True, hide_index=True)
        st.markdown(
            "**JSON保存時の自動項目:** `id`（取り込み時に自動付与） / "
            "`○×式` の `options` は `○ (正しい)`, `× (誤り)` に自動設定"
        )


def render_csv_questions_import(section_id, questions_session_key, widget_prefix=None, edit_exam_id=None):
    """設問のCSV取り込みUI（新規作成・編集の両方で使用）。"""
    render_csv_template_download(section_id)

    with st.expander("CSVから設問を取り込む", expanded=False):
        st.markdown(
            "ダウンロードしたテンプレートに従って作成したCSVを読み込みます。"
            " **選択肢**・**正解**・**選択肢解説**はセル内 **カンマ区切り** です。"
            " 2行目の【DB:...】説明行は自動でスキップされます。"
        )
        enc_col, mode_col = st.columns(2)
        with enc_col:
            csv_encoding = st.radio(
                "取り込むCSVファイルの文字コード",
                ["Shift_JIS", "UTF-8"],
                index=0,
                horizontal=True,
                key=f"csv_import_enc_{section_id}",
            )
        with mode_col:
            import_mode = st.radio(
                "取り込み方法",
                ["既存の設問を置き換える", "既存の設問の末尾に追加"],
                index=0,
                key=f"csv_import_mode_{section_id}",
            )

        uploaded = st.file_uploader(
            "設問CSVファイルを選択",
            type=["csv"],
            key=f"csv_import_file_{section_id}",
        )

        if st.button("CSVを取り込む", type="primary", key=f"csv_import_btn_{section_id}"):
            if uploaded is None:
                st.error("CSVファイルを選択してください。")
                return
            parsed, errs = parse_questions_from_csv(uploaded, csv_encoding)
            if errs:
                for err in errs:
                    st.error(err)
            if parsed:
                if import_mode == "既存の設問を置き換える":
                    if edit_exam_id:
                        clear_edit_widget_session_state(edit_id=edit_exam_id)
                    st.session_state[questions_session_key] = normalize_questions_schema(
                        copy.deepcopy(parsed)
                    )
                    st.success(f"{len(parsed)} 件の設問を取り込みました（既存を置換）。")
                else:
                    # 末尾追加。ただし CSV 側で既存と同じ「設問ID」が指定されていれば
                    # その設問を上書き、それ以外は末尾に追加する（ID 維持のマージ）。
                    current = copy.deepcopy(st.session_state.get(questions_session_key, []))
                    by_id = {q.get("id"): idx for idx, q in enumerate(current) if q.get("id")}
                    overwritten = 0
                    appended = 0
                    for q in copy.deepcopy(parsed):
                        qid = q.get("id")
                        if qid and qid in by_id:
                            current[by_id[qid]] = q
                            overwritten += 1
                        else:
                            current.append(q)
                            appended += 1
                    st.session_state[questions_session_key] = normalize_questions_schema(current)
                    st.success(
                        f"取り込み完了: 新規追加 {appended} 件 / 既存ID上書き {overwritten} 件。"
                    )
                st.rerun()


def render_submission_details_list(submissions_df, schema, exam_id, exam_title, grading_config=None):
    """分析の前に受験者ごとの回答明細を一覧・詳細表示する（行クリックで詳細）。"""
    st.subheader("受験者ごとの回答明細")
    if submissions_df.empty:
        st.info("受験データがありません。")
        return

    if is_smtp_configured():
        st.caption(
            "受験提出時に登録メールへ採点結果を自動送信します。"
            "一覧の行をクリックすると詳細の確認と、結果メールの再送信ができます。"
        )
    else:
        st.warning(
            "メール自動送信は無効です（SMTP未設定）。"
            " `/opt/exam/.env` の SMTP_SERVER / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / EMAIL_FROM "
            "を設定後、Streamlitを再起動してください。"
        )

    row_indices = []
    summary_rows = []
    for idx, s in submissions_df.iterrows():
        row_indices.append(idx)
        tp = s["total_points"] or 1
        if not is_smtp_configured():
            mail_status = "SMTP未設定"
        elif s.get("email_sent", 0):
            mail_status = "送信済"
        else:
            mail_status = "未送信"
        grade = evaluate_score_grade(s["score"], tp, grading_config)
        row_data = {
            "氏名": s["examinee_name"],
            "メール": s["examinee_email"],
            "メール送信": mail_status,
            "得点": f"{s['score']:.1f}",
            "満点": f"{tp:.1f}",
            "正解率": f"{(s['score'] / tp) * 100:.1f}%",
            "提出日時": s["submitted_at"],
        }
        if grade is not None:
            row_data["総合評価"] = grade
        summary_rows.append(row_data)

    table_key = f"submission_detail_table_{exam_id}"
    selection = st.dataframe(
        pd.DataFrame(summary_rows),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=table_key,
    )

    selected_rows = []
    if selection is not None and hasattr(selection, "selection"):
        selected_rows = getattr(selection.selection, "rows", []) or []

    if not selected_rows:
        st.info("上の一覧から受験者の行をクリックして選択してください。")
        st.markdown("<hr>", unsafe_allow_html=True)
        return

    row_pos = selected_rows[0]
    if row_pos >= len(row_indices):
        st.warning("選択した行を読み取れませんでした。もう一度クリックしてください。")
        return

    row = submissions_df.loc[row_indices[row_pos]]
    submission_id = int(row["id"])
    results = json.loads(row["results"])

    # 表示用スキーマは「提出時スナップショット」を最優先で採用する。
    # スナップショットが無い旧データは results の question_id を頼りに現スキーマから再構成する。
    display_schema, schema_source = load_submission_schema(row, schema)
    paired_results = pair_results_with_schema(display_schema, results)

    st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
    st.markdown(f"### {row['examinee_name']} さんの回答詳細")
    st.write(f"メール: {row['examinee_email']} | 提出: {row['submitted_at']}")
    st.write(f"**合計得点: {row['score']:.1f} / {row['total_points']:.1f} 点**")
    grade = evaluate_score_grade(row["score"], row["total_points"], grading_config)
    if grade:
        st.write(f"**総合評価:** {grade}")

    if schema_source == "snapshot":
        snap_ids = {q.get("id") for q in display_schema}
        cur_ids = {q.get("id") for q in (schema or [])}
        if snap_ids != cur_ids:
            st.info(
                "この受験データは提出当時のスキーマで表示しています。"
                " 試験設問は提出後に変更されたため、現在の試験定義とは内容が異なります。"
            )
    elif schema_source in ("mixed", "none"):
        st.warning(
            "提出時のスキーマスナップショットが保存されていない旧データです。"
            " 現在の試験設問では一部の設問が再構成できなかったため、表示が不完全な可能性があります。"
        )

    if is_smtp_configured():
        mail_label = "送信済" if row.get("email_sent", 0) else "未送信（または送信失敗）"
        st.write(f"**結果メール:** {mail_label}")
        if st.button(
            "採点結果メールを再送信",
            key=f"resend_mail_{exam_id}_{submission_id}",
            type="primary",
        ):
            with st.spinner("メール送信中..."):
                ok, msg = resend_result_email_for_submission(
                    submission_id, exam_title, grading_config=grading_config
                )
            if ok:
                st.success(msg)
            else:
                st.error(msg)
            st.rerun()
    else:
        st.caption("SMTP未設定のためメールの再送信はできません。")

    # --- 受験データ削除（編集権限保持者のみ） ---
    can_delete = user_can_edit_exam(
        st.session_state.get("user_id"), st.session_state.get("role"), exam_id
    )
    if can_delete:
        with st.expander("🗑 この受験データを削除する", expanded=False):
            st.warning(
                "削除すると、本受験者の回答・採点結果・提出時スナップショットがすべて失われ、復元できません。"
                " 集計や正答率分析の対象からも除外されます。"
            )
            st.write(
                f"対象: **{row['examinee_name']} 様** "
                f"({row['examinee_email']}) / 得点 {row['score']:.1f} 点 / 提出 {row['submitted_at']}"
            )
            confirm_key = f"confirm_delete_sub_{exam_id}_{submission_id}"
            confirmed = st.checkbox(
                f"上記の受験データ（ID={submission_id}）を削除することを承諾します",
                key=confirm_key,
            )
            del_btn = st.button(
                "🚨 受験データを削除する",
                key=f"delete_sub_btn_{exam_id}_{submission_id}",
                type="primary",
                disabled=not confirmed,
            )
            if del_btn:
                if not confirmed:
                    st.error("確認チェックを入れてください。")
                else:
                    deleted, deleted_name = delete_submission(submission_id, exam_id=exam_id)
                    if deleted > 0:
                        st.success(
                            f"{deleted_name or '受験者'} 様の受験データ（ID={submission_id}）を削除しました。"
                        )
                        # 選択状態と関連 session_state をクリアして再描画
                        st.session_state.pop(table_key, None)
                        st.session_state.pop(confirm_key, None)
                        st.toast("受験データを削除しました。")
                        st.rerun()
                    else:
                        st.error(
                            "削除に失敗しました。対象データが既に削除されているか、"
                            "本試験に属さない可能性があります。"
                        )
    st.markdown("<hr style='border-color:#E5E7EB;'>", unsafe_allow_html=True)

    for i, q in enumerate(display_schema):
        res = paired_results[i]
        if not res:
            st.warning(
                f"問 {i+1}: この設問の採点データが見つかりません（設問IDの不一致など）。"
            )
            continue
        render_question_result_block(q, res, question_no=i + 1)
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("<hr>", unsafe_allow_html=True)


def _move_question(questions, src_idx, dst_idx):
    """questions リストの src_idx を dst_idx に移動する（in-place）。"""
    if not questions:
        return
    n = len(questions)
    if not (0 <= src_idx < n):
        return
    dst_idx = max(0, min(dst_idx, n - 1))
    if src_idx == dst_idx:
        return
    item = questions.pop(src_idx)
    questions.insert(dst_idx, item)


def _migrate_legacy_options_to_labeled(q, label_kind=LABEL_KIND_ALPHA):
    """旧形式の options を新形式（ラベル / 説明テキスト）に分離する。
    "A. テキスト" や "1) テキスト" のような形式を判定して分割し、
    判定できない場合は label_kind に応じて自動採番する。option_explanations のキーも更新する。"""
    opts = q.get("options") or []
    if not opts:
        q["option_texts"] = []
        return
    pattern = re.compile(r"^([A-Za-z0-9]{1,3}|[ぁ-んァ-ヶ一-龯])\s*[\.\．:：\)）、]\s*(.+)$")
    new_labels = []
    new_texts = []
    auto_labels = suggest_option_labels(label_kind, len(opts))
    for j, raw in enumerate(opts):
        s = str(raw)
        m = pattern.match(s)
        if m:
            new_labels.append(m.group(1).strip())
            new_texts.append(m.group(2).strip())
        else:
            new_labels.append(auto_labels[j] if j < len(auto_labels) else str(j + 1))
            new_texts.append(s)
    # option_explanations のキーを旧表示文字列→新ラベルへ移行
    old_exp = q.get("option_explanations") or {}
    new_exp = {}
    if isinstance(old_exp, dict):
        for j, old_opt in enumerate(opts):
            val = old_exp.get(old_opt) or old_exp.get(str(old_opt))
            if val:
                new_exp[new_labels[j]] = val
    q["options"] = new_labels
    q["option_texts"] = new_texts
    q["option_explanations"] = new_exp
    # correct_answer の移行
    ca = q.get("correct_answer")
    if q.get("type") == "複数選択":
        if isinstance(ca, list):
            migrated = []
            for c in ca:
                if c in opts:
                    migrated.append(new_labels[opts.index(c)])
                elif c in new_labels:
                    migrated.append(c)
            q["correct_answer"] = migrated
    else:
        if ca in opts:
            q["correct_answer"] = new_labels[opts.index(ca)]
        elif ca not in new_labels and new_labels:
            q["correct_answer"] = new_labels[0]


def render_choice_options_editor(q, key_prefix, qid, tslug):
    """択一選択／複数選択用の選択肢エディタ（ラベル + 説明テキスト形式）。"""
    safe_qid = str(qid).replace("-", "_")
    if not is_new_option_format(q):
        st.info(
            "この設問は旧形式（表示文字列のみ）で保存されています。"
            "ラベル（A,B,1,2...）と説明文を分けて編集できる新形式に変換できます。"
        )
        col_a, col_b = st.columns([2, 2])
        with col_a:
            init_kind = st.selectbox(
                "変換時のラベル方式（自動採番）",
                LABEL_KIND_OPTIONS,
                index=0,
                key=f"{key_prefix}_optmig_kind_{safe_qid}_{tslug}",
            )
        with col_b:
            st.markdown("<div style='padding-top:28px;'></div>", unsafe_allow_html=True)
            if st.button(
                "🛠 ラベル/説明分離形式に変換する",
                key=f"{key_prefix}_optmig_btn_{safe_qid}_{tslug}",
            ):
                _migrate_legacy_options_to_labeled(q, init_kind)
                st.rerun()
        # 旧形式の従来UIにフォールバック
        opt_str = ", ".join(q.get("options", []))
        edited_opts = st.text_input(
            "選択肢（カンマ ',' 区切り・旧形式）",
            value=opt_str,
            key=f"{key_prefix}_qopts_{safe_qid}_{tslug}",
        )
        q["options"] = [o.strip() for o in edited_opts.split(",") if o.strip()]
        if q["type"] == "択一選択" and q["options"]:
            cur_ans = q.get("correct_answer", q["options"][0])
            idx = q["options"].index(cur_ans) if cur_ans in q["options"] else 0
            q["correct_answer"] = st.selectbox(
                "正解となる選択肢", q["options"], index=idx,
                key=f"{key_prefix}_qans_{safe_qid}_{tslug}",
            )
        elif q["type"] == "複数選択" and q["options"]:
            st.write("正解となる選択肢（複数選んでください）")
            correct_list = q.get("correct_answer", [])
            if not isinstance(correct_list, list):
                correct_list = []
            q["correct_answer"] = [
                opt for j, opt in enumerate(q["options"])
                if st.checkbox(opt, value=opt in correct_list, key=f"{key_prefix}_qcb_{safe_qid}_{j}")
            ]
        return

    # 新形式の編集UI
    ensure_option_texts(q)

    head = st.columns([1.4, 1.2, 1.4])
    with head[0]:
        kind = st.selectbox(
            "ラベル方式",
            LABEL_KIND_OPTIONS,
            index=LABEL_KIND_OPTIONS.index(options_total_label_kind_default(q))
            if options_total_label_kind_default(q) in LABEL_KIND_OPTIONS
            else 0,
            key=f"{key_prefix}_optkind_{safe_qid}_{tslug}",
            help="A-Z / 1-99 など。設定後「ラベル再採番」で一括反映できます。",
        )
    with head[1]:
        st.markdown("<div style='padding-top:28px;'></div>", unsafe_allow_html=True)
        if st.button(
            "🔁 ラベル再採番",
            key=f"{key_prefix}_optrenum_{safe_qid}_{tslug}",
            help="現在の選択肢個数に応じて、選択したラベル方式で振り直します。",
        ):
            n = len(q["options"])
            new_labels = suggest_option_labels(kind, n)
            # 正解と option_explanations のキーも再採番後ラベルに追従
            old_labels = list(q["options"])
            mapping = {old_labels[j]: new_labels[j] for j in range(n)}
            ca = q.get("correct_answer")
            if q["type"] == "複数選択" and isinstance(ca, list):
                q["correct_answer"] = [mapping.get(c, c) for c in ca]
            elif isinstance(ca, str):
                q["correct_answer"] = mapping.get(ca, ca)
            old_exp = q.get("option_explanations") or {}
            q["option_explanations"] = {
                mapping.get(k, k): v for k, v in old_exp.items()
            }
            q["options"] = new_labels
            # 該当ウィジェットの session_state をクリア
            for k in list(st.session_state.keys()):
                if isinstance(k, str) and k.startswith(f"{key_prefix}_optrow_{safe_qid}_{tslug}_"):
                    del st.session_state[k]
            st.rerun()
    with head[2]:
        st.markdown("<div style='padding-top:28px;'></div>", unsafe_allow_html=True)
        if st.button(
            "➕ 選択肢を追加",
            key=f"{key_prefix}_optadd_{safe_qid}_{tslug}",
        ):
            n = len(q["options"])
            suggested = suggest_option_labels(kind, n + 1)
            new_lab = suggested[n] if n < len(suggested) else f"X{n+1}"
            q["options"].append(new_lab)
            q["option_texts"].append("")
            st.rerun()

    # 削除予約のための1パス処理
    remove_idx = None
    st.caption("各選択肢のラベル（A, B, 1, 2 など）と説明文を入力します。表示は「ラベル. 説明文」となります。")
    for j in range(len(q["options"])):
        cols = st.columns([1.2, 4.5, 0.9])
        with cols[0]:
            new_label = st.text_input(
                f"ラベル {j+1}",
                value=q["options"][j],
                key=f"{key_prefix}_optrow_{safe_qid}_{tslug}_lab_{j}",
            )
        with cols[1]:
            new_text = st.text_input(
                f"説明 {j+1}",
                value=q["option_texts"][j],
                key=f"{key_prefix}_optrow_{safe_qid}_{tslug}_txt_{j}",
                placeholder="例: 50%",
            )
        with cols[2]:
            st.markdown("<div style='padding-top:28px;'></div>", unsafe_allow_html=True)
            if st.button(
                "🗑",
                key=f"{key_prefix}_optrow_{safe_qid}_{tslug}_del_{j}",
                help="この選択肢を削除",
            ):
                remove_idx = j
        # ラベル変更時、正解および option_explanations のキーを追随させる
        old_label = q["options"][j]
        if new_label != old_label:
            mapping = {old_label: new_label}
            ca = q.get("correct_answer")
            if q["type"] == "複数選択" and isinstance(ca, list):
                q["correct_answer"] = [mapping.get(c, c) for c in ca]
            elif isinstance(ca, str):
                q["correct_answer"] = mapping.get(ca, ca)
            old_exp = q.get("option_explanations") or {}
            if old_label in old_exp:
                old_exp[new_label] = old_exp.pop(old_label)
                q["option_explanations"] = old_exp
            q["options"][j] = new_label
        q["option_texts"][j] = new_text
        st.markdown(
            f"<div style='color:#6B7280; font-size:12px; margin-top:-8px; margin-bottom:8px;'>"
            f"表示プレビュー: <strong>{format_option_display(q['options'][j], q['option_texts'][j])}</strong>"
            f"</div>",
            unsafe_allow_html=True,
        )

    if remove_idx is not None and 0 <= remove_idx < len(q["options"]):
        removed_label = q["options"].pop(remove_idx)
        q["option_texts"].pop(remove_idx)
        ca = q.get("correct_answer")
        if q["type"] == "複数選択" and isinstance(ca, list):
            q["correct_answer"] = [c for c in ca if c != removed_label]
        elif isinstance(ca, str) and ca == removed_label:
            q["correct_answer"] = q["options"][0] if q["options"] else ""
        if isinstance(q.get("option_explanations"), dict):
            q["option_explanations"].pop(removed_label, None)
        # 削除に伴うウィジェット状態のクリア（インデックスシフトによる不整合を防止）
        for k in list(st.session_state.keys()):
            if isinstance(k, str) and k.startswith(
                f"{key_prefix}_optrow_{safe_qid}_{tslug}_"
            ):
                del st.session_state[k]
        st.rerun()

    if not q["options"]:
        st.warning("選択肢が0件です。少なくとも1つの選択肢を追加してください。")
        return

    if q["type"] == "択一選択":
        cur_ans = q.get("correct_answer")
        if cur_ans not in q["options"]:
            cur_ans = q["options"][0]
        display_map = {
            opt: format_option_display(opt, txt)
            for opt, txt in zip(q["options"], q["option_texts"])
        }
        q["correct_answer"] = st.selectbox(
            "正解となる選択肢（ラベル）",
            q["options"],
            index=q["options"].index(cur_ans),
            format_func=lambda v, m=display_map: m.get(v, v),
            key=f"{key_prefix}_qans_{safe_qid}_{tslug}",
        )
    else:
        st.write("正解となる選択肢（複数選んでください）")
        correct_list = q.get("correct_answer")
        if not isinstance(correct_list, list):
            correct_list = []
        new_correct = []
        for j, opt in enumerate(q["options"]):
            disp = format_option_display(opt, q["option_texts"][j])
            if st.checkbox(
                disp,
                value=opt in correct_list,
                key=f"{key_prefix}_qcb_{safe_qid}_{tslug}_{j}",
            ):
                new_correct.append(opt)
        q["correct_answer"] = new_correct


def render_questions_builder(questions, key_prefix):
    """設問ビルダーUI。questions リストをその場で更新する。
    並び替え（↑/↓/先頭/末尾/任意位置）と任意設問の削除に対応する。"""
    total = len(questions)
    for i, q in enumerate(questions):
        qid = q.get("id") or f"idx{i}"
        tslug = _qtype_slug(q.get("type", "択一選択"))
        with st.container():
            st.markdown("<div class='glass-card' style='padding: 15px;'>", unsafe_allow_html=True)

            # --- 並び替え操作行 ---
            order_cols = st.columns([2.2, 0.6, 0.6, 0.7, 0.7, 1.2, 0.8, 0.8])
            order_cols[0].markdown(
                f"**設問 {i+1}** <span style='color:#9CA3AF;'>/ {total}</span>",
                unsafe_allow_html=True,
            )
            if order_cols[1].button(
                "↑", key=f"{key_prefix}_qup_{qid}", help="一つ上へ", disabled=(i == 0)
            ):
                _move_question(questions, i, i - 1)
                st.rerun()
            if order_cols[2].button(
                "↓", key=f"{key_prefix}_qdn_{qid}", help="一つ下へ", disabled=(i >= total - 1)
            ):
                _move_question(questions, i, i + 1)
                st.rerun()
            if order_cols[3].button(
                "⤒ 先頭",
                key=f"{key_prefix}_qtop_{qid}",
                help="この設問を先頭へ移動",
                disabled=(i == 0),
            ):
                _move_question(questions, i, 0)
                st.rerun()
            if order_cols[4].button(
                "⤓ 末尾",
                key=f"{key_prefix}_qbot_{qid}",
                help="この設問を末尾へ移動",
                disabled=(i >= total - 1),
            ):
                _move_question(questions, i, total - 1)
                st.rerun()
            with order_cols[5]:
                new_pos = st.number_input(
                    "移動先（1〜全件）",
                    min_value=1,
                    max_value=max(total, 1),
                    value=i + 1,
                    step=1,
                    key=f"{key_prefix}_qpos_{qid}",
                    label_visibility="collapsed",
                )
            if order_cols[6].button(
                "移動",
                key=f"{key_prefix}_qjump_{qid}",
                help="左の番号の位置へ移動",
                disabled=(total <= 1 or int(new_pos) - 1 == i),
            ):
                _move_question(questions, i, int(new_pos) - 1)
                st.rerun()
            if order_cols[7].button(
                "🗑 削除",
                key=f"{key_prefix}_qdel_{qid}",
                help="この設問を削除",
            ):
                questions.pop(i)
                # 並び替え用 number_input の session_state を消しておく
                for k in (f"{key_prefix}_qpos_{qid}",):
                    st.session_state.pop(k, None)
                st.rerun()

            # --- 設問編集フォーム ---
            col_type, col_cat, col_pts = st.columns([2, 2, 1.5])
            with col_type:
                type_opts = ["択一選択", "複数選択", "○×式", "テキスト（記述式）", "テキストエリア（長文記述）"]
                cur_type = q.get("type", "択一選択")
                type_idx = type_opts.index(cur_type) if cur_type in type_opts else 0
                q["type"] = st.selectbox(
                    "設問形式", type_opts, index=type_idx, key=f"{key_prefix}_qtype_{qid}"
                )
            with col_cat:
                q["category"] = st.text_input(
                    "カテゴリ（分野）", value=q.get("category", "未分類"), key=f"{key_prefix}_qcat_{qid}"
                )
            with col_pts:
                q["points"] = st.number_input(
                    "配点", min_value=0.0, max_value=100.0,
                    value=float(q.get("points", 20.0)), step=5.0, key=f"{key_prefix}_qpts_{qid}"
                )
            q["question"] = st.text_area(
                "問題文", value=q.get("question", ""), key=f"{key_prefix}_qtext_{qid}"
            )
            needs_option_explanations = False
            if q["type"] in ["択一選択", "複数選択"]:
                render_choice_options_editor(q, key_prefix, qid, tslug)
                needs_option_explanations = bool(q.get("options"))
            elif q["type"] == "○×式":
                q["options"] = ["○ (正しい)", "× (誤り)"]
                cur = q.get("correct_answer", "○ (正しい)")
                idx = 0 if cur == "○ (正しい)" else 1
                q["correct_answer"] = st.selectbox(
                    "正解", q["options"], index=idx, key=f"{key_prefix}_qans_{qid}_{tslug}"
                )
                needs_option_explanations = True
            else:
                q["correct_answer"] = st.text_area(
                    "模範解答・判定用キーワード（AI採点や模範解答提示用）",
                    value=q.get("correct_answer", ""),
                    key=f"{key_prefix}_qans_{qid}_{tslug}",
                )
                if q["type"] in TEXT_QUESTION_TYPES:
                    q["required"] = st.checkbox(
                        "この設問は必須（受験者が未記入のまま提出できないようにする）",
                        value=bool(q.get("required", False)),
                        key=f"{key_prefix}_qreq_{qid}_{tslug}",
                    )
            if needs_option_explanations:
                render_option_explanation_inputs(q, key_prefix, qid, tslug)
            q["explanation"] = st.text_area(
                "解説文（採点結果で受験者に送られます）",
                value=q.get("explanation", ""), key=f"{key_prefix}_qexp_{qid}"
            )
            st.markdown("</div>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

    st.markdown("**設問を追加**")
    add_col_type, add_col_btn = st.columns([2, 2])
    add_type = add_col_type.selectbox(
        "追加する設問形式",
        ["択一選択", "複数選択", "○×式", "テキスト（記述式）", "テキストエリア（長文記述）"],
        key=f"{key_prefix}_add_type",
    )
    if add_col_btn.button("この形式で設問を追加", key=f"{key_prefix}_add_btn"):
        new_q = {
            "id": f"q_{uuid.uuid4().hex[:4]}",
            "type": add_type,
            "question": "新しい設問文を入力してください",
            "category": "一般",
            "points": 20.0,
            "explanation": "解説を記述してください。",
            "option_explanations": {},
        }
        if add_type in ["択一選択", "複数選択"]:
            new_q["options"] = ["A", "B"]
            new_q["option_texts"] = ["選択肢Aの説明", "選択肢Bの説明"]
            new_q["correct_answer"] = "A" if add_type == "択一選択" else ["A"]
        elif add_type == "○×式":
            new_q["options"] = ["○ (正しい)", "× (誤り)"]
            new_q["correct_answer"] = "○ (正しい)"
        else:
            new_q["options"] = []
            new_q["correct_answer"] = ""
            new_q["required"] = False
        questions.append(new_q)
        st.rerun()


def render_exam_editor_permissions(exam_id):
    """試験オーナーのみ：他ユーザーへの編集・分析参照権限付与。"""
    if not user_owns_exam(st.session_state.user_id, exam_id):
        return
    st.markdown("**編集・結果分析権限の付与（他の問題作成者）**")
    st.caption("付与されたユーザーは、当該試験の設問編集と受験結果・分析の閲覧ができます。")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT ee.user_id, u.username, u.company_name
        FROM exam_editors ee
        JOIN users u ON ee.user_id = u.id
        WHERE ee.exam_id = ?
        ORDER BY u.username
    """, (exam_id,))
    current_editors = c.fetchall()
    c.execute(
        "SELECT id, username, company_name FROM users WHERE id != ? AND role IN ('creator', 'admin') ORDER BY username",
        (st.session_state.user_id,),
    )
    grant_candidates = c.fetchall()
    conn.close()

    if current_editors:
        st.caption("現在、編集・分析が可能なユーザー")
        for uid, uname, company in current_editors:
            col_a, col_b = st.columns([4, 1])
            col_a.write(f"{uname} ({company})")
            if col_b.button("解除", key=f"revoke_{exam_id}_{uid}"):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute(
                    "DELETE FROM exam_editors WHERE exam_id = ? AND user_id = ?",
                    (exam_id, uid),
                )
                conn.commit()
                conn.close()
                st.toast(f"{uname} の権限を解除しました。")
                st.rerun()

    if grant_candidates:
        options = {f"{u[1]} ({u[2]})": u[0] for u in grant_candidates}
        current_ids = {e[0] for e in current_editors}
        available = {k: v for k, v in options.items() if v not in current_ids}
        if available:
            selected_label = st.selectbox(
                "編集・分析権限を付与するユーザー",
                list(available.keys()),
                key=f"grant_sel_{exam_id}",
            )
            if st.button("編集・分析権限を付与", key=f"grant_btn_{exam_id}"):
                target_uid = available[selected_label]
                conn = get_db_connection()
                c = conn.cursor()
                try:
                    c.execute(
                        "INSERT INTO exam_editors (exam_id, user_id, granted_by, created_at) VALUES (?, ?, ?, ?)",
                        (
                            exam_id,
                            target_uid,
                            st.session_state.user_id,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                    conn.commit()
                    st.success("編集・結果分析の参照権限を付与しました。")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.warning("このユーザーには既に権限が付与されています。")
                finally:
                    conn.close()
        else:
            st.caption("付与可能なユーザーはいません。")


def render_exam_manage_expander(exam_row, show_owner=False):
    """試験1件分の管理UI（URL・編集・複製・公開状態切替・削除・権限）。"""
    ex_id, title, desc, status, limit_t, created, created_by, owner_name = exam_row
    owner_suffix = f" (作成者: {owner_name})" if show_owner else ""
    status_norm = normalize_exam_status(status)
    published = status_norm == EXAM_STATUS_PUBLISHED
    status_badge_color = "#10B981" if published else "#9CA3AF"
    status_label = f"🟢 {EXAM_STATUS_PUBLISHED}" if published else f"⛔ {EXAM_STATUS_UNPUBLISHED}"
    header = f"【{ex_id}】{title}{owner_suffix} ／ {status_label}"
    with st.expander(header):
        st.write(f"**説明:** {desc}")
        st.markdown(
            f"**制限時間:** {limit_t}分 ｜ **ステータス:** "
            f"<span style='display:inline-block; padding:2px 10px; border-radius:12px;"
            f" background:{status_badge_color}; color:white; font-weight:bold;'>{status_norm}</span>"
            f" ｜ **作成日時:** {created}",
            unsafe_allow_html=True,
        )
        if not published:
            st.info("この試験は現在「非公開」です。受験URLにアクセスしても受験画面は表示されません。")
        render_exam_url_box(ex_id)
        can_edit = user_can_edit_exam(st.session_state.user_id, st.session_state.role, ex_id)
        can_delete = user_can_delete_exam(
            st.session_state.user_id, st.session_state.role, ex_id
        )

        col_edit, col_copy, col_status = st.columns(3)
        with col_edit:
            if can_edit and st.button("編集", key=f"edit_btn_{ex_id}", type="primary"):
                load_exam_into_builder(ex_id, as_copy=False)
                st.rerun()
        with col_copy:
            if can_edit and st.button("コピーして新規作成", key=f"copy_btn_{ex_id}"):
                load_exam_into_builder(ex_id, as_copy=True)
                st.toast("試験内容をコピーしました。下の「新規作成」欄で試験コードを確認のうえ登録してください。")
                st.rerun()
        with col_status:
            if can_edit:
                if published:
                    if st.button(
                        "⛔ 非公開にする",
                        key=f"unpublish_btn_{ex_id}",
                        help="受験者が受験URLからアクセスしても受験できなくなります。",
                    ):
                        set_exam_status(ex_id, EXAM_STATUS_UNPUBLISHED)
                        st.toast(f"【{ex_id}】を非公開にしました。")
                        st.rerun()
                else:
                    if st.button(
                        "🟢 公開する",
                        key=f"publish_btn_{ex_id}",
                        type="primary",
                        help="受験URLからの受験を許可します。",
                    ):
                        set_exam_status(ex_id, EXAM_STATUS_PUBLISHED)
                        st.toast(f"【{ex_id}】を公開しました。")
                        st.rerun()
            else:
                st.caption("公開状態の変更権限がありません。")

        if user_owns_exam(st.session_state.user_id, ex_id):
            render_exam_editor_permissions(ex_id)

        if can_delete:
            with st.expander("🗑 この試験を削除する", expanded=False):
                sub_count_for_del = count_submissions_for_exam(ex_id)
                st.warning(
                    "削除すると、本試験そのものに加え、紐づくすべての"
                    "**受験提出データ**および**編集権限の付与情報**が完全に失われ、復元できません。"
                )
                st.write(
                    f"対象: **【{ex_id}】{title}** ／ 関連する受験提出データ: "
                    f"**{sub_count_for_del} 件**"
                )
                if sub_count_for_del > 0:
                    st.error(
                        f"⚠️ この試験には **{sub_count_for_del} 件** の受験提出データがあります。"
                        " 削除すると、これらの受験結果も同時に失われます。"
                        " 結果を残したい場合は、削除ではなく「非公開」をご利用ください。"
                    )
                confirm_key = f"confirm_delete_exam_{ex_id}"
                confirmed = st.checkbox(
                    f"上記の試験（ID={ex_id}）および関連データをすべて削除することを承諾します",
                    key=confirm_key,
                )
                del_btn = st.button(
                    "🚨 この試験を完全に削除する",
                    key=f"delete_exam_btn_{ex_id}",
                    type="primary",
                    disabled=not confirmed,
                )
                if del_btn:
                    if not confirmed:
                        st.error("確認チェックを入れてください。")
                    else:
                        deleted_title, deleted_subs = delete_exam(ex_id)
                        if deleted_title is not None:
                            if st.session_state.get("editing_exam_id") == ex_id:
                                clear_edit_widget_session_state(edit_id=ex_id)
                                st.session_state.exam_form_mode = "create"
                                st.session_state.editing_exam_id = None
                                st.session_state.edit_questions_builder = []
                            st.session_state.pop(confirm_key, None)
                            st.success(
                                f"試験「【{ex_id}】{deleted_title}」を削除しました。"
                                f"（関連受験提出 {deleted_subs} 件も削除）"
                            )
                            st.toast("試験を削除しました。")
                            st.rerun()
                        else:
                            st.error("削除に失敗しました。対象の試験が既に削除されている可能性があります。")


# --- 3. メール送信エンジン ---
def is_smtp_configured():
    """SMTPが実運用可能な値で設定されているか。"""
    keys = ("SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM")
    if not all(os.getenv(k, "").strip() for k in keys):
        return False
    user = os.getenv("SMTP_USER", "").lower()
    pwd = os.getenv("SMTP_PASSWORD", "").lower()
    placeholders = ("your_email", "your_app_password", "example.com", "xxx", "password")
    if any(p in user or p in pwd for p in placeholders):
        return False
    return True


def send_result_email(to_email, examinee_name, exam_title, score, total_points, results_list, schema, grading_config=None):
    """採点結果メールを送信する。
    schema は表示用（提出時スナップショットを優先して呼び出し側で組み立てたもの）。
    results_list と schema は question_id で結合する（インデックス突合は行わない）。"""
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_from = os.getenv("EMAIL_FROM")
    
    if not is_smtp_configured():
        print("SMTP settings are missing or placeholder. Email notifications skipped.")
        return False
        
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"【試験結果】{exam_title} の採点レポート"
        msg['From'] = email_from
        msg['To'] = to_email
        
        # HTMLメール本文の構築
        html_content = f"""
        <html>
        <body style="font-family: 'Noto Sans JP', sans-serif; color: #333; line-height: 1.6;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                <h2 style="color: #4F46E5; border-bottom: 2px solid #4F46E5; padding-bottom: 10px;">試験結果レポート</h2>
                <p><strong>{examinee_name} 様</strong></p>
                <p>この度は <strong>{exam_title}</strong> を受験いただきありがとうございました。<br>採点結果をお送りいたします。</p>
                
                <div style="background: #F3F4F6; padding: 15px; border-radius: 6px; margin: 20px 0; text-align: center;">
                    <span style="font-size: 18px;">総合得点</span><br>
                    <span style="font-size: 32px; font-weight: bold; color: #4F46E5;">{score}</span> / {total_points} 点
                </div>
        """
        grade_label = evaluate_score_grade(score, total_points, grading_config)
        if grade_label:
            html_content += f"""
                <div style="background: #EEF2FF; padding: 12px; border-radius: 6px; margin: 0 0 20px 0; text-align: center;">
                    <span style="font-size: 16px; color: #4338CA;">総合評価</span><br>
                    <span style="font-size: 26px; font-weight: bold; color: #4F46E5;">{grade_label}</span>
                </div>
            """
        html_content += """
                <h3 style="color: #1F2937; border-left: 4px solid #8B5CF6; padding-left: 8px;">問題別の採点結果詳細</h3>
        """

        # question_id で結合（インデックス突合はしない）。schema を主とし、
        # 紐づく result が無い設問は採点不能としてスキップ／注意書きを表示する。
        paired = pair_results_with_schema(schema, results_list)
        for i, q_info in enumerate(schema):
            res = paired[i]
            if not res:
                html_content += f"""
                <div style="margin-bottom: 20px; padding: 15px; border: 1px solid #E5E7EB; border-radius: 6px;">
                    <p style="margin: 0 0 10px 0; font-weight: bold;">問 {i+1}: {q_info.get("question", "")}</p>
                    <p style="margin: 5px 0; color: #6B7280;">この設問の採点データは保存されていません。</p>
                </div>
                """
                continue
            q_text = q_info["question"]
            points = q_info["points"]
            earned = res["earned"]
            is_correct = res["is_correct"]
            feedback = res.get("feedback", "")
            ans_str = format_answer_display_for_q(q_info, res["student_answer"])
            correct_ans = format_answer_display_for_q(q_info, q_info.get("correct_answer"))
            explanation = q_info.get("explanation", "解説はありません。")
            
            status_text = "🟢 正解" if is_correct else "🔴 不正解（または部分点）"
            status_color = "#10B981" if is_correct else "#F43F5E"
            
            html_content += f"""
            <div style="margin-bottom: 20px; padding: 15px; border: 1px solid #E5E7EB; border-radius: 6px;">
                <p style="margin: 0 0 10px 0; font-weight: bold;">問 {i+1}: {q_text}</p>
                <p style="margin: 5px 0;"><strong>配点:</strong> {points}点 | <strong>獲得点:</strong> <span style="font-weight:bold; color:{status_color};">{earned}点</span> ({status_text})</p>
                <p style="margin: 5px 0; background: #F9FAFB; padding: 8px; border-radius: 4px;"><strong>あなたの回答:</strong> {ans_str}</p>
                <p style="margin: 5px 0;"><strong>模範解答:</strong> {correct_ans}</p>
            """
            
            if feedback:
                html_content += f"""
                <p style="margin: 5px 0; color: #4B5563; font-style: italic;"><strong>AI個別講評:</strong> {feedback}</p>
                """
            html_content += build_option_explanations_html(q_info, res)
            if (q_info.get("explanation") or "").strip():
                html_content += f"""
                <p style="margin: 10px 0 0 0; font-size: 13px; color: #6B7280; background: #FFFBEB; padding: 10px; border-left: 3px solid #F59E0B;"><strong>解説:</strong> {explanation}</p>
                """
            html_content += """
            </div>
            """
            
        html_content += """
                <hr style="border: 0; border-top: 1px solid #EEE; margin: 30px 0;">
                <p style="font-size: 12px; color: #9CA3AF; text-align: center;">※本メールはシステムによる自動送信です。</p>
            </div>
        </body>
        </html>
        """
        
        part = MIMEText(html_content, 'html', 'utf-8')
        msg.attach(part)
        
        # SMTPサーバーへの接続と送信
        server = smtplib.SMTP(smtp_server, int(smtp_port))
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(email_from, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False


def resend_result_email_for_submission(submission_id, exam_title, grading_config=None):
    """管理画面から採点結果メールを再送信し、email_sent を更新する。
    送信に使うスキーマは「提出時スナップショット」を最優先で復元する。"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "SELECT exam_id, examinee_name, examinee_email, score, total_points, results, schema_snapshot FROM submissions WHERE id = ?",
        (submission_id,),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "受験記録が見つかりません。"
    sub_exam_id, name, email, score, total_points, results_str, snapshot_str = row
    results_list = json.loads(results_str)
    conn.close()
    if grading_config is None:
        grading_config = fetch_grading_config(sub_exam_id)

    if not is_smtp_configured():
        return False, "SMTPが未設定です。サーバーの .env に SMTP_SERVER / SMTP_USER 等を設定してください。"

    # 表示用スキーマを決定（スナップショット最優先）
    current_exam = fetch_exam_record(sub_exam_id)
    current_schema = []
    if current_exam:
        try:
            current_schema = json.loads(current_exam[5])
        except (json.JSONDecodeError, TypeError):
            current_schema = []
    display_schema, _ = load_submission_schema(
        {"schema_snapshot": snapshot_str, "results": results_str},
        current_schema,
    )

    ok = send_result_email(
        email, name, exam_title, score, total_points, results_list, display_schema, grading_config
    )
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE submissions SET email_sent = ? WHERE id = ?",
        (1 if ok else 0, submission_id),
    )
    conn.commit()
    conn.close()
    if ok:
        return True, f"{name} 様（{email}）へ採点結果メールを送信しました。"
    return False, f"{email} への送信に失敗しました。SMTP設定・宛先・迷惑メール設定を確認してください。"


# --- 4. AI採点エンジン ---
def ai_grade_question(question_text, points, model_answer, student_answer):
    if not GEMINI_API_KEY or not HAS_GEMINI or not genai:
        # Gemini APIキーがない場合の簡易な部分一致判定 (フォールバック)
        if str(model_answer).strip().lower() == str(student_answer).strip().lower():
            return {"score": float(points), "is_correct": True, "feedback": "完全一致により満点です。(API未設定)"}
        elif str(model_answer).strip().lower() in str(student_answer).strip().lower() or any(k in str(student_answer) for k in str(model_answer).split()):
            return {"score": round(float(points) * 0.5, 1), "is_correct": False, "feedback": "キーワードが含まれているため、部分点を与えます。(API未設定)"}
        else:
            return {"score": 0.0, "is_correct": False, "feedback": "不正解です。(API未設定)"}

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = f"""
        試験の記述式問題の採点を行ってください。

        【問題文】
        {question_text}

        【配点】
        {points} 点

        【模範解答】
        {model_answer}

        【受験者の回答】
        {student_answer}

        【採点基準】
        受験者の回答が模範解答の意味や核心的な要素をどの程度満たしているかを評価してください。
        一字一句が一致している必要はありません。核心的なキーワードや概念が含まれていれば部分点または満点を与えてください。
        文法や語句のゆれは許容し、意味が通っているかを重視して甘めに評価してください。

        【出力フォーマット】
        以下のJSONフォーマットでのみ出力してください。他の説明テキストは一切含めないでください。JSONブロックのバックチックスマークも含めず純粋なJSON文字列としてください。
        {{
          "score": (0から{points}の範囲の数値。小数点第一位まで認める),
          "is_correct": (得点が配点の60%以上の場合はtrue、それ以外はfalse),
          "feedback": "受験者への丁寧で具体的なフィードバックコメント（日本語。どこが良くて、何が足りないかを具体的に解説）"
        }}
        """
        response = model.generate_content(prompt)
        # JSON部分の抽出とパース
        clean_text = response.text.strip()
        if clean_text.startswith("```"):
            clean_text = clean_text.split("```json")[-1].split("```")[0].strip()
        
        result = json.loads(clean_text)
        return {
            "score": float(result.get("score", 0)),
            "is_correct": bool(result.get("is_correct", False)),
            "feedback": str(result.get("feedback", "採点完了"))
        }
    except Exception as e:
        print(f"AI grading failed: {e}")
        # 失敗時のフォールバック
        return {"score": 0.0, "is_correct": False, "feedback": f"AI採点中にエラーが発生したため、デフォルトで0点とします。 (エラー: {e})"}

# --- 5. AI全体分析エンジン ---
def ai_analyze_submissions(submissions_df, exam_title, schema):
    if not GEMINI_API_KEY or not HAS_GEMINI or not genai:
        return "Gemini APIキーが設定されていないか、必要なライブラリ(google-generativeai)がインストールされていないため、AIによる総評は生成できませんでした。データベースから手動で集計してください。"
        
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # 受験結果を要約してプロンプトに渡す
        total_examinees = len(submissions_df)
        avg_score = submissions_df['score'].mean()
        max_score = submissions_df['score'].max()
        min_score = submissions_df['score'].min()
        
        # 設問ごとの正答率（question_id で結合し、提出時にその設問が含まれていなかった
        # 受験者は分母にも含めない＝有効回答者数ベースで算出）
        all_results = []
        for idx, row in submissions_df.iterrows():
            res_list = json.loads(row['results'])
            all_results.append({r.get("question_id"): r for r in res_list if isinstance(r, dict)})

        q_stats = []
        for q in schema:
            qid = q.get("id")
            answered = [r for r in all_results if qid in r]
            corrects = sum(1 for r in answered if r[qid].get('is_correct'))
            denom = len(answered)
            q_stats.append({
                "question": q["question"],
                "category": q["category"],
                "points": q["points"],
                "correct_rate": (corrects / denom) * 100 if denom > 0 else 0,
                "answered_count": denom,
            })
            
        data_summary = {
            "exam_title": exam_title,
            "total_examinees": total_examinees,
            "average_score": avg_score,
            "max_score": max_score,
            "min_score": min_score,
            "question_statistics": q_stats
        }
        
        prompt = f"""
        以下の試験結果データ（JSON）を元に、受験者全体の「解答傾向の分析と評価」を行ってください。
        
        【分析内容】
        1. 全体の得点状況に関する講評（平均点、分散などから見えること）
        2. 正答率が著しく低い問題、または高い問題に対する分析（どの分野・スキルがつまずきやすい弱点か）
        3. 指導者や教育計画立案者向けのアドバイス（今後どのようなフォローアップ授業や学習教材が必要か）
        
        日本語で丁寧にかつ論理的に、400文字程度で「総評」としてまとめてください。
        
        試験データ:
        {json.dumps(data_summary, ensure_ascii=False, indent=2)}
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI全体分析中にエラーが発生しました: {e}"

# --- 6. PDFレポート生成 ---
def generate_pdf_report(exam_title, total_users, avg_score, max_score, min_score, chart_path, ai_eval_text, q_stats):
    if not HAS_FPDF or not FPDF:
        print("FPDF is not installed. PDF report generation skipped.")
        return None
    try:
        pdf = FPDF()
        
        if os.path.exists(FONT_PATH):
            pdf.add_font('IPAexGothic', '', FONT_PATH, uni=True)
            pdf.set_font('IPAexGothic', '', 12)
        else:
            pdf.set_font('Helvetica', '', 12)
            
        pdf.add_page()
        
        # ヘッダータイトル
        pdf.set_font('IPAexGothic' if os.path.exists(FONT_PATH) else 'Helvetica', '', 18)
        pdf.cell(0, 15, txt=f"試験解答傾向分析レポート", ln=True, align='C')
        pdf.set_font('IPAexGothic' if os.path.exists(FONT_PATH) else 'Helvetica', '', 12)
        pdf.cell(0, 10, txt=f"対象試験: {exam_title}", ln=True, align='C')
        pdf.ln(5)
        
        # 基本統計テーブル
        pdf.cell(0, 8, txt=f"■ 基本統計情報", ln=True)
        pdf.ln(2)
        pdf.cell(45, 8, txt=f"総受験者数: {total_users}名", border=1)
        pdf.cell(45, 8, txt=f"平均得点: {avg_score:.1f}点", border=1)
        pdf.cell(45, 8, txt=f"最高得点: {max_score:.1f}点", border=1)
        pdf.cell(45, 8, txt=f"最低得点: {min_score:.1f}点", border=1)
        pdf.ln(12)
        
        # グラフ描画
        if chart_path and os.path.exists(chart_path):
            pdf.cell(0, 8, txt=f"■ 設問別正答率統計", ln=True)
            pdf.image(chart_path, x=15, y=pdf.get_y() + 2, w=180)
            # グラフ画像の高さに合わせて改ページまたは改行
            pdf.ln(85)
            
        # 設問ごとの正答率リスト
        if pdf.get_y() > 220:
            pdf.add_page()
            
        pdf.cell(0, 8, txt=f"■ 設問別詳細データ", ln=True)
        pdf.ln(2)
        pdf.set_font('IPAexGothic' if os.path.exists(FONT_PATH) else 'Helvetica', '', 10)
        
        # カラムヘッダー
        pdf.cell(15, 7, txt="番号", border=1)
        pdf.cell(100, 7, txt="問題内容（抜粋）", border=1)
        pdf.cell(40, 7, txt="カテゴリ", border=1)
        pdf.cell(30, 7, txt="正答率", border=1, ln=True)
        
        for i, q in enumerate(q_stats):
            q_text = q['question'][:30] + "..." if len(q['question']) > 30 else q['question']
            pdf.cell(15, 7, txt=f"問 {i+1}", border=1)
            pdf.cell(100, 7, txt=q_text, border=1)
            pdf.cell(40, 7, txt=q['category'], border=1)
            pdf.cell(30, 7, txt=f"{q['correct_rate']:.1f}%", border=1, ln=True)
            
        pdf.ln(10)
        
        # AI総評
        if pdf.get_y() > 180:
            pdf.add_page()
            
        pdf.set_font('IPAexGothic' if os.path.exists(FONT_PATH) else 'Helvetica', '', 12)
        pdf.cell(0, 8, txt=f"■ AIによる解答傾向の総評・指導者向けアドバイス", ln=True)
        pdf.ln(2)
        pdf.set_font('IPAexGothic' if os.path.exists(FONT_PATH) else 'Helvetica', '', 10)
        pdf.multi_cell(0, 6, txt=ai_eval_text, border=1)
        
        return bytes(pdf.output())
    except Exception as e:
        print(f"PDF creation failed: {e}")
        return None

# --- 7. スタイルシート (プレミアムネオン・Glassmorphismテーマ) ---
def apply_custom_styles():
    st.markdown("""
    <style>
    /* ====== ライトテーマ：白〜淡色背景、濃色文字 ====== */

    /* 全体背景（白〜ごく淡い青系のグラデーション） */
    .stApp {
        background: linear-gradient(135deg, #FFFFFF 0%, #F5F7FB 60%, #EEF2FF 100%);
        color: #111827 !important;
    }

    /* 見出し・段落・ラベルを濃色で強制 */
    .stApp p, .stApp span, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6, .stApp li {
        color: #111827 !important;
    }
    .stApp h1, .stApp h2, .stApp h3 {
        color: #1F2937 !important;
    }
    /* リンク文字色 */
    .stApp a {
        color: #1D4ED8 !important;
    }

    /* CSS 変数のオーバーライド（サイドバー等の Streamlit 既定色対策） */
    :root {
        --background-color: #FFFFFF !important;
        --secondary-background-color: #F9FAFB !important;
        --text-color: #111827 !important;
        --primary-color: #4F46E5 !important;
    }

    /* サイドバー全体：白に近い淡色背景＋濃色文字 */
    section[data-testid="stSidebar"],
    [data-testid="stSidebar"],
    [data-testid="stSidebarUserContent"],
    .stSidebar {
        background-color: #F9FAFB !important;
        background: #F9FAFB !important;
        border-right: 1px solid rgba(17, 24, 39, 0.08) !important;
        --text-color: #111827 !important;
        --primary-color: #4F46E5 !important;
    }
    [data-testid="stSidebar"] *,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] div,
    [data-testid="stSidebar"] a,
    [data-testid="stSidebar"] legend {
        color: #111827 !important;
    }
    [data-testid="stSidebar"] div[data-testid="stRadio"] label,
    [data-testid="stSidebar"] div[data-testid="stRadio"] p,
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] span,
    [data-testid="stSidebar"] legend {
        color: #111827 !important;
        font-weight: 600 !important;
    }

    /* フォントファミリー */
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=Noto+Sans+JP:wght@300;400;700&display=swap');
    html, body, [class*="css"] {
        font-family: 'Outfit', 'Noto Sans JP', sans-serif;
    }

    /* カード（旧 ガラスモフィズム）：白カード＋淡い枠線とソフト影 */
    .glass-card {
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 16px;
        padding: 24px;
        box-shadow: 0 4px 18px 0 rgba(15, 23, 42, 0.06);
        margin-bottom: 20px;
        color: #111827;
    }
    .glass-card * { color: #111827; }

    /* 通常ボタン：白背景＋濃色枠＋濃色文字 */
    .stButton > button {
        background: #FFFFFF;
        border: 1px solid #C7D2FE;
        color: #1F2937 !important;
        font-weight: 600;
        padding: 10px 24px;
        border-radius: 8px;
        box-shadow: 0 1px 2px 0 rgba(17, 24, 39, 0.05);
        transition: all 0.2s ease;
    }
    .stButton > button:hover {
        transform: translateY(-1px);
        background: #EEF2FF;
        border-color: #818CF8;
        box-shadow: 0 2px 8px 0 rgba(99, 102, 241, 0.15);
    }
    .stButton > button:disabled {
        background: #F3F4F6 !important;
        color: #9CA3AF !important;
        border-color: #E5E7EB !important;
    }
    /* primary ボタン（type="primary"）はインディゴで強調・文字は白 */
    .stButton > button[kind="primary"],
    .stButton > button[data-testid="baseButton-primary"],
    .stButton > button[data-testid="baseButton-primaryFormSubmit"],
    .stFormSubmitButton > button[kind="primary"],
    .stFormSubmitButton > button[data-testid="baseButton-primaryFormSubmit"],
    .stFormSubmitButton > button[data-testid="baseButton-primary"] {
        background: linear-gradient(90deg, #4F46E5, #6366F1) !important;
        color: #FFFFFF !important;
        border: 1px solid #4338CA !important;
    }
    /* グローバルな文字色強制を上書きするため、子要素にも白を強制適用 */
    .stButton > button[kind="primary"] *,
    .stButton > button[data-testid="baseButton-primary"] *,
    .stButton > button[data-testid="baseButton-primaryFormSubmit"] *,
    .stFormSubmitButton > button[kind="primary"] *,
    .stFormSubmitButton > button[data-testid="baseButton-primaryFormSubmit"] *,
    .stFormSubmitButton > button[data-testid="baseButton-primary"] *,
    .stButton > button[kind="primary"] p,
    .stButton > button[kind="primary"] span,
    .stButton > button[kind="primary"] div,
    .stFormSubmitButton > button[kind="primary"] p,
    .stFormSubmitButton > button[kind="primary"] span,
    .stFormSubmitButton > button[kind="primary"] div {
        color: #FFFFFF !important;
    }
    .stButton > button[kind="primary"]:hover,
    .stButton > button[data-testid="baseButton-primary"]:hover,
    .stButton > button[data-testid="baseButton-primaryFormSubmit"]:hover,
    .stFormSubmitButton > button[kind="primary"]:hover,
    .stFormSubmitButton > button[data-testid="baseButton-primaryFormSubmit"]:hover,
    .stFormSubmitButton > button[data-testid="baseButton-primary"]:hover {
        background: linear-gradient(90deg, #4338CA, #4F46E5) !important;
        color: #FFFFFF !important;
    }

    /* 入力フォーム：白背景＋濃色文字 */
    input, select, textarea {
        background-color: #FFFFFF !important;
        color: #111827 !important;
        border: 1px solid #D1D5DB !important;
        border-radius: 8px !important;
        font-size: 15px !important;
    }
    input::placeholder, textarea::placeholder {
        color: #9CA3AF !important;
    }
    /* Streamlit ラッパー対応 */
    div[data-baseweb="select"] > div,
    div[data-baseweb="input"] > div,
    div[data-baseweb="textarea"] > div {
        background-color: #FFFFFF !important;
        color: #111827 !important;
        border-color: #D1D5DB !important;
    }
    /* number_input の +/- ステッパー */
    button[data-testid="stNumberInputStepUp"],
    button[data-testid="stNumberInputStepDown"] {
        background-color: #F9FAFB !important;
        color: #1F2937 !important;
    }

    /* データフレーム・テーブル：白背景＋濃色文字 */
    div[data-testid="stDataFrame"] td, div[data-testid="stDataFrame"] th,
    div[data-testid="stTable"] td, div[data-testid="stTable"] th {
        color: #111827 !important;
        background-color: #FFFFFF !important;
    }
    div[data-testid="stDataFrame"] th {
        background-color: #F3F4F6 !important;
        font-weight: 700 !important;
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid #E5E7EB !important;
        border-radius: 8px;
        overflow: hidden;
    }

    /* メトリック表示 */
    div[data-testid="stMetricLabel"] { color: #4B5563 !important; }
    div[data-testid="stMetricValue"] {
        color: #4F46E5 !important;
        font-size: 28px !important;
        font-weight: bold !important;
    }

    /* アラート（info/success/warning/error）：淡色背景＋濃色文字 */
    .stAlert {
        border-radius: 12px;
        border: 1px solid #E5E7EB;
        background-color: #F9FAFB !important;
    }
    .stAlert p, .stAlert span, .stAlert div {
        color: #111827 !important;
    }
    /* type 別の左ボーダーで視認性向上 */
    div[data-testid="stAlert"][data-baseweb="notification"] {
        border-left: 4px solid #6366F1;
    }
    div[data-testid="stAlert"] a {
        color: #1D4ED8 !important;
        text-decoration: underline !important;
    }

    /* 受験URL表示 */
    .exam-url-display {
        background: #F8FAFC !important;
        border: 1px solid #C7D2FE !important;
        border-radius: 8px !important;
        padding: 12px 16px !important;
        margin: 8px 0 12px 0 !important;
        word-break: break-all !important;
        color: #1F2937 !important;
    }
    a.exam-url-link {
        color: #1D4ED8 !important;
        font-weight: 600 !important;
        text-decoration: underline !important;
    }
    a.exam-url-link:hover {
        color: #1E40AF !important;
    }

    /* st.code ブロック */
    div[data-testid="stCode"], div[data-testid="stCodeBlock"] {
        background: #F8FAFC !important;
        border: 1px solid #E5E7EB !important;
    }
    div[data-testid="stCode"] pre, div[data-testid="stCodeBlock"] pre,
    div[data-testid="stCode"] code, div[data-testid="stCodeBlock"] code {
        color: #1F2937 !important;
        background: transparent !important;
    }
    div[data-testid="stCode"] button, div[data-testid="stCodeBlock"] button {
        color: #1F2937 !important;
        border-color: #D1D5DB !important;
    }

    /* ダウンロードボタン */
    div[data-testid="stDownloadButton"] button,
    div.stDownloadButton button,
    [data-testid="stDownloadButton"] button {
        background: linear-gradient(90deg, #4F46E5, #6366F1) !important;
        color: #FFFFFF !important;
        border: 1px solid #4338CA !important;
        font-weight: 600 !important;
    }
    div[data-testid="stDownloadButton"] button:hover,
    div.stDownloadButton button:hover {
        background: linear-gradient(90deg, #4338CA, #4F46E5) !important;
        color: #FFFFFF !important;
        border-color: #3730A3 !important;
    }
    div[data-testid="stDownloadButton"] button p,
    div[data-testid="stDownloadButton"] button span,
    div[data-testid="stDownloadButton"] button div,
    div[data-testid="stDownloadButton"] button * {
        color: #FFFFFF !important;
    }

    /* expander（折りたたみ）の見出し */
    details > summary,
    [data-testid="stExpander"] summary {
        color: #111827 !important;
        background-color: #F9FAFB !important;
        border-radius: 8px !important;
    }
    [data-testid="stExpander"] {
        border: 1px solid #E5E7EB !important;
        border-radius: 8px !important;
    }

    /* ツールチップ等の補助テキスト */
    .stCaption, [data-testid="stCaptionContainer"] p {
        color: #6B7280 !important;
    }

    /* 区切り線 */
    hr {
        border: 0 !important;
        border-top: 1px solid #E5E7EB !important;
    }

    /* タブ */
    div[data-testid="stTabs"] button {
        color: #4B5563 !important;
    }
    div[data-testid="stTabs"] button[aria-selected="true"] {
        color: #4F46E5 !important;
        border-bottom-color: #4F46E5 !important;
    }

    /* 印刷時のスタイル */
    @media print {
        header, footer, .stSidebar, .stButton, div[data-testid="stSidebarCollapseButton"] {
            display: none !important;
        }
        .stApp {
            background: white !important;
            color: black !important;
        }
        .glass-card {
            background: none !important;
            border: 1px solid #ddd !important;
            box-shadow: none !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)

# --- 8. UI画面のレンダリング ---

# A. ログイン画面
def render_login_screen():
    st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align: center; color:#4338CA;'>🔐 管理者・作成者ログイン</h2>", unsafe_allow_html=True)

    # パスワード変更後の強制ログアウト通知（1 回のみ表示）
    pw_notice = st.session_state.pop("password_change_logout_notice", None)
    if pw_notice:
        st.info(pw_notice)

    username = st.text_input("ログインID (ユーザー名)")
    password = st.text_input("パスワード", type="password")
    
    if st.button("ログイン"):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, username, password, company_name, role FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()
        
        if user and user[2] == hash_password(password):
            st.session_state.logged_in = True
            st.session_state.user_id = user[0]
            st.session_state.username = user[1]
            st.session_state.company_name = user[3]
            st.session_state.role = user[4]
            st.toast(f"ようこそ、{user[1]}さん！ ({user[3]})")
            st.rerun()
        else:
            st.error("ログインIDまたはパスワードが正しくありません。")
            
    st.markdown("<p style='font-size:12px; color:#9CA3AF; text-align:center; margin-top:20px;'>※ログインアカウントは管理者が発行します。</p>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# 自身のプロフィールおよびパスワード設定画面
def render_profile_settings():
    st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
    st.markdown("<h2 style='text-align: center; color:#4338CA;'>🔑 自身のパスワード・所属の変更</h2>", unsafe_allow_html=True)
    st.write("ご自身のアカウントパスワードおよび登録情報を変更します。セキュリティのため、現在のパスワードの入力が必要です。")
    
    with st.form("profile_settings_form"):
        username = st.session_state.username
        st.text_input("ユーザー名（ログインID）", value=username, disabled=True)
        
        current_company = st.session_state.company_name
        new_company = st.text_input("所属名・組織名", value=current_company)
        
        current_password_input = st.text_input("現在のパスワード（必須）", type="password")
        st.markdown("<hr style='margin: 15px 0; border: 0; border-top: 1px solid #E5E7EB;'>", unsafe_allow_html=True)
        
        new_password = st.text_input("新しいパスワード（変更しない場合は空欄）", type="password")
        new_password_confirm = st.text_input("新しいパスワード（確認用）", type="password")
        
        submit = st.form_submit_button("設定を更新する", type="primary")
        
        if submit:
            if not current_password_input:
                st.error("現在のパスワードを入力してください。")
            else:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT password FROM users WHERE username = ?", (username,))
                user_record = c.fetchone()
                
                if not user_record or user_record[0] != hash_password(current_password_input):
                    st.error("現在のパスワードが正しくありません。")
                    conn.close()
                else:
                    password_changed = False
                    try:
                        if new_password:
                            if new_password != new_password_confirm:
                                st.error("新しいパスワードと確認用パスワードが一致しません。")
                                conn.close()
                                return
                            # パスワードと所属の更新
                            c.execute(
                                "UPDATE users SET company_name = ?, password = ? WHERE username = ?",
                                (new_company, hash_password(new_password), username)
                            )
                            password_changed = True
                        else:
                            # 所属のみの更新
                            c.execute(
                                "UPDATE users SET company_name = ? WHERE username = ?",
                                (new_company, username)
                            )
                        conn.commit()
                        st.session_state.company_name = new_company

                        if password_changed:
                            # パスワード変更後はセキュリティのため強制ログアウトし、
                            # ログイン画面で新パスワードによる再ログインを促す。
                            st.success(
                                "パスワードを変更しました。セキュリティのため一度ログアウトします。"
                                " 新しいパスワードで再度ログインしてください。"
                            )
                            st.toast("パスワードを変更しました。再ログインしてください。")
                            for k in (
                                "logged_in", "role", "user_id", "username", "company_name",
                            ):
                                if k in st.session_state:
                                    del st.session_state[k]
                            st.session_state.logged_in = False
                            st.session_state.role = None
                            st.session_state.user_id = None
                            st.session_state.username = None
                            st.session_state.password_change_logout_notice = (
                                "パスワードを変更しました。新しいパスワードで再ログインしてください。"
                            )
                            st.rerun()
                        else:
                            st.success("アカウント設定を正常に更新しました！")
                            st.toast("設定を更新しました。")
                            st.rerun()
                    except Exception as e:
                        st.error(f"更新中にエラーが発生しました: {e}")
                    finally:
                        conn.close()
    st.markdown("</div>", unsafe_allow_html=True)

# B. システム管理者画面 (ユーザー登録 / 全試験・全結果の閲覧と分析)
def render_admin_dashboard():
    st.sidebar.markdown(f"**ログイン中**: {st.session_state.username} (管理者)")
    st.sidebar.markdown(f"**所属**: {st.session_state.company_name}")
    
    menu = st.sidebar.radio("管理メニュー", ["ユーザー(他者)の登録と管理", "すべての試験問題管理", "すべての受験結果・分析", "自身のパスワード・所属の変更"])
    
    if st.sidebar.button("ログアウト"):
        st.session_state.logged_in = False
        st.session_state.role = None
        st.session_state.user_id = None
        st.rerun()

    # 1. ユーザー（他者）の登録と管理
    if menu == "ユーザー(他者)の登録と管理":
        st.title("👥 新規問題作成ユーザー（他者）の登録")
        render_exam_users(exclude_user_id=st.session_state.user_id)

    # 2. すべての試験問題管理
    elif menu == "すべての試験問題管理":
        st.title("📝 すべての試験問題管理")
        st.write("現在システムに登録されているすべての試験問題を確認・管理できます（管理者権限）。")
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT e.id, e.title, e.description, e.status, e.limit_time, e.created_at,
                   e.created_by, u.username
            FROM exams e
            JOIN users u ON e.created_by = u.id
            ORDER BY e.created_at DESC
        """)
        exams_list = c.fetchall()
        conn.close()
        
        if exams_list:
            for ex in exams_list:
                render_exam_manage_expander(ex, show_owner=True)
        else:
            st.info("登録されている試験はありません。")

    # 3. すべての受験結果・分析
    elif menu == "すべての受験結果・分析":
        st.title("📊 すべての受験結果および解答傾向分析")
        st.write("システム内のすべての受験データをもとに、解答傾向の分析およびPDFレポートの生成を行います。")
        
        # 試験選択
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, title, schema FROM exams")
        all_exams = c.fetchall()
        conn.close()
        
        if not all_exams:
            st.info("試験が登録されていません。")
            return
            
        exam_options = {f"【{e[0]}】{e[1]}": (e[0], e[1], json.loads(e[2])) for e in all_exams}
        selected_key = st.selectbox("分析対象の試験を選択してください", list(exam_options.keys()))
        exam_id, exam_title, schema = exam_options[selected_key]
        
        # 該当試験の回答データの取得
        conn = get_db_connection()
        submissions_df = pd.read_sql_query(
            "SELECT * FROM submissions WHERE exam_id = ? ORDER BY submitted_at DESC", conn, params=(exam_id,)
        )
        conn.close()
        
        if submissions_df.empty:
            st.warning("この試験に対する受験データがまだありません。")
            return

        grading_config = fetch_grading_config(exam_id)
        render_analytics_content(submissions_df, exam_id, exam_title, schema, grading_config)

    elif menu == "自身のパスワード・所属の変更":
        render_profile_settings()

# C. 問題作成者画面 (自身の試験作成、自身が担当する試験結果の閲覧と分析)
def render_creator_dashboard():
    st.sidebar.markdown(f"**ログイン中**: {st.session_state.username} (問題作成者)")
    st.sidebar.markdown(f"**所属**: {st.session_state.company_name}")
    
    menu = st.sidebar.radio("作成メニュー", ["試験問題の作成・編集", "担当試験の受験結果・分析", "自身のパスワード・所属の変更"])
    
    if st.sidebar.button("ログアウト"):
        st.session_state.logged_in = False
        st.session_state.role = None
        st.session_state.user_id = None
        st.rerun()

    # 1. 試験問題の作成・編集
    if menu == "試験問題の作成・編集":
        st.title("📝 試験問題の作成・編集")
        st.write(
            "新規作成、既存試験の設問編集、他試験からのコピー、編集権限の付与が行えます。"
            "受験URLは `http://{0}/exam/?ID=試験コード` 形式です。".format(EXAM_HOST)
        )

        if "questions_builder" not in st.session_state:
            st.session_state.questions_builder = []
        if "edit_questions_builder" not in st.session_state:
            st.session_state.edit_questions_builder = []
        if "exam_form_mode" not in st.session_state:
            st.session_state.exam_form_mode = "create"
        if "new_exam_id" not in st.session_state:
            st.session_state.new_exam_id = f"EX-{uuid.uuid4().hex[:6].upper()}"

        is_editing = (
            st.session_state.get("exam_form_mode") == "edit"
            and st.session_state.get("editing_exam_id")
        )

        # --- 既存試験の編集パネル ---
        if is_editing:
            edit_id = st.session_state.editing_exam_id
            if not user_can_edit_exam(st.session_state.user_id, st.session_state.role, edit_id):
                st.error("この試験を編集する権限がありません。")
                clear_edit_widget_session_state(edit_id=edit_id)
                st.session_state.exam_form_mode = "create"
                st.session_state.editing_exam_id = None
                st.session_state.edit_questions_builder = []
            else:
                st.subheader(f"✏️ 試験の編集: {edit_id}")
                col_cancel, _ = st.columns([1, 4])
                if col_cancel.button("編集をキャンセル"):
                    clear_edit_widget_session_state(edit_id=edit_id)
                    st.session_state.exam_form_mode = "create"
                    st.session_state.editing_exam_id = None
                    st.session_state.edit_questions_builder = []
                    st.rerun()

                # 既存提出データがある場合の警告（設問差替えは過去データの整合を壊しうる）
                sub_count = count_submissions_for_exam(edit_id)
                if sub_count > 0:
                    st.warning(
                        f"⚠️ この試験には既に **{sub_count} 件**の受験提出データが存在します。\n\n"
                        "**設問の追加・削除・並び替え・正解の変更は、過去の受験結果と整合しなくなります。**"
                        " 過去データは提出当時のスキーマで個別に保持されますが、本試験の集計（正答率など）は"
                        " 設問変更の影響を受けます。設問構成を大きく変える場合は"
                        " 「コピーして新規作成」で別試験コードとして公開することを強く推奨します。"
                    )
                    if not st.session_state.get(f"edit_unlock_{edit_id}"):
                        if st.checkbox(
                            "上記の影響を理解した上で設問編集を許可する",
                            key=f"edit_unlock_chk_{edit_id}",
                        ):
                            st.session_state[f"edit_unlock_{edit_id}"] = True
                            st.rerun()
                    edit_locked = not st.session_state.get(f"edit_unlock_{edit_id}", False)
                else:
                    edit_locked = False
                e_title = st.text_input("試験タイトル", key="edit_title_inp")
                e_desc = st.text_area("試験の説明・概要", key="edit_desc_inp")
                e_limit = st.number_input(
                    "制限時間（分、0で無制限）",
                    min_value=0,
                    max_value=180,
                    key="edit_limit_inp",
                )
                st.markdown("**設問の追加・削除・変更**")
                render_csv_questions_import(
                    section_id=f"edit_{edit_id}",
                    questions_session_key="edit_questions_builder",
                    widget_prefix=f"edit_{edit_id}",
                    edit_exam_id=edit_id,
                )
                render_questions_builder(
                    st.session_state.edit_questions_builder, f"edit_{edit_id}"
                )
                render_grading_config_editor(f"edit_grading_{edit_id}")
                if edit_locked:
                    st.info(
                        "既存の受験提出データがあるため、変更の保存はロックされています。"
                        " 上のチェックボックスで影響を承諾するか、「コピーして新規作成」をご利用ください。"
                    )
                save_clicked = st.button(
                    "変更を保存する",
                    type="primary",
                    key="save_edit_exam",
                    disabled=edit_locked,
                )
                if save_clicked:
                    if not e_title:
                        st.error("試験タイトルは必須です。")
                    elif len(st.session_state.edit_questions_builder) == 0:
                        st.error("少なくとも1つの設問を設定してください。")
                    else:
                        conn = get_db_connection()
                        c = conn.cursor()
                        grading_json = json.dumps(
                            collect_grading_config_from_session(f"edit_grading_{edit_id}"),
                            ensure_ascii=False,
                        )
                        c.execute(
                            "UPDATE exams SET title=?, description=?, limit_time=?, schema=?, grading_config=? WHERE id=?",
                            (
                                e_title, e_desc, e_limit,
                                json.dumps(
                                    normalize_questions_schema(
                                        st.session_state.edit_questions_builder
                                    ),
                                    ensure_ascii=False,
                                ),
                                grading_json,
                                edit_id,
                            ),
                        )
                        conn.commit()
                        conn.close()
                        st.success("試験内容を更新しました。")
                        clear_edit_widget_session_state(edit_id=edit_id)
                        st.session_state.exam_form_mode = "create"
                        st.session_state.editing_exam_id = None
                        st.session_state.edit_questions_builder = []
                        render_exam_url_box(edit_id)
                        st.rerun()
                st.markdown("<hr>", unsafe_allow_html=True)

        # --- 新規作成パネル（編集中は非表示） ---
        if is_editing:
            st.info("既存試験を編集中です。新規作成する場合は、上の「編集をキャンセル」を押してください。")
        else:
            st.subheader("新規試験の作成")
            col_id, col_time = st.columns([2, 1])
            with col_id:
                exam_title = st.text_input(
                    "試験タイトル",
                    value=st.session_state.get("new_exam_title", ""),
                    placeholder="例: HTML/CSS 基礎検定",
                    key="new_title_inp",
                )
                exam_desc = st.text_area(
                    "試験の説明・概要",
                    value=st.session_state.get("new_exam_desc", ""),
                    placeholder="例: フロントエンドの基礎知識を確認します。",
                    key="new_desc_inp",
                )
            with col_time:
                st.text_input(
                    "試験コード (一意)",
                    value=st.session_state.new_exam_id,
                    disabled=True,
                    key="new_id_inp",
                )
                if st.button("試験コードを再生成", key="regen_exam_id"):
                    st.session_state.new_exam_id = f"EX-{uuid.uuid4().hex[:6].upper()}"
                    st.rerun()
                limit_time = st.number_input(
                    "制限時間（分、0で無制限）",
                    min_value=0, max_value=180,
                    value=int(st.session_state.get("new_exam_limit", 0)),
                    key="new_limit_inp",
                )

            st.markdown("**設問の設定**")
            render_csv_questions_import(
                section_id="new",
                questions_session_key="questions_builder",
                widget_prefix="new",
            )
            render_questions_builder(st.session_state.questions_builder, "new")
            render_grading_config_editor("new_grading")

        if not is_editing and st.button("試験を登録・公開する", type="primary", key="save_new_exam"):
            if not exam_title:
                st.error("試験タイトルは必須です。")
            elif len(st.session_state.questions_builder) == 0:
                st.error("少なくとも1つの設問を設定してください。")
            else:
                conn = get_db_connection()
                c = conn.cursor()
                try:
                    grading_json = json.dumps(
                        collect_grading_config_from_session("new_grading"),
                        ensure_ascii=False,
                    )
                    c.execute(
                        "INSERT INTO exams (id, title, description, status, limit_time, schema, created_by, created_at, grading_config) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            st.session_state.new_exam_id,
                            exam_title,
                            exam_desc,
                            "公開",
                            limit_time,
                            json.dumps(
                                normalize_questions_schema(st.session_state.questions_builder),
                                ensure_ascii=False,
                            ),
                            st.session_state.user_id,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            grading_json,
                        ),
                    )
                    conn.commit()
                    saved_id = st.session_state.new_exam_id
                    st.success("試験を登録・公開しました！")
                    st.session_state.questions_builder = []
                    st.session_state.new_exam_id = f"EX-{uuid.uuid4().hex[:6].upper()}"
                    for k in ("new_exam_title", "new_exam_desc", "new_exam_limit"):
                        st.session_state.pop(k, None)
                    render_exam_url_box(saved_id)
                except sqlite3.IntegrityError:
                    st.error("その試験コードは既に使用されています。「試験コードを再生成」を押してください。")
                finally:
                    conn.close()

        st.markdown("<hr>", unsafe_allow_html=True)
        st.subheader("作成・編集可能な試験一覧")
        editable_exams = get_editable_exams(st.session_state.user_id, st.session_state.role)
        if editable_exams:
            for ex in editable_exams:
                show_owner = ex[6] != st.session_state.user_id
                render_exam_manage_expander(ex, show_owner=show_owner)
        else:
            st.info("まだ試験がありません。")

    # 2. 担当試験の受験結果・分析
    elif menu == "担当試験の受験結果・分析":
        st.title("📊 担当試験の受験結果と解答傾向分析")
        st.write(
            "ご自身が作成した試験、または編集・分析権限を付与された試験の"
            "受験データの表示および分析レポートの生成を行います。"
        )

        analyzable = get_analyzable_exams(st.session_state.user_id, st.session_state.role)
        if not analyzable:
            st.info("分析対象の試験がありません。試験を作成するか、他者から権限を付与してもらってください。")
            return

        exam_options = {}
        for ex_id, title, schema_str, created_by, owner_name in analyzable:
            suffix = f" (作成者: {owner_name})" if created_by != st.session_state.user_id else ""
            label = f"【{ex_id}】{title}{suffix}"
            exam_options[label] = (ex_id, title, json.loads(schema_str))

        selected_key = st.selectbox("分析対象の試験を選択してください", list(exam_options.keys()))
        exam_id, exam_title, schema = exam_options[selected_key]

        if not user_can_access_exam(st.session_state.user_id, st.session_state.role, exam_id):
            st.error("この試験の結果を参照する権限がありません。")
            return

        # 該当試験に対する回答データを取得
        conn = get_db_connection()
        submissions_df = pd.read_sql_query(
            "SELECT * FROM submissions WHERE exam_id = ? ORDER BY submitted_at DESC", conn, params=(exam_id,)
        )
        conn.close()
        
        if submissions_df.empty:
            st.warning("この試験に対する受験データがまだありません。")
            return
            
        grading_config = fetch_grading_config(exam_id)
        render_analytics_content(submissions_df, exam_id, exam_title, schema, grading_config)

    elif menu == "自身のパスワード・所属の変更":
        render_profile_settings()

# 解答傾向分析のコンテンツ表示（管理者・作成者共通処理）
def render_analytics_content(submissions_df, exam_id, exam_title, schema, grading_config=None):
    total_users = len(submissions_df)
    avg_score = submissions_df['score'].mean()
    max_score = submissions_df['score'].max()
    min_score = submissions_df['score'].min()
    
    st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
    st.subheader(f"📈 基本統計情報 ({exam_title})")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("総受験者数", f"{total_users} 名")
    col2.metric("平均得点", f"{avg_score:.1f} 点")
    col3.metric("最高得点", f"{max_score:.1f} 点")
    col4.metric("最低得点", f"{min_score:.1f} 点")
    st.markdown("</div>", unsafe_allow_html=True)

    cfg = normalize_grading_config(grading_config)
    if cfg["mode"] != "none" and total_users > 0:
        grade_counts = {}
        for _, s in submissions_df.iterrows():
            g = evaluate_score_grade(s["score"], s["total_points"], cfg)
            if g:
                grade_counts[g] = grade_counts.get(g, 0) + 1
        if grade_counts:
            st.caption(
                "総合評価の内訳: "
                + " / ".join(f"{label} {cnt}名" for label, cnt in sorted(grade_counts.items(), key=lambda x: -x[1]))
            )

    render_submission_details_list(submissions_df, schema, exam_id, exam_title, grading_config)

    st.subheader("回答状況の分析")
    
    # 設問ごとの正答率集計（question_id 単位で結合。設問が提出時に存在しなかった
    # 受験者は分母から除外し、未提出データによる IndexError も発生しないようにする）
    all_results = []
    for idx, row in submissions_df.iterrows():
        res_list = json.loads(row['results'])
        all_results.append({r.get("question_id"): r for r in res_list if isinstance(r, dict)})

    q_stats = []
    q_labels = []
    q_rates = []

    for i, q in enumerate(schema):
        qid = q.get("id")
        answered = [r for r in all_results if qid in r]
        corrects = sum(1 for r in answered if r[qid].get('is_correct'))
        denom = len(answered)
        rate = (corrects / denom) * 100 if denom > 0 else 0
        q_stats.append({
            "question": q["question"],
            "category": q["category"],
            "points": q["points"],
            "correct_rate": rate,
            "answered_count": denom,
        })
        q_labels.append(f"問 {i+1}")
        q_rates.append(rate)
        
    # Matplotlibで美しい棒グラフを作成
    fig, ax = plt.subplots(figsize=(8, 4))
    bars = ax.bar(q_labels, q_rates, color=['#6366F1' if r >= 60 else '#F43F5E' for r in q_rates], edgecolor='none', width=0.6)
    ax.set_ylabel('正答率 (%)', color='#1F2937')
    ax.set_title('設問別正答率 (%)', color='#1F2937', fontsize=14, pad=15)
    ax.set_ylim(0, 100)
    ax.tick_params(colors='#374151')
    ax.spines['bottom'].set_color('#9CA3AF')
    ax.spines['left'].set_color('#9CA3AF')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.set_facecolor('#FFFFFF')
    fig.patch.set_facecolor('#FFFFFF')

    # パーセントラベルを棒の上に追加
    for bar in bars:
        height = bar.get_height()
        ax.annotate(f'{height:.1f}%',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center', va='bottom', color='#1F2937')

    # グラフを画面表示
    st.pyplot(fig)

    # グラフ画像をテンポラリファイルへ保存（PDF出力用）
    tmp_chart = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    fig.savefig(tmp_chart.name, bbox_inches='tight', facecolor='#FFFFFF', transparent=False)
    plt.close(fig)
    
    # AI全体傾向分析
    st.subheader("🤖 AIによる解答傾向の総合分析")
    
    # キャッシュキー
    ai_cache_key = f"ai_eval_{submissions_df.iloc[0]['submitted_at']}_{len(submissions_df)}"
    if ai_cache_key not in st.session_state:
        with st.spinner("AIが解答傾向の分析および指導アドバイスを生成中..."):
            ai_text = ai_analyze_submissions(submissions_df, exam_title, schema)
            st.session_state[ai_cache_key] = ai_text
            
    st.info(st.session_state[ai_cache_key])
    
    # PDFおよびCSVのダウンロードボタン
    st.subheader("📥 結果レポートのダウンロード")
    if not HAS_FPDF or not FPDF:
        st.warning("⚠️ PDF生成ライブラリ (fpdf) がインストールされていないため、PDFレポートのダウンロードは無効化されています。")
        pdf_bytes = None
    else:
        pdf_bytes = generate_pdf_report(
            exam_title, total_users, avg_score, max_score, min_score,
            tmp_chart.name, st.session_state[ai_cache_key], q_stats
        )
    
    col_pdf, col_csv = st.columns(2)
    with col_pdf:
        if pdf_bytes:
            st.download_button(
                label="📄 分析レポートPDFをダウンロード",
                data=pdf_bytes,
                file_name=f"exam_report_{exam_title}.pdf",
                mime="application/pdf"
            )
            
    with col_csv:
        rows = []
        for _, s in submissions_df.iterrows():
            tp = s["total_points"] or 1
            row_out = {
                "受験者氏名": s["examinee_name"],
                "メールアドレス": s["examinee_email"],
                "合計得点": s["score"],
                "満点": s["total_points"],
                "正解率": f"{(s['score'] / tp) * 100:.1f}%",
                "提出日時": s["submitted_at"],
            }
            grade = evaluate_score_grade(s["score"], tp, grading_config)
            if grade is not None:
                row_out["総合評価"] = grade
            rows.append(row_out)
        csv_df = pd.DataFrame(rows)
        csv_encoding = st.radio(
            "CSV文字コード",
            ["Shift_JIS", "UTF-8"],
            index=0,
            horizontal=True,
            key=f"csv_enc_{exam_title}",
        )
        csv_bytes = encode_csv_dataframe(csv_df, csv_encoding)
        st.download_button(
            label="受験者一覧CSVをダウンロード",
            data=csv_bytes,
            file_name=f"examinees_{exam_title}.csv",
            mime="text/csv",
            key=f"dl_csv_{exam_title}",
        )
        
    # 一時ファイルの削除
    try:
        os.unlink(tmp_chart.name)
    except:
        pass

# D. 受験者画面 (試験の回答と自動採点・結果表示)
def render_examinee_screen(exam_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "SELECT title, description, limit_time, schema, grading_config, status FROM exams WHERE id = ?",
        (exam_id,),
    )
    exam = c.fetchone()
    conn.close()
    
    if not exam:
        st.error("指定された試験問題は存在しないか、URLが正しくありません。")
        return
        
    exam_title, exam_desc, limit_time, schema_str, grading_raw, exam_status = exam

    if not is_exam_published(exam_status):
        st.error(
            "この試験は現在「非公開」に設定されているため、受験することはできません。\n\n"
            "受験URLをお知らせした担当者にお問い合わせください。"
        )
        return
    schema = json.loads(schema_str)
    grading_config = parse_grading_config(grading_raw)
    
    # 受験状態セッション管理
    state_key = f"exam_state_{exam_id}"
    if state_key not in st.session_state:
        st.session_state[state_key] = "register" # register, in_progress, completed
        st.session_state[f"start_time_{exam_id}"] = None
        st.session_state[f"examinee_name_{exam_id}"] = ""
        st.session_state[f"examinee_email_{exam_id}"] = ""
        
    # 1. 受験者情報の登録画面
    if st.session_state[state_key] == "register":
        st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
        st.markdown(f"<h2 style='color:#4338CA; text-align:center;'>📝 {exam_title}</h2>", unsafe_allow_html=True)
        st.write(exam_desc)
        if limit_time > 0:
            st.warning(f"⏱️ **制限時間:** {limit_time} 分 (送信ボタンを押すまで時間がカウントされます)")
        else:
            st.info("⏱️ 制限時間はありません。じっくり取り組んでください。")
            
        st.markdown("<hr>", unsafe_allow_html=True)
        st.subheader("👤 受験者情報の登録")
        
        name = st.text_input("氏名 (漢字で入力してください)")
        email = st.text_input("フィードバック受信用メールアドレス（必須）", placeholder="例: email@example.com")
        
        # 簡易メールバリデーション
        if st.button("試験を開始する", type="primary"):
            if not name:
                st.error("氏名を入力してください。")
            elif not email or "@" not in email or "." not in email:
                st.error("有効なメールアドレスを正しく入力してください。")
            else:
                st.session_state[state_key] = "in_progress"
                st.session_state[f"start_time_{exam_id}"] = datetime.now()
                st.session_state[f"examinee_name_{exam_id}"] = name
                st.session_state[f"examinee_email_{exam_id}"] = email
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # 2. 試験回答画面
    elif st.session_state[state_key] == "in_progress":
        st.title(f"⏱️ 受験中: {exam_title}")
        st.write(f"受験者: {st.session_state[f'examinee_name_{exam_id}']} 様")
        
        # タイマーの表示
        if limit_time > 0:
            elapsed = (datetime.now() - st.session_state[f"start_time_{exam_id}"]).total_seconds()
            remaining = (limit_time * 60) - elapsed
            
            if remaining <= 0:
                st.error("⚠️ 制限時間が終了しました！回答を自動的に提出します。")
                # 提出処理へ強制ジャンプ（以下の回答を空として送信）
                remaining = 0
            else:
                mins, secs = divmod(int(remaining), 60)
                st.markdown(f"<h3 style='color:#DC2626; text-align:right;'>⏳ 残り時間: {mins:02d}分{secs:02d}秒</h3>", unsafe_allow_html=True)
                # タイマー更新のための簡易的リロードトリガー
                if st.button("🔄 タイマーを更新"):
                    st.rerun()
                    
        # 設問フォームの生成
        st.caption(
            "🟠 マークの設問は必須項目です。択一・複数選択は1つ以上選択するまで提出できません。"
        )
        with st.form("exam_answer_form"):
            answers = {}
            # 設問種別ごとに必須かどうかを判定するヘルパー
            def _is_required(q):
                if q["type"] in ("択一選択", "複数選択", "○×式"):
                    return True
                if q["type"] in TEXT_QUESTION_TYPES:
                    return bool(q.get("required", False))
                return False

            for i, q in enumerate(schema):
                st.markdown(f"<div class='glass-card' style='margin-bottom:15px;'>", unsafe_allow_html=True)
                required_mark = (
                    "<span style='color:#DC2626; font-weight:bold; margin-left:6px;'>🟠 必須</span>"
                    if _is_required(q)
                    else ""
                )
                st.markdown(
                    f"**問 {i+1}: {q['question']}** (配点: {q['points']}点){required_mark}",
                    unsafe_allow_html=True,
                )

                if q["type"] == "択一選択":
                    if is_new_option_format(q):
                        display_map = {
                            opt: format_option_display(opt, txt)
                            for opt, txt in zip(q["options"], q["option_texts"])
                        }
                        answers[q["id"]] = st.radio(
                            "選択肢から選んでください:",
                            q["options"],
                            index=None,
                            format_func=lambda v, m=display_map: m.get(v, v),
                            key=f"ans_q_{i}",
                        )
                    else:
                        answers[q["id"]] = st.radio(
                            "選択肢から選んでください:",
                            q["options"],
                            index=None,
                            key=f"ans_q_{i}",
                        )
                elif q["type"] == "複数選択":
                    answers[q["id"]] = []
                    st.write("該当するものをすべて選択してください（複数可）:")
                    if is_new_option_format(q):
                        for opt, txt in zip(q["options"], q["option_texts"]):
                            disp = format_option_display(opt, txt)
                            if st.checkbox(disp, key=f"ans_cb_{i}_{opt}"):
                                answers[q["id"]].append(opt)
                    else:
                        for opt in q["options"]:
                            if st.checkbox(opt, key=f"ans_cb_{i}_{opt}"):
                                answers[q["id"]].append(opt)
                elif q["type"] == "○×式":
                    answers[q["id"]] = st.radio(
                        "正しいか誤りか選んでください:",
                        q["options"],
                        index=None,
                        key=f"ans_q_{i}",
                    )
                elif q["type"] == "テキスト（記述式）":
                    answers[q["id"]] = st.text_input("記述回答（短い英単語・用語など）:", key=f"ans_q_{i}")
                elif q["type"] == "テキストエリア（長文記述）":
                    answers[q["id"]] = st.text_area("記述回答（説明文など）:", key=f"ans_q_{i}")

                st.markdown("</div>", unsafe_allow_html=True)

            submit_exam = st.form_submit_button("回答を提出する", type="primary")

            if submit_exam:
                # --- 提出時バリデーション ---
                forced_timeout = bool(limit_time > 0 and locals().get("remaining", 1) <= 0)
                missing = []
                if not forced_timeout:
                    for i, q in enumerate(schema):
                        ans = answers.get(q["id"])
                        if q["type"] == "択一選択":
                            if ans in (None, ""):
                                missing.append((i + 1, q["question"], "未選択"))
                        elif q["type"] == "複数選択":
                            if not (isinstance(ans, list) and len(ans) > 0):
                                missing.append((i + 1, q["question"], "1つ以上選択してください"))
                        elif q["type"] == "○×式":
                            if ans in (None, ""):
                                missing.append((i + 1, q["question"], "未選択"))
                        elif q["type"] in TEXT_QUESTION_TYPES and q.get("required"):
                            if not (isinstance(ans, str) and ans.strip()):
                                missing.append((i + 1, q["question"], "必須項目が未記入"))

                if missing:
                    st.error(
                        f"未回答／必須未記入の設問が {len(missing)} 件あります。すべての項目を入力してから提出してください。"
                    )
                    bullet_html = "".join(
                        f"<li>問 {n}: {q_text}<span style='color:#DC2626; margin-left:8px;'>（{reason}）</span></li>"
                        for n, q_text, reason in missing
                    )
                    st.markdown(
                        f"<ul style='color:#1F2937; background:#FEF2F2; padding:12px 24px; border-radius:6px; border:1px solid #FCA5A5;'>{bullet_html}</ul>",
                        unsafe_allow_html=True,
                    )
                else:
                    with st.spinner("採点中および結果送信処理中..."):
                        process_submission(exam_id, schema, answers)
                        st.session_state[state_key] = "completed"
                        st.rerun()

    # 3. 受験完了・採点結果表示画面
    elif st.session_state[state_key] == "completed":
        st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
        st.success("試験が正常に提出され、採点が完了しました！")
        examinee_email = st.session_state[f"examinee_email_{exam_id}"]

        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "SELECT score, total_points, results, email_sent FROM submissions WHERE exam_id = ? AND examinee_email = ? ORDER BY id DESC LIMIT 1",
            (exam_id, examinee_email),
        )
        sub = c.fetchone()
        conn.close()

        if not is_smtp_configured():
            st.info(
                f"採点結果はこの画面でご確認ください（メール自動送信はサーバー側SMTP未設定のため行われていません）。"
                f" 登録メール: **{examinee_email}**"
            )
        elif sub and sub[3]:
            st.write(
                f"採点結果を登録メール（ **{examinee_email}** ）宛に送信しました。"
                "届かない場合は迷惑メールフォルダをご確認ください。"
            )
        else:
            st.warning(
                f"採点結果のメール送信に失敗しました（ **{examinee_email}** ）。"
                "画面の結果をご確認のうえ、必要に応じて管理者へお問い合わせください。"
            )
        
        if sub:
            score, total_points, results_str, _email_sent_flag = sub
            results = json.loads(results_str)
            
            st.markdown(f"<div style='background:#EEF2FF; border:1px solid #C7D2FE; padding:20px; border-radius:12px; text-align:center; margin:20px 0;'>", unsafe_allow_html=True)
            st.markdown(f"<span style='font-size:20px; color:#4B5563;'>あなたの得点</span><br>", unsafe_allow_html=True)
            st.markdown(f"<span style='font-size:48px; font-weight:bold; color:#4338CA;'>{score:.1f}</span> / {total_points:.1f} 点", unsafe_allow_html=True)
            st.markdown(f"正解率: <b>{((score/total_points)*100):.1f}%</b>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            grade_label = evaluate_score_grade(score, total_points, grading_config)
            render_grade_badge(grade_label, grading_config)

            st.subheader("設問別の解説・正誤")
            for i, res in enumerate(results):
                render_question_result_block(schema[i], res, question_no=i + 1)
                
        if st.button("トップ画面に戻る"):
            del st.session_state[state_key]
            st.query_params.clear()
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

# 受験回答提出の採点および保存・メール送信処理
def process_submission(exam_id, schema, answers):
    examinee_name = st.session_state[f"examinee_name_{exam_id}"]
    examinee_email = st.session_state[f"examinee_email_{exam_id}"]
    
    total_score = 0.0
    total_points = 0.0
    results_list = []
    
    for i, q in enumerate(schema):
        q_id = q["id"]
        points = float(q["points"])
        total_points += points
        student_ans = answers.get(q_id, "")
        
        # 形式に応じた採点ロジック
        if q["type"] in ["択一選択", "○×式"]:
            is_correct = (str(q["correct_answer"]).strip() == str(student_ans).strip())
            earned = points if is_correct else 0.0
            feedback = "正解です！" if is_correct else "残念ながら不正解です。"
        elif q["type"] == "複数選択":
            # リスト同士の一致判定
            correct_set = set(q["correct_answer"])
            student_set = set(student_ans) if isinstance(student_ans, list) else set([student_ans])
            is_correct = (correct_set == student_set)
            earned = points if is_correct else 0.0
            feedback = "選択項目がすべて一致しました！" if is_correct else "一部の選択肢に誤りがあります。"
        else:
            # 記述式: Gemini API による採点アシストを呼び出す
            grade = ai_grade_question(q["question"], points, q["correct_answer"], student_ans)
            earned = float(grade["score"])
            is_correct = bool(grade["is_correct"])
            feedback = grade["feedback"]
            
        total_score += earned
        results_list.append({
            "question_id": q_id,
            "student_answer": student_ans,
            "earned": earned,
            "is_correct": is_correct,
            "feedback": feedback
        })
        
    # メール送信
    exam_row = fetch_exam_record(exam_id)
    mail_exam_title = exam_row[1] if exam_row else "試験結果"
    grading_config = parse_grading_config(exam_row[7]) if exam_row else {"mode": "none"}
    email_success = send_result_email(
        examinee_email, examinee_name, mail_exam_title, total_score, total_points,
        results_list, schema, grading_config,
    )
    email_sent_flag = 1 if email_success else 0
    
    # データベースに書き込み（提出時の schema をスナップショットとして保存）
    schema_snapshot = build_schema_snapshot(schema)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO submissions (exam_id, examinee_name, examinee_email, answers, score, total_points, results, email_sent, submitted_at, schema_snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            exam_id,
            examinee_name,
            examinee_email,
            json.dumps(answers, ensure_ascii=False),
            total_score,
            total_points,
            json.dumps(results_list, ensure_ascii=False),
            email_sent_flag,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            json.dumps(schema_snapshot, ensure_ascii=False),
        )
    )
    conn.commit()
    conn.close()

# --- 9. メインルーチン ---
def main():
    st.set_page_config(page_title="試験問題作成・採点・評価システム", layout="wide")
    init_db()
    apply_custom_styles()
    
    # セッションステート初期化
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'role' not in st.session_state:
        st.session_state.role = None
    if 'username' not in st.session_state:
        st.session_state.username = None
        
    params = st.query_params
    
    # 画面制御ルーティング
    if "ID" in params:
        # 受験者用画面の表示
        render_examinee_screen(params["ID"])
    else:
        # 管理者・作成者ログインおよびダッシュボード表示
        if not st.session_state.logged_in:
            render_login_screen()
        else:
            if st.session_state.role == 'admin':
                render_admin_dashboard()
            elif st.session_state.role == 'creator':
                render_creator_dashboard()

if __name__ == "__main__":
    main()
