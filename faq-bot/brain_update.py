"""頭脳（RAG）の差分更新 — Streamlit / cron 共通"""
import os
import glob
import time
import random
import sqlite3
from dotenv import load_dotenv
from langchain_community.document_loaders import UnstructuredFileLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma

DOCUMENTS_DIR = "./documents"
CHROMA_DIR = "./chroma_db"
SUPPORTED_DOC_EXTS = ("txt", "pdf", "docx", "csv", "xlsx", "pptx", "md")
DB_PATH = "chat_history.db"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_file_metadata_table():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS file_metadata
           (filepath TEXT PRIMARY KEY, mtime REAL, size INTEGER)"""
    )
    conn.commit()
    conn.close()


def get_tracked_files():
    init_file_metadata_table()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT filepath, mtime, size FROM file_metadata")
    rows = c.fetchall()
    conn.close()
    return {row[0]: {"mtime": row[1], "size": row[2]} for row in rows}


def update_tracked_file(filepath, mtime, size):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO file_metadata (filepath, mtime, size) VALUES (?, ?, ?)",
        (filepath, mtime, size),
    )
    conn.commit()
    conn.close()


def remove_tracked_file(filepath):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM file_metadata WHERE filepath = ?", (filepath,))
    conn.commit()
    conn.close()


def clear_file_metadata():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM file_metadata")
    conn.commit()
    conn.close()


# 429（レート制限）時: 切替可能な別キーがなければ待機して同じキーで再試行
RATE_LIMIT_MAX_RETRIES = 12
RATE_LIMIT_BASE_WAIT_SEC = 15
RATE_LIMIT_MAX_WAIT_SEC = 60


def classify_api_error(error_msg: str) -> str:
    """rate_limit | permission_denied | other"""
    if "PERMISSION_DENIED" in error_msg or " 403 " in f" {error_msg} ":
        return "permission_denied"
    if (
        "RESOURCE_EXHAUSTED" in error_msg
        or "rate limit" in error_msg.lower()
        or " 429 " in f" {error_msg} "
        or "quota" in error_msg.lower()
    ):
        return "rate_limit"
    return "other"


def load_api_keys_from_env():
    load_dotenv(override=True)
    raw = os.getenv("GOOGLE_API_KEYS", os.getenv("GOOGLE_API_KEY", ""))
    return [k.strip() for k in raw.split(",") if k.strip()]


class ApiKeyManager:
    def __init__(self, api_keys, exhausted_keys=None):
        self.api_keys = list(api_keys)
        self.exhausted_keys = exhausted_keys if exhausted_keys is not None else set()

    @classmethod
    def from_env(cls):
        return cls(load_api_keys_from_env())

    def refresh_from_env(self):
        """.env 変更を反映し、存在しないキーは exhausted から除外"""
        self.api_keys = load_api_keys_from_env()
        self.exhausted_keys &= set(self.api_keys)

    def get_key(self):
        available = [k for k in self.api_keys if k not in self.exhausted_keys]
        if not available:
            return None
        return random.choice(available)

    def other_available_keys(self, api_key):
        return [
            k
            for k in self.api_keys
            if k not in self.exhausted_keys and k != api_key
        ]

    def mark_permission_denied(self, api_key):
        """403: キー自体が使えない → セッション中は使用不可"""
        self.exhausted_keys.add(api_key)

    def mark_rate_limited(self, api_key) -> bool:
        """
        429: 別キーがあれば切替用に exhausted へ。なければ exhausted にしない（待機リトライ用）。
        戻り値: True = 別キーへ切替した
        """
        if self.other_available_keys(api_key):
            self.exhausted_keys.add(api_key)
            return True
        return False

    def key_index(self, api_key):
        try:
            return self.api_keys.index(api_key) + 1
        except ValueError:
            return "?"


class LogUI:
    def info(self, msg):
        print(f"[{time.strftime('%X')}] {msg}", flush=True)

    def warning(self, msg):
        print(f"[{time.strftime('%X')}] WARNING: {msg}", flush=True)

    def error(self, msg):
        print(f"[{time.strftime('%X')}] ERROR: {msg}", flush=True)

    def status(self, msg):
        print(f"[{time.strftime('%X')}] {msg}", flush=True)

    def progress(self, _value):
        pass


def _scan_current_files():
    current_files = {}
    for ext in SUPPORTED_DOC_EXTS:
        for f in glob.glob(os.path.join(DOCUMENTS_DIR, "**", f"*.{ext}"), recursive=True):
            abs_path = os.path.abspath(f)
            current_files[abs_path] = {
                "mtime": os.path.getmtime(abs_path),
                "size": os.path.getsize(abs_path),
                "ext": ext,
            }
    return current_files


def _diff_files(current_files, tracked_files):
    files_to_process = []
    files_to_delete = []

    for path, stats in current_files.items():
        if path not in tracked_files:
            files_to_process.append(path)
        elif (
            tracked_files[path]["mtime"] != stats["mtime"]
            or tracked_files[path]["size"] != stats["size"]
        ):
            files_to_delete.append(path)
            files_to_process.append(path)

    for path in tracked_files.keys():
        if path not in current_files:
            files_to_delete.append(path)

    return files_to_process, files_to_delete


def run_brain_update(key_manager=None, ui=None, refresh_vectorstore_cache=None):
    """
    差分更新を実行する。戻り値: {"ok": bool, "message": str}
    refresh_vectorstore_cache: 省略可。callable(api_key) で Streamlit キャッシュ破棄等。
    """
    ui = ui or LogUI()
    key_manager = key_manager or ApiKeyManager.from_env()
    if hasattr(key_manager, "refresh_from_env"):
        key_manager.refresh_from_env()

    api_key = key_manager.get_key()
    if not api_key:
        msg = "APIキーが未設定、または使用上限に達しています。"
        ui.error(msg)
        return {"ok": False, "message": msg}

    try:
        if not os.path.exists(CHROMA_DIR):
            clear_file_metadata()

        current_files = _scan_current_files()
        tracked_files = get_tracked_files()
        files_to_process, files_to_delete = _diff_files(current_files, tracked_files)

        if not files_to_process and not files_to_delete:
            msg = "変更されたファイルはありません。頭脳は最新の状態です。"
            ui.info(msg)
            return {"ok": True, "message": msg, "unchanged": True}

        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001", google_api_key=api_key
        )
        vectorstore = Chroma(persist_directory=CHROMA_DIR, embedding_function=embeddings)

        if files_to_delete:
            ui.status("古いファイル・更新されたファイルの過去データを削除中...")
            for path in files_to_delete:
                try:
                    results = vectorstore.get(where={"source": path})
                    if results and results["ids"]:
                        vectorstore.delete(ids=results["ids"])
                except Exception as e:
                    print(
                        f"[{time.strftime('%X')}] Delete Error ({os.path.basename(path)}): {e}",
                        flush=True,
                    )
                remove_tracked_file(path)

        if not files_to_process:
            if refresh_vectorstore_cache:
                refresh_vectorstore_cache(api_key)
            msg = "ファイルの削除をAIに反映しました。"
            ui.info(msg)
            return {"ok": True, "message": msg, "deleted_only": True}

        documents = []
        total_files = len(files_to_process)

        for idx, file_path in enumerate(files_to_process):
            ext = current_files[file_path]["ext"]
            filename = os.path.basename(file_path)
            normalized_path = file_path.replace("\\", "/").lower()
            ui.status(f"読み込み中 ({idx + 1}/{total_files}): {filename}")

            try:
                if "catalog" in normalized_path:
                    loader = UnstructuredFileLoader(
                        file_path,
                        strategy="hi_res",
                        extract_image_block_types=["Image"],
                    )
                elif ext in ("docx", "xlsx", "pptx"):
                    loader = UnstructuredFileLoader(
                        file_path,
                        strategy="hi_res",
                        extract_image_block_types=["Image"],
                    )
                else:
                    loader = UnstructuredFileLoader(file_path, strategy="fast")
                documents.extend(loader.load())
            except Exception as e:
                ui.error(f"{filename} の読み込みをスキップしました: {e}")
                print(f"[{time.strftime('%X')}] Load Error ({filename}): {e}", flush=True)

            ui.progress((idx + 1) / total_files * 0.5)

        if not documents:
            msg = "有効なテキストデータが抽出できませんでした。"
            ui.error(msg)
            return {"ok": False, "message": msg}

        ui.status("データをチャンクに分割中...")
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        chunks = text_splitter.split_documents(documents)

        if not chunks:
            msg = "分割できるテキストがありませんでした。"
            ui.warning(msg)
            return {"ok": False, "message": msg}

        ui.status("差分データをAIに学習させています...")
        total_chunks = len(chunks)
        batch_size = 50

        for i in range(0, total_chunks, batch_size):
            batch = chunks[i : i + batch_size]
            success = False
            rate_limit_retries = 0
            last_err_kind = "rate_limit"

            while not success:
                try:
                    vectorstore.add_documents(batch)
                    success = True
                    rate_limit_retries = 0
                except Exception as e:
                    error_msg = str(e)
                    last_err_kind = classify_api_error(error_msg)
                    err_kind = last_err_kind
                    key_idx = key_manager.key_index(api_key)
                    print(
                        f"[{time.strftime('%X')}] add_documents失敗 (キー#{key_idx}, {err_kind}): {error_msg[:500]}",
                        flush=True,
                    )

                    if err_kind == "permission_denied":
                        key_manager.mark_permission_denied(api_key)
                        new_key = key_manager.get_key()
                        if not new_key:
                            break
                        api_key = new_key
                        ui.status(
                            f"🔄 権限エラー: APIキー #{key_manager.key_index(api_key)} に切り替えてリトライ中..."
                        )
                        embeddings = GoogleGenerativeAIEmbeddings(
                            model="models/gemini-embedding-001", google_api_key=api_key
                        )
                        vectorstore = Chroma(
                            persist_directory=CHROMA_DIR, embedding_function=embeddings
                        )
                    elif err_kind == "rate_limit":
                        switched = key_manager.mark_rate_limited(api_key)
                        if switched:
                            new_key = key_manager.get_key()
                            if not new_key:
                                break
                            api_key = new_key
                            rate_limit_retries = 0
                            ui.status(
                                f"🔄 レート制限: APIキー #{key_manager.key_index(api_key)} に切り替えてリトライ中..."
                            )
                            embeddings = GoogleGenerativeAIEmbeddings(
                                model="models/gemini-embedding-001", google_api_key=api_key
                            )
                            vectorstore = Chroma(
                                persist_directory=CHROMA_DIR, embedding_function=embeddings
                            )
                        else:
                            rate_limit_retries += 1
                            if rate_limit_retries >= RATE_LIMIT_MAX_RETRIES:
                                break
                            wait_sec = min(
                                RATE_LIMIT_BASE_WAIT_SEC * rate_limit_retries,
                                RATE_LIMIT_MAX_WAIT_SEC,
                            )
                            ui.status(
                                f"⏳ APIレート制限: {wait_sec}秒待機して再試行 "
                                f"({rate_limit_retries}/{RATE_LIMIT_MAX_RETRIES})..."
                            )
                            time.sleep(wait_sec)
                    else:
                        raise

            if not success:
                available = [
                    k for k in key_manager.api_keys if k not in key_manager.exhausted_keys
                ]
                if not key_manager.api_keys:
                    msg = "APIキーが .env に設定されていません。"
                elif last_err_kind == "rate_limit" and available:
                    msg = (
                        "APIレート制限により学習が中断されました。"
                        "しばらく待ってから再度「頭脳を更新」を実行してください。"
                    )
                elif not available:
                    msg = (
                        "全てのAPIキーが使用不可（権限拒否または上限）のため、学習が中断されました。"
                        ".env のキー設定を確認してください。"
                    )
                else:
                    msg = (
                        "APIレート制限の待機回数を超えたため、学習が中断されました。"
                        "しばらく待ってから再度実行してください。"
                    )
                ui.error(msg)
                return {"ok": False, "message": msg}

            ui.progress(0.5 + min((i + batch_size) / total_chunks, 1.0) * 0.5)
            if i + batch_size < total_chunks:
                time.sleep(0.5)

        for path in files_to_process:
            update_tracked_file(
                path, current_files[path]["mtime"], current_files[path]["size"]
            )

        if refresh_vectorstore_cache:
            refresh_vectorstore_cache(api_key)

        msg = "頭脳の差分更新が完了しました。"
        ui.info(msg)
        return {"ok": True, "message": msg}

    except Exception as e:
        msg = f"更新中にエラーが発生しました: {e}"
        ui.error(msg)
        return {"ok": False, "message": msg}
