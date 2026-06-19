import os
import time
import uuid
import random
import datetime
import sqlite3
import shutil
import glob
import streamlit as st
from dotenv import load_dotenv

from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_classic.chains import create_history_aware_retriever, create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain

from brain_update import (
    DOCUMENTS_DIR,
    SUPPORTED_DOC_EXTS,
    classify_api_error,
    clear_file_metadata,
    load_api_keys_from_env,
    run_brain_update,
)
from access_log_store import clear_access_log, init_access_log, log_access

# ==========================================
# ⚙️ 初期設定・環境変数
# ==========================================
load_dotenv()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "nakaboshi_admin0123")
raw_keys = os.getenv("GOOGLE_API_KEYS", os.getenv("GOOGLE_API_KEY", ""))
API_KEYS_LIST = [k.strip() for k in raw_keys.split(",") if k.strip()]

st.set_page_config(page_title="社内FAQボット", page_icon="🤖", layout="wide")

# ==========================================
# 👮 グローバル状態管理
# ==========================================
@st.cache_resource
def get_global_state():
    return {
        "active_users": {},
        "api_keys": API_KEYS_LIST,
        "exhausted_keys": set(),
        "last_reset_date": datetime.date.today(),
        "is_updating": False
    }

global_state = get_global_state()

today = datetime.date.today()
if global_state["last_reset_date"] != today:
    global_state["exhausted_keys"].clear()
    global_state["last_reset_date"] = today

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

current_time = time.time()
global_state["active_users"][st.session_state.session_id] = current_time

for sid in list(global_state["active_users"].keys()):
    if current_time - global_state["active_users"][sid] > 600:
        del global_state["active_users"][sid]

if len(global_state["active_users"]) > 20:
    sorted_users = sorted(global_state["active_users"].items(), key=lambda x: x[1])
    allowed_users = [u[0] for u in sorted_users[:20]]
    if st.session_state.session_id not in allowed_users:
        del global_state["active_users"][st.session_state.session_id]
        st.warning("⚠️ 現在アクセスが集中しています。しばらく待ってから再度利用してください")
        st.stop()

def get_valid_api_key():
    keys = global_state["api_keys"]
    if not keys: return None

    available_keys = [k for k in keys if k not in global_state["exhausted_keys"]]
    if not available_keys:
        return None
    return random.choice(available_keys)

# ==========================================
# 🗄️ データベース（チャット履歴 ＆ 差分更新管理）
# ==========================================
def get_db_connection():
    conn = sqlite3.connect('chat_history.db', timeout=15)
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # チャット履歴テーブル
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  query TEXT, answer TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    try:
        c.execute("ALTER TABLE history ADD COLUMN show_top INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE history ADD COLUMN show_recent INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass

    # ファイル更新状態の管理テーブル（差分更新用）
    c.execute('''CREATE TABLE IF NOT EXISTS file_metadata
                 (filepath TEXT PRIMARY KEY, mtime REAL, size INTEGER)''')
                 
    conn.commit(); conn.close()
    init_access_log()

def save_to_db(query, answer):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO history (query, answer, show_top, show_recent) VALUES (?, ?, 1, 1)", (query, answer))
    conn.commit(); conn.close()

def get_top_5_frequent():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT query, answer, COUNT(*) as count FROM history WHERE show_top = 1 GROUP BY query ORDER BY count DESC LIMIT 5")
    results = c.fetchall()
    conn.close()
    return results

def get_recent_10_qa():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT query, answer FROM history WHERE show_recent = 1 ORDER BY timestamp DESC LIMIT 10")
    results = c.fetchall()
    conn.close()
    return results

def clear_top_qa():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE history SET show_top = 0")
    conn.commit(); conn.close()

def clear_recent_qa():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE history SET show_recent = 0")
    conn.commit(); conn.close()

def clear_db_history():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM history")
    c.execute("DELETE FROM sqlite_sequence WHERE name='history'")
    conn.commit(); conn.close()

def get_client_ip():
    try:
        headers = st.context.headers
        forwarded = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return headers.get("X-Real-IP") or headers.get("x-real-ip") or headers.get("Remote-Addr") or "unknown"
    except Exception:
        return "unknown"


def get_client_hostname(ip: str) -> str:
    if not ip or ip == "unknown":
        return ""
    try:
        import socket
        hostname, _, _ = socket.gethostbyaddr(ip)
        return hostname
    except Exception:
        return ""

def list_documents():
    if not os.path.exists(DOCUMENTS_DIR):
        os.makedirs(DOCUMENTS_DIR)
    docs = []
    for ext in SUPPORTED_DOC_EXTS:
        for f in glob.glob(os.path.join(DOCUMENTS_DIR, "**", f"*.{ext}"), recursive=True):
            if os.path.isfile(f):
                rel = os.path.relpath(f, DOCUMENTS_DIR).replace("\\", "/")
                docs.append(rel)
    return sorted(docs)

def delete_document(relative_path):
    rel = relative_path.replace("\\", "/")
    if rel.startswith("/") or ".." in rel.split("/"):
        return False
    path = os.path.abspath(os.path.join(DOCUMENTS_DIR, rel))
    docs_root = os.path.abspath(DOCUMENTS_DIR)
    if path != docs_root and not path.startswith(docs_root + os.sep):
        return False
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False

init_db()

# ==========================================
# 🧠 AIモデル・RAGチェーンの構築
# ==========================================
@st.cache_resource(show_spinner=False)
def get_vectorstore(api_key):
    embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001", google_api_key=api_key)
    return Chroma(persist_directory="./chroma_db", embedding_function=embeddings)

def create_rag_chain(api_key, has_history=True):
    vectorstore = get_vectorstore(api_key)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 10})
    llm = ChatGoogleGenerativeAI(model="gemini-flash-latest", temperature=0, google_api_key=api_key)

    system_prompt = (
        "あなたは社内資料に基づき回答するアシスタントです。提供された【コンテキスト】のみを使用して回答してください。\n"
        "回答の末尾に、参照したファイル名を「出典: [ファイル名]」の形式で必ず記載してください。\n"
        "答えが不明な場合は「該当する情報が見つかりませんでした」と答えてください。\n\n"
        "【コンテキスト】\n{context}"
    )
    qa_prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ])
    question_answer_chain = create_stuff_documents_chain(llm, qa_prompt)

    if has_history:
        contextualize_q_system_prompt = (
            "あなたはユーザーの質問を検索用に最適化するアシスタントです。\n"
            "過去の会話履歴と最新の質問を踏まえ、履歴がなくても意味が通じる「独立した1つの質問文」に書き換えてください。\n\n"
            "【厳守事項】\n"
            "・必ず「質問文のみ」を出力してください。\n"
            "・絶対に出力を空（カラ）にしないでください。"
        )
        contextualize_q_prompt = ChatPromptTemplate.from_messages([
            ("system", contextualize_q_system_prompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ])
        history_aware_retriever = create_history_aware_retriever(llm, retriever, contextualize_q_prompt)
        return create_retrieval_chain(history_aware_retriever, question_answer_chain)
    else:
        return create_retrieval_chain(retriever, question_answer_chain)

class _StreamlitBrainUI:
    def __init__(self):
        self._status = st.empty()
        self._progress = st.progress(0.0)

    def info(self, msg):
        prefix = {"変更されたファイルはありません": "🔄", "頭脳の差分更新": "✅", "ファイルの削除": "✅"}
        icon = next((v for k, v in prefix.items() if k in msg), "ℹ️")
        st.info(f"{icon} {msg}")

    def warning(self, msg):
        st.warning(msg)

    def error(self, msg):
        st.error(f"⚠️ {msg}" if not msg.startswith("⚠️") else msg)

    def status(self, msg):
        self._status.text(msg)

    def progress(self, value):
        self._progress.progress(value)


class _AppKeyManager:
    def __init__(self):
        self.api_keys = global_state["api_keys"]
        self.exhausted_keys = global_state["exhausted_keys"]

    def refresh_from_env(self):
        keys = load_api_keys_from_env()
        global_state["api_keys"] = keys
        self.api_keys = keys
        self.exhausted_keys &= set(keys)

    def get_key(self):
        return get_valid_api_key()

    def other_available_keys(self, api_key):
        return [
            k
            for k in self.api_keys
            if k not in self.exhausted_keys and k != api_key
        ]

    def mark_permission_denied(self, api_key):
        self.exhausted_keys.add(api_key)

    def mark_rate_limited(self, api_key) -> bool:
        if self.other_available_keys(api_key):
            self.exhausted_keys.add(api_key)
            return True
        return False

    def key_index(self, api_key):
        try:
            return self.api_keys.index(api_key) + 1
        except ValueError:
            return "?"


def reload_api_keys_from_env():
    keys = load_api_keys_from_env()
    global_state["api_keys"] = keys
    global_state["exhausted_keys"] &= set(keys)
    return keys


def update_brain():
    if global_state.get("is_updating", False):
        st.warning("⚠️ すでに他の管理者が更新処理を実行中です。")
        return

    reload_api_keys_from_env()
    # 旧ロジックで 429 時に枯渇扱いされたキーを解放（頭脳更新は待機リトライで対応）
    global_state["exhausted_keys"].clear()
    if not global_state["api_keys"]:
        st.error("⚠️ .env に GOOGLE_API_KEYS（または GOOGLE_API_KEY）が設定されていません。")
        return
    if not get_valid_api_key():
        st.error(
            "⚠️ 利用可能なAPIキーがありません。"
            "権限拒否(403)のキーは .env から差し替えてください。"
            "レート制限のみの場合は「頭脳を更新」を再度お試しください。"
        )
        return

    global_state["is_updating"] = True
    try:
        run_brain_update(
            key_manager=_AppKeyManager(),
            ui=_StreamlitBrainUI(),
            refresh_vectorstore_cache=lambda k: (get_vectorstore.clear(), get_vectorstore(k)),
        )
    finally:
        global_state["is_updating"] = False

# ==========================================
# ⚙️ サイドバー：管理者機能 & 履歴
# ==========================================
with st.sidebar:
    if st.button("✨ チャットをクリア", type="primary", use_container_width=True):
        st.session_state.chat_history = []
        st.rerun()

    with st.expander("🛠️ 管理者メニュー"):
        pw = st.text_input("管理者パスワード", type="password")
        if pw == ADMIN_PASSWORD:
            st.subheader("📁 ファイル管理")
            up = st.file_uploader("資料追加 (※サブフォルダ配置はサーバー直接操作)", type=list(SUPPORTED_DOC_EXTS))
            if up:
                with open(os.path.join("./documents", up.name), "wb") as f:
                    f.write(up.getbuffer())
                st.success(f"保存: {up.name}")

            docs = list_documents()
            filter_q = st.text_input("削除対象を絞り込み", placeholder="ファイル名・フォルダ名の一部を入力")
            if filter_q.strip():
                q = filter_q.strip().lower()
                filtered_docs = [d for d in docs if q in d.lower()]
            else:
                filtered_docs = docs
            st.caption(f"表示: {len(filtered_docs)} / {len(docs)} 件")
            sel = st.selectbox("削除選択", ["-- 選択 --"] + filtered_docs)
            if sel != "-- 選択 --" and st.button("🗑️ 削除実行"):
                if delete_document(sel): st.warning(f"削除: {sel}"); st.rerun()
            
            st.divider()
            if st.button("🧠 頭脳を更新(RAG)"):
                st.info("更新・差分チェック中...")
                update_brain()
                
            st.divider()
            st.subheader("👁️ 表示履歴の管理")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🧹 ランキングをリセット"):
                    clear_top_qa()
                    st.success("よく見られているQ&Aをリセットしました")
                    time.sleep(1)
                    st.rerun()
            with col2:
                if st.button("🧹 直近履歴をリセット"):
                    clear_recent_qa()
                    st.success("直近のQ&Aをリセットしました")
                    time.sleep(1)
                    st.rerun()
                    
            st.divider()
            if st.button("⚠️ DB全データ完全削除"):
                clear_db_history()
                clear_access_log()
                clear_file_metadata() # メタデータもクリア
                get_vectorstore.clear()
                st.success("データベースとファイル履歴を完全に初期化しました")
                time.sleep(1)
                st.rerun()

    st.divider()
    st.subheader("🔥 よく見られているQ&A")
    top_5 = get_top_5_frequent()
    if not top_5:
        st.caption("現在データがありません。")
    for q, a, count in top_5:
        with st.expander(f"Q: {q} ({count}回)"): st.write(a)
    
    st.divider()
    st.subheader("🆕 直近のQ&A (10件)")
    recent_10 = get_recent_10_qa()
    if not recent_10:
        st.caption("現在データがありません。")
    for q, a in recent_10:
        with st.expander(f"Q: {q[:30]}{'...' if len(q) > 30 else ''}"): 
            st.write(f"**質問:** {q}")
            st.write(f"**回答:** {a}")

# ==========================================
# 💬 メイン：UI構築とチャット
# ==========================================
banner_exts = ['banner.png', 'banner.jpg', 'banner.jpeg']
banner_path = next((ext for ext in banner_exts if os.path.exists(ext)), None)
if banner_path:
    st.image(banner_path, use_container_width=True)
else:
    st.title("🤖 中星工業 Q&A Webボット")

if os.path.exists("notice.txt"):
    with open("notice.txt", "r", encoding="utf-8") as f:
        notice_text = f.read().strip()
    if notice_text:
        st.info(f"📢 **お知らせ**\n\n{notice_text}")

st.markdown("""
<style>
@keyframes blink {
    0% { opacity: 1; }
    50% { opacity: 0.4; }
    100% { opacity: 1; }
}
@keyframes hideNormal {
    0%, 99% { opacity: 1; height: auto; overflow: visible; }
    100% { opacity: 0; height: 0; overflow: hidden; margin: 0; padding: 0; display: none; }
}
@keyframes showDelayed {
    0%, 99% { opacity: 0; height: 0; overflow: hidden; }
    100% { opacity: 1; height: auto; overflow: visible; }
}
@keyframes alternateText {
    0%, 45% { opacity: 1; }
    50%, 95% { opacity: 0; }
    100% { opacity: 1; }
}

.blink-text { animation: blink 1.5s ease-in-out infinite; font-weight: bold; }
.reference-text { color: #2e7d32; font-size: 0.9em; }
.warning-text { color: #d32f2f; font-size: 0.9em; }

.normal-msg { animation: hideNormal 15s forwards; }
.delayed-msg { animation: showDelayed 15s forwards; position: relative; height: 40px; }
.delayed-text-1 { animation: alternateText 8s infinite; position: absolute; width: 100%; }
.delayed-text-2 { animation: alternateText 8s infinite; animation-delay: 4s; position: absolute; width: 100%; opacity: 0; }
</style>
""", unsafe_allow_html=True)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

for message in st.session_state.chat_history:
    with st.chat_message(message.type): st.markdown(message.content)

if global_state.get("is_updating", False):
    st.warning("⚠️ 現在データベースの更新中のため、しばらく待った後に改めてご利用ください")
    st.chat_input("データベース更新中のため入力できません...", disabled=True)

elif prompt := st.chat_input("質問を入力してください"):
    _ip = get_client_ip()
    log_access(prompt, _ip, get_client_hostname(_ip))
    with st.chat_message("user"): st.markdown(prompt)

    with st.chat_message("assistant"):
        status_placeholder = st.empty()
        message_placeholder = st.empty()
        
        status_placeholder.markdown('<div class="blink-text" style="color:#4a4a4a;">⏳ 関連する社内資料を検索中...</div>', unsafe_allow_html=True)
        
        max_attempts = len(global_state["api_keys"]) if global_state["api_keys"] else 0
        attempt = 0
        success = False
        full_answer = ""
        source_files = set()
        
        has_history = len(st.session_state.chat_history) > 0
        first_chunk_received = False
        
        while attempt < max_attempts and not success:
            current_api_key = get_valid_api_key()
            if not current_api_key:
                break
            
            try:
                rag_chain = create_rag_chain(current_api_key, has_history=has_history)
                
                for chunk in rag_chain.stream({"input": prompt, "chat_history": st.session_state.chat_history}):
                    
                    if "context" in chunk:
                        for doc in chunk["context"]:
                            source_files.add(os.path.basename(doc.metadata.get('source', '不明')))
                        
                        if source_files and not first_chunk_received:
                            files_str = ", ".join(source_files)
                            html_status = f"""
                            <div class="status-container">
                                <div class="normal-msg blink-text reference-text">
                                    📄 参照中: {files_str}<br>⏳ 回答を生成しています...
                                </div>
                                <div class="delayed-msg blink-text">
                                    <div class="delayed-text-1 warning-text">⏳ 現在回答の生成に時間がかかっています...</div>
                                    <div class="delayed-text-2 reference-text">📄 引き続き参照中: {files_str}</div>
                                </div>
                            </div>
                            """
                            status_placeholder.markdown(html_status, unsafe_allow_html=True)
                    
                    if "answer" in chunk:
                        if not first_chunk_received:
                            first_chunk_received = True
                            status_placeholder.empty()
                            full_answer = "" 
                        
                        full_answer += chunk["answer"]
                        message_placeholder.markdown(full_answer + "▌")
                
                success = True
                
            except Exception as e:
                error_msg = str(e)
                print(f"[{time.strftime('%X')}] ⚠️ エラー発生 (Attempt {attempt}): {error_msg[:150]}")
                
                full_answer = "" 
                first_chunk_received = False
                source_files.clear()
                
                status_placeholder.markdown('<div class="blink-text warning-text">⏳ サーバー混雑のため別ルートで再接続中...</div>', unsafe_allow_html=True)
                message_placeholder.empty()
                
                err_kind = classify_api_error(error_msg)
                try:
                    key_idx = global_state["api_keys"].index(current_api_key) + 1
                except ValueError:
                    key_idx = "?"
                print(f"[{time.strftime('%X')}]    エラー全文(キー#{key_idx}, {err_kind}): {error_msg[:500]}", flush=True)

                if err_kind == "permission_denied":
                    global_state["exhausted_keys"].add(current_api_key)
                    print(f"[{time.strftime('%X')}] ⚠️ APIキー #{key_idx} 権限拒否(403) -> 別キーに切替えます", flush=True)
                    attempt += 1
                elif err_kind == "rate_limit":
                    others = [
                        k
                        for k in global_state["api_keys"]
                        if k not in global_state["exhausted_keys"] and k != current_api_key
                    ]
                    if others:
                        global_state["exhausted_keys"].add(current_api_key)
                        print(f"[{time.strftime('%X')}] ⚠️ APIキー #{key_idx} レート制限(429) -> 別キーに切替えます", flush=True)
                        attempt += 1
                    else:
                        status_placeholder.markdown(
                            '<div class="blink-text warning-text">⏳ APIレート制限: 15秒待機して再試行中...</div>',
                            unsafe_allow_html=True,
                        )
                        time.sleep(15)
                elif "500" in error_msg or "INTERNAL" in error_msg:
                    time.sleep(1)
                    attempt += 1
                else:
                    status_placeholder.empty()
                    message_placeholder.error(f"⚠️ 予期せぬシステムエラーが発生しました: {e}")
                    st.stop()
        
        if success:
            if source_files:
                full_answer += f"\n\n---\n**出典:** {', '.join(source_files)}"
            message_placeholder.markdown(full_answer)
            
            from langchain_core.messages import HumanMessage, AIMessage
            st.session_state.chat_history.extend([HumanMessage(content=prompt), AIMessage(content=full_answer)])
            save_to_db(prompt, full_answer)
        else:
            status_placeholder.empty()
            message_placeholder.error("⚠️ 通信エラー、または本日の利用上限に達しました。少し待ってから質問の言い回しを変えてお試しください。")
            st.stop()