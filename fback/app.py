import streamlit as st
import streamlit.components.v1 as components
import sqlite3
import json
import os
import re
import hashlib
import secrets
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import uuid
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from dotenv import load_dotenv
import google.generativeai as genai
from fpdf import FPDF
import tempfile
import pandas as pd

# .env: Gemini（fback）+ SMTP（exam と共通）
load_dotenv()
load_dotenv("/opt/exam/.env")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FBACK_HOST = os.getenv("FBACK_HOST", "172.16.16.10")
FBACK_PORT = os.getenv("FBACK_PORT", "8503")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# 日本語フォントの設定（画面のmatplotlibグラフ用文字化け対策）
font_path = "/opt/fback/assets/ipaexg.ttf"
if os.path.exists(font_path):
    fm.fontManager.addfont(font_path)
    plt.rcParams['font.family'] = 'IPAexGothic'
else:
    plt.rcParams['font.family'] = 'sans-serif'

# --- データベース操作 ---
def get_db_connection():
    return sqlite3.connect('universal_feedback.db', check_same_thread=False)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def normalize_email(email):
    return email.strip().lower()

def is_valid_email(email):
    if not email:
        return False
    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, normalize_email(email)) is not None

def get_admin_portal_url():
    return f"http://{FBACK_HOST}:{FBACK_PORT}/?admin=true"

def get_survey_url(survey_id: str) -> str:
    return f"http://{FBACK_HOST}:{FBACK_PORT}/?ID={survey_id}"

def inject_survey_url_styles():
    st.markdown(
        """
        <style>
        .survey-url-display {
            background: rgba(15, 23, 42, 0.92) !important;
            border: 1px solid rgba(139, 92, 246, 0.55) !important;
            border-radius: 8px !important;
            padding: 12px 16px !important;
            margin: 8px 0 12px 0 !important;
            word-break: break-all !important;
        }
        a.survey-url-link {
            color: #2563EB !important;
            font-weight: 600 !important;
            text-decoration: underline !important;
        }
        a.survey-url-link:hover {
            color: #1D4ED8 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

def render_survey_url_box(survey_id: str):
    """回答用URLを表示し、クリップボードコピーを提供する（exam_app.py と同様）。"""
    inject_survey_url_styles()
    url = get_survey_url(survey_id)
    btn_id = "copy_" + survey_id.replace("-", "_").replace(".", "_")
    st.markdown("**回答者への配布用URL**")
    st.markdown(
        f'<div class="survey-url-display">'
        f'<a class="survey-url-link" href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
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

def is_smtp_configured():
    keys = ("SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM")
    if not all(os.getenv(k, "").strip() for k in keys):
        return False
    user = os.getenv("SMTP_USER", "").lower()
    pwd = os.getenv("SMTP_PASSWORD", "").lower()
    placeholders = ("your_email", "your_app_password", "example.com", "xxx", "password")
    if any(p in user or p in pwd for p in placeholders):
        return False
    return True

def generate_random_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

def send_user_registration_email(to_email, plain_password, company_name):
    """新規登録・再送用: ユーザー登録完了メールを送信する。"""
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
初回ログイン後は、プロフィール設定からパスワードの変更を推奨します。

※本メールはシステムによる自動送信です。
"""

    html_body = f"""
<html>
<body style="font-family: 'Noto Sans JP', sans-serif; color: #333; line-height: 1.7;">
  <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
    <h2 style="color: #4F46E5; border-bottom: 2px solid #4F46E5; padding-bottom: 10px;">ユーザー登録完了</h2>
    <p>アンケートシステムのアカウントが登録されました。以下の情報で管理画面にログインしてください。</p>
    <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
      <tr><td style="padding: 8px; background: #F3F4F6; font-weight: bold; width: 38%;">管理画面URL</td>
          <td style="padding: 8px;"><a href="{admin_url}">{admin_url}</a></td></tr>
      <tr><td style="padding: 8px; background: #F3F4F6; font-weight: bold;">ユーザーID</td>
          <td style="padding: 8px;">{to_email}</td></tr>
      <tr><td style="padding: 8px; background: #F3F4F6; font-weight: bold;">ログインパスワード</td>
          <td style="padding: 8px;"><code>{plain_password}</code></td></tr>
      <tr><td style="padding: 8px; background: #F3F4F6; font-weight: bold;">所属</td>
          <td style="padding: 8px;">{company_name}</td></tr>
    </table>
    <p style="font-size: 13px; color: #6B7280;">初回ログイン後は、プロフィール設定からパスワードの変更を推奨します。</p>
    <hr style="border: 0; border-top: 1px solid #EEE; margin: 24px 0;">
    <p style="font-size: 12px; color: #9CA3AF; text-align: center;">※本メールはシステムによる自動送信です。</p>
  </div>
</body>
</html>
"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = email_from
        msg["To"] = to_email
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        server = smtplib.SMTP(smtp_server, int(smtp_port))
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(email_from, to_email, msg.as_string())
        server.quit()
        return True, f"{to_email} へ登録完了メールを送信しました。"
    except Exception as e:
        return False, f"メール送信に失敗しました: {e}"

def init_db():
    conn = get_db_connection()
    c = conn.cursor()

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

    c.execute('''
        CREATE TABLE IF NOT EXISTS surveys (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            status TEXT,
            schema TEXT,
            created_by INTEGER,
            created_at DATETIME,
            FOREIGN KEY(created_by) REFERENCES users(id)
        )
    ''')
    c.execute("PRAGMA table_info(surveys)")
    survey_cols = {row[1] for row in c.fetchall()}
    if "created_by" not in survey_cols:
        c.execute("ALTER TABLE surveys ADD COLUMN created_by INTEGER")

    c.execute('''
        CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id TEXT,
            answers TEXT,
            submitted_at DATETIME
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS survey_editors (
            survey_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            granted_by INTEGER NOT NULL,
            created_at DATETIME,
            PRIMARY KEY (survey_id, user_id),
            FOREIGN KEY(survey_id) REFERENCES surveys(id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(granted_by) REFERENCES users(id)
        )
    ''')

    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "INSERT INTO users (username, password, company_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            ('admin', hash_password('admin123'), 'システム管理部', 'admin', now),
        )
        c.execute(
            "INSERT INTO users (username, password, company_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            ('creator1', hash_password('creator123'), '第一事業部', 'creator', now),
        )

    c.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
    admin_row = c.fetchone()
    if admin_row:
        c.execute("UPDATE surveys SET created_by = ? WHERE created_by IS NULL", (admin_row[0],))

    conn.commit()
    conn.close()

def user_can_access_survey(user_id, role, survey_id):
    """アンケートの編集・分析参照権限（作成者 or survey_editors）。"""
    if role == "admin":
        return True
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT created_by FROM surveys WHERE id = ?", (survey_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    if row[0] == user_id:
        conn.close()
        return True
    c.execute(
        "SELECT 1 FROM survey_editors WHERE survey_id = ? AND user_id = ?",
        (survey_id, user_id),
    )
    allowed = c.fetchone() is not None
    conn.close()
    return allowed

def user_can_edit_survey(user_id, role, survey_id):
    return user_can_access_survey(user_id, role, survey_id)

def user_owns_survey(user_id, survey_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT created_by FROM surveys WHERE id = ?", (survey_id,))
    row = c.fetchone()
    conn.close()
    return row and row[0] == user_id

def get_editable_surveys(user_id, role):
    conn = get_db_connection()
    c = conn.cursor()
    base = '''
        SELECT s.id, s.title, s.description, s.status, s.created_at, s.schema,
               (SELECT COUNT(*) FROM responses r WHERE r.survey_id = s.id) AS resp_count,
               s.created_by, u.username
        FROM surveys s
        JOIN users u ON s.created_by = u.id
    '''
    if role == "admin":
        c.execute(base + " ORDER BY s.created_at DESC")
    else:
        c.execute(
            base
            + """
            LEFT JOIN survey_editors se ON s.id = se.survey_id AND se.user_id = ?
            WHERE s.created_by = ? OR se.user_id = ?
            ORDER BY s.created_at DESC
            """,
            (user_id, user_id, user_id),
        )
    rows = c.fetchall()
    conn.close()
    return rows

def toggle_status(survey_id, current_status):
    new_status = '公開' if current_status == '非公開' else '非公開'
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE surveys SET status = ? WHERE id = ?", (new_status, survey_id))
    conn.commit()
    conn.close()

def delete_survey(survey_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM survey_editors WHERE survey_id = ?", (survey_id,))
    c.execute("DELETE FROM surveys WHERE id = ?", (survey_id,))
    c.execute("DELETE FROM responses WHERE survey_id = ?", (survey_id,))
    conn.commit()
    conn.close()

def clear_responses(survey_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM responses WHERE survey_id = ?", (survey_id,))
    conn.commit()
    conn.close()

def delete_response(response_id, survey_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "DELETE FROM responses WHERE id = ? AND survey_id = ?",
        (response_id, survey_id),
    )
    conn.commit()
    conn.close()

def _response_preview_label(response_id, answers_json, submitted_at, max_len=40):
    try:
        answers = json.loads(answers_json)
        parts = []
        for k, v in list(answers.items())[:2]:
            val = ", ".join(v) if isinstance(v, list) else str(v)
            if len(val) > max_len:
                val = val[:max_len] + "…"
            parts.append(f"{k}: {val}")
        preview = " / ".join(parts) if parts else "(内容なし)"
    except (json.JSONDecodeError, TypeError):
        preview = "(解析不可)"
    ts = (submitted_at or "")[:19]
    return f"#{response_id} [{ts}] {preview}"

def render_response_deletion_panel(survey_id, response_rows):
    """分析画面: 個別回答の削除UI。"""
    if not user_can_edit_survey(
        st.session_state.user_id, st.session_state.role, survey_id
    ):
        return

    st.subheader("🗑️ 回答の個別削除")
    st.caption("誤入力などで削除したい回答を1件ずつ選んで削除できます。削除後は分析結果の再実行が必要です。")

    options = {
        _response_preview_label(rid, ans, ts): rid
        for rid, ans, ts in response_rows
    }
    selected_label = st.selectbox(
        "削除する回答",
        list(options.keys()),
        key=f"del_resp_sel_{survey_id}",
    )
    confirm = st.checkbox(
        "この回答を削除することを承諾します",
        key=f"del_resp_confirm_{survey_id}",
    )
    if st.button("選択した回答を削除", type="primary", key=f"del_resp_btn_{survey_id}"):
        if not confirm:
            st.error("削除を承諾するチェックボックスをオンにしてください。")
            return
        target_id = options[selected_label]
        delete_response(target_id, survey_id)
        if "analysis_cache" in st.session_state:
            del st.session_state["analysis_cache"]
        st.toast(f"回答 #{target_id} を削除しました。")
        st.rerun()

# --- セッションステート初期化 ---
def init_session_state():
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'user_id' not in st.session_state:
        st.session_state.user_id = None
    if 'username' not in st.session_state:
        st.session_state.username = None
    if 'company_name' not in st.session_state:
        st.session_state.company_name = None
    if 'role' not in st.session_state:
        st.session_state.role = None
    if 'staff_menu' not in st.session_state:
        st.session_state.staff_menu = 'surveys'
    if 'admin_view' not in st.session_state:
        st.session_state.admin_view = 'list' # 'list', 'edit', 'analyze'
    if 'edit_data' not in st.session_state:
        st.session_state.edit_data = None
    if 'schema_builder' not in st.session_state:
        st.session_state.schema_builder = []
    if 'target_survey_id' not in st.session_state:
        st.session_state.target_survey_id = None
    if 'show_link_for' not in st.session_state:
        st.session_state.show_link_for = None
    if 'show_perm_for' not in st.session_state:
        st.session_state.show_perm_for = None

def reset_editor():
    st.session_state.admin_view = 'list'
    st.session_state.edit_data = None
    st.session_state.schema_builder = []

# --- 集計グラフ ---
CHART_TYPE_OPTIONS = {
    "棒グラフ": "bar",
    "円グラフ": "pie",
    "横棒グラフ": "barh",
    "ドーナツグラフ": "donut",
}

def aggregate_choice_counts(parsed_responses, q_text):
    counts = {}
    for ans in parsed_responses:
        val = ans.get(q_text, "")
        if isinstance(val, list):
            for v in val:
                if v:
                    counts[v] = counts.get(v, 0) + 1
        elif val:
            counts[val] = counts.get(val, 0) + 1
    return counts

def create_aggregation_chart(counts, chart_type):
    labels = list(counts.keys())
    values = list(counts.values())
    fig, ax = plt.subplots(figsize=(6, 4))

    if chart_type == "bar":
        ax.bar(labels, values, color='skyblue')
        ax.set_ylabel('回答数')
        plt.setp(ax.get_xticklabels(), rotation=15, ha='right')
    elif chart_type == "barh":
        ax.barh(labels, values, color='skyblue')
        ax.set_xlabel('回答数')
    elif chart_type == "pie":
        ax.pie(values, labels=labels, autopct='%1.1f%%', startangle=90)
        ax.axis('equal')
    elif chart_type == "donut":
        ax.pie(
            values, labels=labels, autopct='%1.1f%%', startangle=90,
            wedgeprops={'width': 0.45}
        )
        ax.axis('equal')

    fig.tight_layout()
    return fig

def save_chart_image(fig):
    tmp_img = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    fig.savefig(tmp_img.name, bbox_inches='tight', facecolor='white', transparent=False)
    plt.close(fig)
    return tmp_img.name

# --- 認証・ユーザー管理 ---
def render_login_screen():
    st.title("🔐 管理者・作成者ログイン")
    email = st.text_input("ユーザーID（メールアドレス）")
    password = st.text_input("パスワード", type="password")
    if st.button("ログイン", type="primary"):
        login_id = normalize_email(email) if "@" in email else email.strip()
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "SELECT id, username, password, company_name, role FROM users WHERE username = ?",
            (login_id,),
        )
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
            st.error("メールアドレスまたはパスワードが正しくありません。")
    st.caption("※ログインアカウントは管理者が発行します。")

def render_profile_settings():
    st.title("🔑 プロフィール設定")
    with st.form("profile_settings_form"):
        st.text_input("ユーザーID（メールアドレス）", value=st.session_state.username, disabled=True)
        new_company = st.text_input("所属名・組織名", value=st.session_state.company_name)
        current_password_input = st.text_input("現在のパスワード（必須）", type="password")
        new_password = st.text_input("新しいパスワード（変更しない場合は空欄）", type="password")
        new_password_confirm = st.text_input("新しいパスワード（確認用）", type="password")
        submit = st.form_submit_button("設定を更新する", type="primary")
        if submit:
            if not current_password_input:
                st.error("現在のパスワードを入力してください。")
            else:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT password FROM users WHERE id = ?", (st.session_state.user_id,))
                user_record = c.fetchone()
                if not user_record or user_record[0] != hash_password(current_password_input):
                    st.error("現在のパスワードが正しくありません。")
                    conn.close()
                elif new_password and new_password != new_password_confirm:
                    st.error("新しいパスワードと確認用パスワードが一致しません。")
                    conn.close()
                else:
                    try:
                        if new_password:
                            c.execute(
                                "UPDATE users SET company_name = ?, password = ? WHERE id = ?",
                                (new_company, hash_password(new_password), st.session_state.user_id),
                            )
                        else:
                            c.execute(
                                "UPDATE users SET company_name = ? WHERE id = ?",
                                (new_company, st.session_state.user_id),
                            )
                        conn.commit()
                        st.session_state.company_name = new_company
                        st.success("アカウント設定を更新しました。")
                        st.rerun()
                    except Exception as e:
                        st.error(f"更新中にエラーが発生しました: {e}")
                    finally:
                        conn.close()

def render_user_management():
    st.title("👥 ユーザー登録・管理")
    st.write("アンケート作成者アカウントの登録、編集、削除を行います。ログインIDにはメールアドレスを使用します。")
    if is_smtp_configured():
        st.success(f"メール送信: 有効（管理画面URL: {get_admin_portal_url()}）")
    else:
        st.warning(
            "メール送信: 無効（/opt/exam/.env の SMTP_SERVER / SMTP_USER / SMTP_PASSWORD / EMAIL_FROM を確認してください）"
        )

    with st.form("register_user_form"):
        new_email = st.text_input("ユーザーID（メールアドレス）", placeholder="例: user@example.com")
        new_company = st.text_input("所属名・組織名", placeholder="例: 東京支社")
        new_password = st.text_input("ログインパスワード", type="password")
        send_mail_on_register = st.checkbox("登録完了メールを送信する", value=True)
        if st.form_submit_button("ユーザーを登録する"):
            new_email_norm = normalize_email(new_email)
            if not (new_email and new_company and new_password):
                st.error("すべての項目を入力してください。")
            elif not is_valid_email(new_email):
                st.error("ユーザーIDには有効なメールアドレスを入力してください。")
            else:
                conn = get_db_connection()
                c = conn.cursor()
                try:
                    c.execute(
                        "INSERT INTO users (username, password, company_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
                        (
                            new_email_norm,
                            hash_password(new_password),
                            new_company,
                            'creator',
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                    conn.commit()
                    st.success(f"ユーザー「{new_email_norm}」を登録しました。")
                    if send_mail_on_register:
                        ok, mail_msg = send_user_registration_email(
                            new_email_norm, new_password, new_company
                        )
                        if ok:
                            st.info(mail_msg)
                        else:
                            st.warning(mail_msg)
                except sqlite3.IntegrityError:
                    st.error("そのメールアドレスは既に登録されています。")
                finally:
                    conn.close()
    st.divider()
    st.subheader("登録済みユーザー一覧（自分以外）")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, username, company_name, role, created_at FROM users WHERE id != ? ORDER BY id DESC",
        (st.session_state.user_id,),
    )
    users = c.fetchall()
    conn.close()
    if not users:
        st.info("登録済みのユーザーはいません。")
        return
    st.dataframe(
        pd.DataFrame(users, columns=["DB ID", "メールアドレス", "所属", "権限", "登録日時"]),
        use_container_width=True,
    )

    st.subheader("📧 登録完了メールの再送信")
    st.caption(
        "メールに記載するパスワードを入力して再送信します。"
        "空欄の場合は新しいパスワードを自動生成し、DBのパスワードも更新します。"
    )
    mail_user_options = {f"{u[1]} ({u[2]})": u for u in users}
    mail_sel_label = st.selectbox("再送信先ユーザー", list(mail_user_options.keys()), key="resend_mail_user_sel")
    mail_sel = mail_user_options[mail_sel_label]
    mail_sel_id, mail_sel_email, mail_sel_company, _, _ = mail_sel
    resend_password = st.text_input(
        "メールに記載するログインパスワード（空欄で自動再発行）",
        type="password",
        key="resend_mail_password",
    )
    if st.button("📧 登録完了メールを再送信", key="resend_mail_btn"):
        password_for_mail = resend_password.strip()
        if not password_for_mail:
            password_for_mail = generate_random_password()
            st.info("新しいパスワードを自動生成し、アカウントに反映しました。")
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (hash_password(password_for_mail), mail_sel_id),
        )
        conn.commit()
        conn.close()
        ok, mail_msg = send_user_registration_email(
            mail_sel_email, password_for_mail, mail_sel_company
        )
        if ok:
            st.success(mail_msg)
        else:
            st.error(mail_msg)

    st.divider()
    st.subheader("ユーザー編集・削除")
    user_options = {f"{u[1]} ({u[2]} - {u[3]})": u for u in users}
    selected_label = st.selectbox("編集するユーザー", list(user_options.keys()))
    sel = user_options[selected_label]
    sel_id, sel_username, sel_company, sel_role, _ = sel
    with st.form("edit_user_form"):
        edit_company = st.text_input("所属名・組織名", value=sel_company)
        edit_role = st.selectbox(
            "アカウント権限",
            ["一般作成者 (creator)", "システム管理者 (admin)"],
            index=0 if sel_role == 'creator' else 1,
        )
        edit_password = st.text_input("新しいパスワード（変更する場合のみ）", type="password")
        if st.form_submit_button("変更を保存する", type="primary"):
            if not edit_company:
                st.error("所属名は空欄にできません。")
            else:
                role_val = 'creator' if edit_role.startswith("一般") else 'admin'
                conn = get_db_connection()
                c = conn.cursor()
                try:
                    if edit_password:
                        c.execute(
                            "UPDATE users SET company_name = ?, role = ?, password = ? WHERE id = ?",
                            (edit_company, role_val, hash_password(edit_password), sel_id),
                        )
                    else:
                        c.execute(
                            "UPDATE users SET company_name = ?, role = ? WHERE id = ?",
                            (edit_company, role_val, sel_id),
                        )
                    conn.commit()
                    st.success("ユーザー情報を更新しました。")
                    st.rerun()
                except Exception as e:
                    st.error(f"更新エラー: {e}")
                finally:
                    conn.close()
    with st.expander("⚠️ このユーザーを削除する"):
        confirm_delete = st.checkbox(f"「{sel_username}」の削除を承諾します")
        if st.button("ユーザーを削除する", type="primary"):
            if confirm_delete:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("DELETE FROM survey_editors WHERE user_id = ? OR granted_by = ?", (sel_id, sel_id))
                c.execute("DELETE FROM users WHERE id = ?", (sel_id,))
                conn.commit()
                conn.close()
                st.success(f"ユーザー「{sel_username}」を削除しました。")
                st.rerun()
            else:
                st.error("確認チェックボックスをオンにしてください。")

def render_survey_editor_permissions(survey_id):
    """アンケートオーナーのみ：他ユーザーへの編集・分析権限付与。"""
    if not user_owns_survey(st.session_state.user_id, survey_id):
        return
    st.markdown("**編集・分析権限の付与（他の作成者）**")
    st.caption("付与されたユーザーは、当該アンケートの編集・回答データの分析ができます。")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT se.user_id, u.username, u.company_name
        FROM survey_editors se
        JOIN users u ON se.user_id = u.id
        WHERE se.survey_id = ?
        ORDER BY u.username
    """, (survey_id,))
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
            if col_b.button("解除", key=f"revoke_{survey_id}_{uid}"):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute(
                    "DELETE FROM survey_editors WHERE survey_id = ? AND user_id = ?",
                    (survey_id, uid),
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
                key=f"grant_sel_{survey_id}",
            )
            if st.button("編集・分析権限を付与", key=f"grant_btn_{survey_id}"):
                target_uid = available[selected_label]
                conn = get_db_connection()
                c = conn.cursor()
                try:
                    c.execute(
                        "INSERT INTO survey_editors (survey_id, user_id, granted_by, created_at) VALUES (?, ?, ?, ?)",
                        (
                            survey_id,
                            target_uid,
                            st.session_state.user_id,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                    conn.commit()
                    st.success("編集・分析権限を付与しました。")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.warning("このユーザーには既に権限が付与されています。")
                finally:
                    conn.close()
        else:
            st.caption("付与可能なユーザーはいません。")

def render_staff_portal():
    role_label = "管理者" if st.session_state.role == "admin" else "作成者"
    st.sidebar.markdown(f"**ログイン中**: {st.session_state.username} ({role_label})")
    st.sidebar.markdown(f"**所属**: {st.session_state.company_name}")
    if st.session_state.role == "admin":
        menu_options = ["アンケート管理", "ユーザー登録・管理", "プロフィール設定"]
    else:
        menu_options = ["アンケート管理", "プロフィール設定"]
    st.session_state.staff_menu = st.sidebar.radio("メニュー", menu_options)
    if st.sidebar.button("ログアウト"):
        st.session_state.logged_in = False
        st.session_state.role = None
        st.session_state.user_id = None
        st.session_state.admin_view = 'list'
        reset_editor()
        st.rerun()
    if st.session_state.staff_menu == "ユーザー登録・管理":
        render_user_management()
    elif st.session_state.staff_menu == "プロフィール設定":
        render_profile_settings()
    elif st.session_state.admin_view == 'list':
        render_admin_list()
    elif st.session_state.admin_view == 'analyze':
        render_analysis_screen()
    else:
        render_admin_editor()

# --- 画面コンポーネント ---
def show_welcome_screen():
    st.title("アンケートシステム")
    image_path = "/opt/fback/assets/logo.png"
    if os.path.exists(image_path):
        st.image(image_path, use_container_width=True)
    else:
        st.info("📌 指定されたアンケートが見つかりません。\n\n正しい回答用URLを入力するか、管理者にお問い合わせください。")


# 📝 回答画面
def show_survey_screen(survey_id):
    # --- 送信完了後のサンクスページ表示 ---
    if st.session_state.get(f'submitted_{survey_id}', False):
        st.title("✨ 送信完了")
        st.success("回答ありがとうございました！")
        st.info("ご協力に感謝いたします。")
        
        st.divider()
        
        # 2つのボタンを横並びに美しく配置
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 回答画面をもう一度表示する", use_container_width=True):
                # 送信完了フラグを削除して画面を再描画（フォームを復活させる）
                del st.session_state[f'submitted_{survey_id}']
                st.rerun()
                
        with col2:
            # 社内ポータルへのリンクボタン（別タブで開きます）
            portal_url = "https://nakaboshi365.sharepoint.com/sites/portal/SitePages/Home.aspx"
            st.link_button("🏠 社内ポータルを表示する", url=portal_url, use_container_width=True)

        st.balloons() # お祝いの風船アニメーション
        return # ここで処理を終了し、下のフォームを描画しない

    # --- 通常の回答フォーム表示 ---
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT title, description, status, schema FROM surveys WHERE id = ?", (survey_id,))
    survey = c.fetchone()
    conn.close()

    if not survey:
        st.error("指定されたアンケートは存在しません。")
        return
    
    title, description, status, schema_str = survey
    if status == '非公開':
        st.warning("このアンケートは現在非公開、または回答受付を終了しています。")
        return

    st.title(title)
    if description:
        st.write(description)
    st.divider()
    
    schema = json.loads(schema_str)
    
    with st.form("response_form"):
        answers = {}
        for i, item in enumerate(schema):
            q_text = item["question"]
            q_type = item["type"]
            
            if q_type == "ラジオボタン":
                answers[q_text] = st.radio(q_text, item.get("options", []), key=f"q_{i}")
            elif q_type == "チェックボックス":
                opts = item.get("options", [])
                selected = []
                st.write(q_text)
                for opt in opts:
                    if st.checkbox(opt, key=f"cb_{i}_{opt}"):
                        selected.append(opt)
                answers[q_text] = selected
            elif q_type == "コンボボックス":
                answers[q_text] = st.selectbox(q_text, item.get("options", []), key=f"q_{i}")
            elif q_type == "テキスト":
                answers[q_text] = st.text_input(q_text, key=f"q_{i}")
            elif q_type == "テキストエリア":
                answers[q_text] = st.text_area(q_text, key=f"q_{i}")
            elif q_type == "日付":
                answers[q_text] = str(st.date_input(q_text, key=f"q_{i}"))
                
        submit = st.form_submit_button("回答を送信する")
        
        if submit:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT INTO responses (survey_id, answers, submitted_at) VALUES (?, ?, ?)",
                      (survey_id, json.dumps(answers), datetime.now()))
            conn.commit()
            conn.close()
            
            # 送信完了フラグをセッションに保存し、画面を再描画する
            st.session_state[f'submitted_{survey_id}'] = True
            st.rerun()

# ⚙️ 管理者画面：アンケート一覧
def render_admin_list():
    is_admin = st.session_state.role == "admin"
    st.title("📊 管理者ダッシュボード" if is_admin else "📊 アンケート管理")
    
    col1, col2 = st.columns([8, 2])
    with col1:
        st.write("作成済みのアンケート一覧と管理を行います。" if is_admin else "担当・作成したアンケートの管理を行います。")
    with col2:
        if st.button("➕ 新規作成", use_container_width=True):
            st.session_state.admin_view = 'edit'
            st.session_state.edit_data = {'id': f"SV-{uuid.uuid4().hex[:6].upper()}", 'title': '', 'description': '', 'is_new': True}
            st.session_state.schema_builder = []
            st.rerun()

    st.divider()

    surveys = get_editable_surveys(st.session_state.user_id, st.session_state.role)

    if not surveys:
        st.info("表示できるアンケートはまだありません。")
        return

    h_col1, h_col2, h_col3, h_col4, h_col5 = st.columns([1.2, 1.5, 3, 1, 5.8])
    h_col1.write("**ステータス**")
    h_col2.write("**作成日**")
    h_col3.write("**ID / タイトル**")
    h_col4.write("**回答数**")
    h_col5.write("**アクション**")
    st.divider()

    uid = st.session_state.user_id
    role = st.session_state.role

    for sv in surveys:
        sv_id, title, desc, status, created_at, schema_str, resp_count, created_by, owner_name = sv
        can_edit = user_can_edit_survey(uid, role, sv_id)
        is_owner = user_owns_survey(uid, sv_id)
        col1, col2, col3, col4, col5 = st.columns([1.2, 1.5, 3, 1, 5.8])
        
        with col1:
            btn_label = "🟢 公開中" if status == '公開' else "🔴 非公開"
            if can_edit and st.button(btn_label, key=f"tog_{sv_id}"):
                toggle_status(sv_id, status)
                st.rerun()
            elif not can_edit:
                st.caption(btn_label)
        with col2:
            st.caption(created_at[:16])
        with col3:
            st.write(f"**{sv_id}**")
            st.write(title)
            if is_admin and owner_name:
                st.caption(f"作成者: {owner_name}")
        with col4:
            st.write(f"**{resp_count}** 件")
        with col5:
            b_col1, b_col2, b_col3, b_col4, b_col5, b_col6, b_col7 = st.columns(7)
            
            if b_col1.button("🔗", key=f"lnk_{sv_id}", help="回答URL"):
                st.session_state.show_link_for = None if st.session_state.show_link_for == sv_id else sv_id
                st.session_state.show_perm_for = None
                st.rerun()
            if can_edit and b_col2.button("📊", key=f"ana_{sv_id}", help="分析"):
                st.session_state.admin_view = 'analyze'
                st.session_state.target_survey_id = sv_id
                st.rerun()
            if can_edit and b_col3.button("✏️", key=f"edt_{sv_id}", help="編集"):
                st.session_state.admin_view = 'edit'
                st.session_state.edit_data = {'id': sv_id, 'title': title, 'description': desc, 'is_new': False}
                st.session_state.schema_builder = json.loads(schema_str)
                st.rerun()
            if can_edit and b_col4.button("📄", key=f"cpy_{sv_id}", help="コピー"):
                st.session_state.admin_view = 'edit'
                st.session_state.edit_data = {'id': f"SV-{uuid.uuid4().hex[:6].upper()}", 'title': f"{title}のコピー", 'description': desc, 'is_new': True}
                st.session_state.schema_builder = json.loads(schema_str)
                st.rerun()
            if is_owner and b_col5.button("🔐", key=f"perm_{sv_id}", help="権限付与"):
                st.session_state.show_perm_for = None if st.session_state.show_perm_for == sv_id else sv_id
                st.session_state.show_link_for = None
                st.rerun()
            if can_edit and b_col6.button("🧹", key=f"clr_{sv_id}", help="回答クリア"):
                clear_responses(sv_id)
                st.toast(f"{sv_id}の回答データをクリアしました。")
                st.rerun()
            if can_edit and b_col7.button("🗑️", key=f"del_{sv_id}", help="削除"):
                delete_survey(sv_id)
                st.rerun()
                
        if st.session_state.show_link_for == sv_id:
            render_survey_url_box(sv_id)
        if st.session_state.show_perm_for == sv_id:
            render_survey_editor_permissions(sv_id)
            
        st.divider()

# ⚙️ 管理者画面：エディタ
def render_admin_editor():
    edit_info = st.session_state.edit_data
    is_new = edit_info.get('is_new', True)
    survey_id = edit_info['id']

    if not is_new and not user_can_edit_survey(
        st.session_state.user_id, st.session_state.role, survey_id
    ):
        st.error("このアンケートを編集する権限がありません。")
        if st.button("🔙 一覧に戻る"):
            reset_editor()
            st.rerun()
        return
    
    st.title("📝 アンケート編集エディタ" if not is_new else "✨ アンケート新規作成")
    
    st.subheader("1. 基本情報")
    col_id, col_title = st.columns([1, 2])
    new_id = col_id.text_input("アンケートID", value=edit_info['id'], disabled=not is_new)
    new_title = col_title.text_input("タイトル", value=edit_info['title'])
    new_desc = st.text_area("概要・目的", value=edit_info['description'])
    
    st.divider()
    
    st.subheader("2. 設問項目の設定")
    schema = st.session_state.schema_builder
    
    for i, q in enumerate(schema):
        with st.container():
            st.markdown(f"**設問 {i+1}**")
            c1, c2, c3 = st.columns([3, 5, 1])
            with c1:
                st.text_input("種類", value=q['type'], disabled=True, key=f"type_{i}")
            with c2:
                q['question'] = st.text_input("設問文", value=q['question'], key=f"qtext_{i}")
            with c3:
                st.write("") 
                if st.button("❌", key=f"del_q_{i}", help="この項目を削除"):
                    st.session_state.schema_builder.pop(i)
                    st.rerun()
            
            if q['type'] in ["ラジオボタン", "チェックボックス", "コンボボックス"]:
                opt_str = ", ".join(q.get('options', []))
                edited_opts = st.text_input("選択肢（カンマ ',' 区切りで入力）", value=opt_str, key=f"opts_{i}")
                q['options'] = [o.strip() for o in edited_opts.split(',') if o.strip()]
            
            st.markdown("---")

    st.write("**➕ 新しい項目を追加**")
    add_col1, add_col2 = st.columns([1, 2])
    new_q_type = add_col1.selectbox("追加する形式", ["ラジオボタン", "チェックボックス", "テキスト", "テキストエリア", "日付", "コンボボックス"])
    if add_col2.button("この形式で項目を末尾に追加"):
        st.session_state.schema_builder.append({"type": new_q_type, "question": "新しい設問", "options": []})
        st.rerun()
        
    st.divider()
    
    action_col1, action_col2, action_col3 = st.columns([2, 2, 6])
    
    if action_col1.button("💾 保存する", type="primary", use_container_width=True):
        if not new_title or not new_id:
            st.error("IDとタイトルは必須です。")
        elif len(schema) == 0:
            st.error("少なくとも1つの設問を設定してください。")
        else:
            conn = get_db_connection()
            c = conn.cursor()
            try:
                schema_json = json.dumps(schema, ensure_ascii=False)
                if is_new:
                    c.execute(
                        "INSERT INTO surveys (id, title, description, status, schema, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (new_id, new_title, new_desc, '非公開', schema_json, st.session_state.user_id, datetime.now()),
                    )
                else:
                    c.execute("UPDATE surveys SET title = ?, description = ?, schema = ? WHERE id = ?",
                              (new_title, new_desc, schema_json, new_id))
                conn.commit()
                st.success("保存しました！")
                reset_editor() 
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("エラー: そのアンケートIDは既に使用されています。")
            finally:
                conn.close()
                
    if action_col2.button("🔙 キャンセル", use_container_width=True):
        reset_editor() 
        st.rerun()

# 📈 分析画面のレンダリング
def render_analysis_screen():
    survey_id = st.session_state.target_survey_id

    if not user_can_access_survey(st.session_state.user_id, st.session_state.role, survey_id):
        st.error("このアンケートの分析を参照する権限がありません。")
        if st.button("🔙 一覧に戻る"):
            st.session_state.admin_view = 'list'
            st.rerun()
        return
    
    col1, col2 = st.columns([8, 2])
    with col1:
        st.title(f"📊 分析レポート: {survey_id}")
    with col2:
        if st.button("🔙 一覧に戻る", use_container_width=True):
            st.session_state.admin_view = 'list'
            if 'analysis_cache' in st.session_state:
                del st.session_state['analysis_cache']
            st.rerun()
            
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT schema FROM surveys WHERE id = ?", (survey_id,))
    survey_data = c.fetchone()
    c.execute(
        "SELECT id, answers, submitted_at FROM responses WHERE survey_id = ? ORDER BY submitted_at DESC",
        (survey_id,),
    )
    response_rows = c.fetchall()
    conn.close()

    if not survey_data or not response_rows:
        st.warning("このアンケートにはまだ回答データがありません。")
        return

    schema = json.loads(survey_data[0])
    parsed_responses = [json.loads(r[1]) for r in response_rows]
    
    st.write(f"**総回答数:** {len(parsed_responses)} 件")

    with st.expander("Rawデータ（回答一覧）を見る", expanded=False):
        list_rows = []
        for rid, ans, ts in response_rows:
            row = json.loads(ans)
            row["回答ID"] = rid
            row["送信日時"] = ts
            list_rows.append(row)
        df = pd.DataFrame(list_rows)
        front_cols = ["回答ID", "送信日時"]
        other_cols = [c for c in df.columns if c not in front_cols]
        df = df[[c for c in front_cols + other_cols if c in df.columns]]
        st.dataframe(df, use_container_width=True)
        
        st.write("---")
        enc_choice = st.radio(
            "ダウンロードする文字コード（WindowsのExcelで開く場合はShift-JISを推奨）:",
            ["Shift-JIS (cp932)", "UTF-8 (BOM無し)"],
            horizontal=True,
            key="csv_enc_radio"
        )
        
        enc = 'cp932' if 'Shift-JIS' in enc_choice else 'utf-8'
        
        # 👇【最重要修正】文字列を .encode(enc) でバイトデータに変換
        # これにより、ダウンロードボタンがUTF-8を強制上書きするのを防ぎます
        csv_bytes = df.to_csv(index=False, errors='replace').encode(enc)
        
        st.download_button(
            label="📥 RawデータをCSVでダウンロード",
            data=csv_bytes, # バイトデータを渡す
            file_name=f"raw_data_{survey_id}.csv",
            mime="text/csv",
            use_container_width=True
        )

    with st.expander("回答の個別削除", expanded=False):
        render_response_deletion_panel(survey_id, response_rows)

    st.divider()

    if 'analysis_cache' not in st.session_state:
        chart_type_label = st.selectbox(
            "選択式項目の集計グラフ形式",
            list(CHART_TYPE_OPTIONS.keys()),
            index=0,
            help="画面表示とPDFレポートの両方に反映されます。",
            key=f"chart_type_{survey_id}",
        )
        chart_type = CHART_TYPE_OPTIONS[chart_type_label]

        if st.button("🚀 データを集計・AI分析する", type="primary"):
            if not GEMINI_API_KEY:
                st.error("Gemini APIキーが設定されていません。.envファイルを確認してください。")
                return
                
            with st.spinner("AIが分析とPDFレポートを作成中です..."):
                try:
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    charts_temp_paths = [] 
                    text_analysis_results = []
                    
                    for item in schema:
                        q_text = item["question"]
                        if item["type"] in ["ラジオボタン", "チェックボックス", "コンボボックス"]:
                            counts = aggregate_choice_counts(parsed_responses, q_text)
                            if not counts:
                                continue
                            fig = create_aggregation_chart(counts, chart_type)
                            charts_temp_paths.append((q_text, save_chart_image(fig)))

                    for item in schema:
                        q_text = item["question"]
                        if item["type"] in ["テキスト", "テキストエリア"]:
                            texts = [ans.get(q_text, "") for ans in parsed_responses if ans.get(q_text, "")]
                            if not texts:
                                text_analysis_results.append((q_text, "テキスト回答はありませんでした。"))
                                continue
                                
                            prompt = f"以下のアンケート自由記述回答を分析し、箇条書きで簡潔にまとめてください。\n①多い意見の傾向\n②少ない意見\n③変わった意見・尖った意見\n\n回答一覧:\n{texts}"
                            response = model.generate_content(prompt)
                            text_analysis_results.append((q_text, response.text))

                    overall_prompt = f"以下のアンケート結果全体（JSON）を元に、このアンケートから得られたインサイトと今後の改善提案を含む「総評」を400文字程度で作成してください。\n{json.dumps(parsed_responses, ensure_ascii=False)}"
                    overall_response = model.generate_content(overall_prompt)
                    overall_text = overall_response.text
                    
                    pdf = FPDF()
                    font_path = "/opt/fback/assets/ipaexg.ttf"
                    if os.path.exists(font_path):
                        pdf.add_font('IPAexGothic', '', font_path, uni=True)
                        pdf.set_font('IPAexGothic', '', 12)
                        pdf.add_page()
                        pdf.set_font('IPAexGothic', '', 16)
                        pdf.cell(0, 10, txt=f"アンケート分析レポート: {survey_id}", ln=True, align='C')
                        pdf.set_font('IPAexGothic', '', 12)
                        pdf.ln(5)
                        pdf.cell(0, 8, txt=f"集計グラフ形式: {chart_type_label}", ln=True)
                        pdf.ln(5)
                        
                        for title, img_path in charts_temp_paths:
                            if pdf.get_y() > 180: pdf.add_page() 
                            pdf.cell(0, 10, txt=f"【集計】{title}", ln=True)
                            pdf.image(img_path, w=150)
                            pdf.ln(5)
                        
                        for q_text, analysis_text in text_analysis_results:
                            if pdf.get_y() > 220: pdf.add_page() 
                            pdf.cell(0, 10, txt=f"【自由記述分析】{q_text}", ln=True)
                            pdf.multi_cell(0, 8, txt=analysis_text)
                            pdf.ln(5)
                            
                        if pdf.get_y() > 220: pdf.add_page()
                        pdf.cell(0, 10, txt="【AIアンケート総評】", ln=True)
                        pdf.multi_cell(0, 8, txt=overall_text)
                    
                    pdf_bytes = bytes(pdf.output())

                    st.session_state['analysis_cache'] = {
                        'charts': charts_temp_paths, 'texts': text_analysis_results,
                        'overall': overall_text, 'pdf': pdf_bytes,
                        'chart_type': chart_type_label,
                    }
                    st.rerun()
                    
                except Exception as e:
                    import traceback
                    st.error(f"エラー: {e}")
                    st.code(traceback.format_exc())

    else:
        cache = st.session_state['analysis_cache']
        st.subheader("1. 選択式項目の集計")
        st.caption(f"グラフ形式: {cache.get('chart_type', '棒グラフ')}")
        for q_text, img_path in cache['charts']:
            st.write(f"**{q_text}**")
            st.image(img_path)

        st.subheader("2. テキスト項目のAI傾向分析")
        for q_text, analysis_text in cache['texts']:
            st.write(f"**{q_text}**")
            st.info(analysis_text)

        st.subheader("3. AIによるアンケート総評")
        st.success(cache['overall'])
        
        st.divider()
        st.subheader("📄 分析結果のPDF出力")
        st.download_button(
            label="📥 レポートをPDFでダウンロード",
            data=cache['pdf'],
            file_name=f"report_{survey_id}.pdf",
            mime="application/pdf",
            type="primary"
        )
        
        if st.button("🔄 分析をやり直す"):
            del st.session_state['analysis_cache']
            st.rerun()
# --- メインルーチン ---
def main():
    st.set_page_config(page_title="汎用アンケートシステム", layout="wide")
    init_db()
    init_session_state()

    params = st.query_params

    if "admin" in params and params["admin"] == "true":
        if not st.session_state.logged_in:
            render_login_screen()
        else:
            render_staff_portal()
    elif "ID" in params:
        show_survey_screen(params["ID"])
    else:
        show_welcome_screen()

if __name__ == "__main__":
    main()