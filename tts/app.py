"""音声合成ツール — Google Gemini Text-to-Speech API（Web UI）"""
from __future__ import annotations

import base64
import datetime
import socket
import threading
import time

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import db
import tts_engine as tts
import ui_common

JST = datetime.timezone(datetime.timedelta(hours=9))
st.set_page_config(
    page_title="音声合成ツール",
    page_icon="🔊",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_CSS = """
<style>
.block-container { padding-top: 1.5rem; }
div[data-testid="stDownloadButton"] button { width: 100%; }
header[data-testid="stHeader"] {
    background: transparent;
    height: 2.875rem;
    min-height: 2.875rem;
}
header[data-testid="stHeader"] [data-testid="stToolbar"],
header[data-testid="stHeader"] [data-testid="stDecoration"] {
    display: none;
}
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stSidebarCollapsedControl"],
button[kind="header"],
button[kind="headerNoPadding"] {
    visibility: visible !important;
}
</style>
"""

SIDEBAR_OPEN_JS = """
<script>
(function () {
    const doc = window.parent.document;
    const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
    if (sidebar && sidebar.getAttribute('aria-expanded') === 'true') {
        return;
    }
    const selectors = [
        '[data-testid="stSidebarCollapsedControl"] button',
        'button[kind="headerNoPadding"]',
        '[data-testid="collapsedControl"] button',
        'section[data-testid="stSidebar"] button[kind="header"]',
    ];
    for (const sel of selectors) {
        const btn = doc.querySelector(sel);
        if (btn) {
            btn.click();
            return;
        }
    }
})();
</script>
"""


def get_client_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "unknown"


def check_session_timeout() -> None:
    if not st.session_state.get("authenticated"):
        return
    employee_id = st.session_state.get("user", {}).get("employee_id", "")
    timeout_sec = db.get_effective_session_timeout_sec(employee_id)
    if timeout_sec <= 0:
        st.session_state.last_activity = time.time()
        return
    now = time.time()
    last = st.session_state.get("last_activity", now)
    if now - last > timeout_sec:
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        label = db.format_session_timeout_label(timeout_sec)
        st.warning(
            f"{label}間操作がなかったため、セキュリティのためログアウトしました。"
        )
        st.rerun()
    st.session_state.last_activity = now


def show_login() -> None:
    ui_common.render_login_page_style()
    ui_common.render_login_banner()
    st.markdown("## 🔊 音声合成ツール")
    st.caption(
        "社員番号とパスワードでログインしてください（NAI と同一アカウント）。"
        "TTS の利用許可は管理者が個別に設定します。"
    )
    with st.form("login_form"):
        emp_id = st.text_input("社員番号 / ID", placeholder="例: 12345 または admin")
        password = st.text_input("パスワード", type="password")
        submitted = st.form_submit_button("ログイン", use_container_width=True)
        if submitted:
            user = db.authenticate_user(emp_id, password)
            if user and not db.user_can_access_tts(user):
                st.error(
                    "TTS（音声合成ツール）の利用が許可されていません。"
                    "管理者にお問い合わせください。"
                )
            elif user:
                st.session_state.user = user
                st.session_state.authenticated = True
                st.session_state.last_activity = time.time()
                st.rerun()
            else:
                st.error("社員番号またはパスワードが正しくありません。")


_SYNTH_JOBS: dict[str, dict] = {}
_SYNTH_LOCK = threading.Lock()


def _is_synth_running() -> bool:
    job_id = st.session_state.get("active_synth_job_id")
    if not job_id:
        return False
    with _SYNTH_LOCK:
        job = _SYNTH_JOBS.get(job_id)
    return bool(job and job.get("status") == "running")


def _cancel_synth_job(job_id: str | None = None) -> None:
    job_id = job_id or st.session_state.get("active_synth_job_id")
    if not job_id:
        return
    with _SYNTH_LOCK:
        job = _SYNTH_JOBS.get(job_id)
        if job:
            job["cancel_requested"] = True


def _start_synth_job(
    user: dict,
    *,
    text: str,
    model: str,
    voice: str,
    style_prompt: str,
    char_count: int,
) -> None:
    job_id = f"{user['employee_id']}-{time.time_ns()}"
    job = {
        "id": job_id,
        "status": "running",
        "cancel_requested": False,
        "result": None,
        "error": None,
        "started_at": time.time(),
        "params": {
            "user": user,
            "text": text,
            "model": model,
            "voice": voice,
            "style_prompt": style_prompt,
            "char_count": char_count,
        },
    }
    with _SYNTH_LOCK:
        _SYNTH_JOBS[job_id] = job
    st.session_state["active_synth_job_id"] = job_id

    def worker() -> None:
        try:
            def cancel_check() -> bool:
                with _SYNTH_LOCK:
                    current = _SYNTH_JOBS.get(job_id)
                    return bool(current and current["cancel_requested"])

            result = tts.synthesize_speech(
                text,
                model=model,
                voice=voice,
                style_prompt=style_prompt,
                cancel_check=cancel_check,
            )
            with _SYNTH_LOCK:
                current = _SYNTH_JOBS.get(job_id)
                if not current:
                    return
                if current["cancel_requested"]:
                    current["status"] = "cancelled"
                else:
                    current["result"] = result
                    current["status"] = "done"
        except tts.SynthesisCancelled:
            with _SYNTH_LOCK:
                current = _SYNTH_JOBS.get(job_id)
                if current:
                    current["status"] = "cancelled"
        except Exception as exc:
            with _SYNTH_LOCK:
                current = _SYNTH_JOBS.get(job_id)
                if not current:
                    return
                if current["cancel_requested"]:
                    current["status"] = "cancelled"
                else:
                    current["error"] = str(exc)
                    current["status"] = "error"

    threading.Thread(target=worker, daemon=True).start()


def _apply_synth_job_result(job: dict) -> None:
    params = job["params"]
    user = params["user"]
    elapsed = int((time.time() - job["started_at"]) * 1000)
    common_log = dict(
        employee_id=user["employee_id"],
        username=user.get("username", ""),
        department=user.get("department", ""),
        model=params["model"],
        voice=params["voice"],
        char_count=params["char_count"],
        text=params["text"],
        style_prompt=params["style_prompt"],
        elapsed_ms=elapsed,
        client_ip=get_client_ip(),
    )

    if job["status"] == "done" and job["result"]:
        result = job["result"]
        db.log_usage(**common_log, status="success")
        st.session_state["last_audio"] = result.wav_bytes
        st.session_state["last_meta"] = {
            "model": result.model,
            "voice": result.voice,
            "chars": params["char_count"],
        }
        st.success("音声の合成が完了しました。")
    elif job["status"] == "cancelled":
        db.log_usage(
            **common_log,
            status="cancelled",
            error_message="ユーザーによりキャンセル",
        )
        st.info("音声合成をキャンセルしました。")
    elif job["status"] == "error":
        db.log_usage(
            **common_log,
            status="error",
            error_message=job.get("error") or "不明なエラー",
        )
        st.session_state.pop("last_audio", None)
        st.session_state.pop("last_meta", None)
        st.error(f"合成エラー: {job.get('error')}")


@st.fragment(run_every=datetime.timedelta(seconds=1))
def _synth_progress_panel() -> None:
    job_id = st.session_state.get("active_synth_job_id")
    if not job_id:
        return

    with _SYNTH_LOCK:
        job = _SYNTH_JOBS.get(job_id)

    if not job:
        st.session_state.pop("active_synth_job_id", None)
        return

    if job["status"] == "running":
        label = (
            "キャンセルしています…（処理の完了を待っています）"
            if job["cancel_requested"]
            else "音声を合成しています…"
        )
        with st.status(label, state="running", expanded=True):
            if not job["cancel_requested"]:
                st.button(
                    "⏹️ 合成をキャンセル",
                    key="btn_cancel_synth",
                    on_click=_cancel_synth_job,
                )
        return

    with _SYNTH_LOCK:
        finished = _SYNTH_JOBS.pop(job_id, None)
    st.session_state.pop("active_synth_job_id", None)
    if finished:
        _apply_synth_job_result(finished)
        st.rerun()


def _clear_synth_form() -> None:
    job_id = st.session_state.get("active_synth_job_id")
    if job_id:
        _cancel_synth_job(job_id)
        with _SYNTH_LOCK:
            _SYNTH_JOBS.pop(job_id, None)
    st.session_state.pop("synth_text", None)
    st.session_state.pop("last_audio", None)
    st.session_state.pop("last_meta", None)
    st.session_state.pop("active_synth_job_id", None)


def _request_open_sidebar() -> None:
    st.session_state["_open_sidebar"] = True


def render_result_audio(wav_bytes: bytes, volume: float = 0.5) -> None:
    """生成結果の再生プレーヤー（初期音量を指定可能）"""
    b64 = base64.b64encode(wav_bytes).decode()
    vol = max(0.0, min(1.0, volume))
    components.html(
        f"""
        <audio controls style="width:100%;" id="tts-result-audio">
            <source src="data:audio/wav;base64,{b64}" type="audio/wav">
        </audio>
        <script>
            const audio = document.getElementById('tts-result-audio');
            if (audio) {{
                audio.volume = {vol};
                audio.addEventListener('loadedmetadata', () => {{ audio.volume = {vol}; }});
            }}
        </script>
        """,
        height=54,
    )


def render_usage_logs(user: dict) -> None:
    is_admin = bool(user.get("is_admin"))
    st.markdown(
        '<div style="font-size:0.78rem;color:#555;padding:8px 0 2px;">📋 利用ログ</div>',
        unsafe_allow_html=True,
    )
    if is_admin:
        filter_id = st.text_input(
            "社員番号で絞り込み（管理者）",
            key="log_filter_emp",
            placeholder="空欄ですべて表示",
        )
        logs = db.get_all_logs(limit=50, employee_id=filter_id)
    else:
        logs = db.get_user_logs(user["employee_id"], limit=20)

    if not logs:
        st.caption("利用ログはまだありません。")
        return

    rows = []
    for row in logs:
        rows.append({
            "日時": row["logged_at"],
            "社員番号": row["employee_id"],
            "氏名": row["username"],
            "モデル": tts.TTS_MODELS.get(row["model"], row["model"]),
            "音声": tts.VOICE_OPTIONS.get(row["voice"], row["voice"]),
            "文字数": row["char_count"],
            "結果": "成功" if row["status"] == "success" else "失敗",
            "処理時間(ms)": row["elapsed_ms"],
            "テキスト": row["text_preview"],
        })
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        height=min(320, 38 + len(rows) * 35),
    )


def render_main() -> None:
    user = st.session_state.user
    st.markdown(APP_CSS, unsafe_allow_html=True)

    title_col, settings_col = st.columns([6, 1])
    with title_col:
        st.title("🔊 音声合成ツール")
        st.caption(
            f"ログイン中: {user.get('username', '')}（{user['employee_id']}） / "
            "Google Text-to-Speech API（Gemini TTS）"
        )
    with settings_col:
        st.button(
            "⚙️ 設定",
            use_container_width=True,
            help="サイドバーを開く",
            on_click=_request_open_sidebar,
            key="open_sidebar_btn",
        )
    if st.session_state.pop("_open_sidebar", False):
        components.html(SIDEBAR_OPEN_JS, height=0)

    with st.sidebar:
        st.header("設定")
        model_id = st.selectbox(
            "音声合成モデル",
            options=list(tts.TTS_MODELS.keys()),
            format_func=lambda k: tts.TTS_MODELS[k],
            index=0,
        )
        voice_name = st.selectbox(
            "音声（ボイス）",
            options=list(tts.VOICE_OPTIONS.keys()),
            format_func=lambda k: tts.VOICE_OPTIONS[k],
            index=list(tts.VOICE_OPTIONS.keys()).index("Kore"),
        )
        style_prompt = st.text_area(
            "読み上げスタイル（任意）",
            placeholder="例: 落ち着いたトーンで、はっきりと読み上げてください。",
            height=100,
        )
        fmt_options = list(tts.available_download_formats().keys())
        default_download_fmt = "mp3" if "mp3" in fmt_options else "wav"
        download_fmt = st.selectbox(
            "ダウンロード形式",
            options=fmt_options,
            format_func=lambda k: tts.available_download_formats()[k],
            index=fmt_options.index(default_download_fmt),
            key="download_format",
        )
        if not tts.ffmpeg_available():
            st.caption("MP3 / M4A / OGG を使うにはサーバーに ffmpeg のインストールが必要です。")
        st.divider()
        render_usage_logs(user)
        st.divider()
        if st.button("ログアウト", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

    if "synth_text" not in st.session_state:
        st.session_state.synth_text = ""

    col_main, col_side = st.columns([3, 1])

    with col_main:
        input_text = st.text_area(
            "合成テキスト",
            height=260,
            placeholder="ここに読み上げたいテキストを入力してください。",
            key="synth_text",
        )
        char_count = len(input_text or "")
        btn_clear, btn_txt, btn_generate, btn_spacer = st.columns([1, 1, 1, 1])
        with btn_clear:
            st.button(
                "🗑️ クリア・新規作成",
                use_container_width=True,
                on_click=_clear_synth_form,
            )
        with btn_txt:
            txt_ts = datetime.datetime.now(JST).strftime("%Y%m%d_%H%M%S")
            st.download_button(
                label="⬇️ テキストを TXT 保存",
                data=(input_text or "").encode("utf-8"),
                file_name=f"tts_text_{txt_ts}.txt",
                mime="text/plain; charset=utf-8",
                use_container_width=True,
                disabled=not (input_text or "").strip(),
            )
        with btn_generate:
            generate = st.button(
                "🎙️ 音声を合成",
                type="primary",
                use_container_width=True,
                disabled=_is_synth_running(),
            )
        cap_clear, cap_txt, cap_gen, cap_spacer = st.columns([1, 1, 1, 1])
        with cap_clear:
            st.caption("入力欄と生成音声をリセット")
        with cap_txt:
            st.caption("入力内容を .txt でダウンロード")
        st.caption(f"文字数: {char_count:,}")

    with col_side:
        st.markdown("##### 使い方")
        st.markdown(
            "1. テキストを入力\n"
            "2. モデルと音声を選択\n"
            "3. 「音声を合成」をクリック\n"
            "   （合成中は「合成をキャンセル」で中断可能）\n"
            "4. ダウンロード形式を選択（WAV / MP3 / M4A / OGG）\n"
            "5. プレビュー再生またはファイルをダウンロード\n\n"
            "※ 一度に合成するテキスト文字数は **1,000文字以内** を推奨します。"
        )

    if generate:
        if not (input_text or "").strip():
            st.warning("テキストを入力してください。")
        elif _is_synth_running():
            st.warning("音声合成が実行中です。完了するかキャンセルしてください。")
        else:
            _start_synth_job(
                user,
                text=input_text,
                model=model_id,
                voice=voice_name,
                style_prompt=style_prompt,
                char_count=char_count,
            )

    _synth_progress_panel()

    if st.session_state.get("last_audio"):
        meta = st.session_state.get("last_meta", {})
        st.divider()
        st.subheader("生成結果")
        render_result_audio(st.session_state["last_audio"], volume=0.5)

        ts = datetime.datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        voice = meta.get("voice", "voice")
        sel_fmt = st.session_state.get(
            "download_format",
            "mp3" if tts.ffmpeg_available() else "wav",
        )
        try:
            dl_data, dl_mime, dl_ext = tts.convert_audio(
                st.session_state["last_audio"], sel_fmt,
            )
            fmt_label = tts.DOWNLOAD_FORMATS[sel_fmt]["label"]
        except Exception as exc:
            st.warning(f"選択形式への変換に失敗しました。WAV でダウンロードします。（{exc}）")
            dl_data = st.session_state["last_audio"]
            dl_mime = "audio/wav"
            dl_ext = "wav"
            fmt_label = tts.DOWNLOAD_FORMATS["wav"]["label"]
        filename = f"tts_{voice}_{ts}.{dl_ext}"
        st.download_button(
            label=f"⬇️ {fmt_label} をダウンロード",
            data=dl_data,
            file_name=filename,
            mime=dl_mime,
            use_container_width=True,
        )
        st.caption(
            f"モデル: {tts.TTS_MODELS.get(meta.get('model', ''), meta.get('model', ''))} / "
            f"音声: {tts.VOICE_OPTIONS.get(meta.get('voice', ''), meta.get('voice', ''))} / "
            f"文字数: {meta.get('chars', 0):,}"
        )


def main() -> None:
    db.init_db()
    if not st.session_state.get("authenticated"):
        show_login()
        return
    check_session_timeout()
    fresh = db.get_user(st.session_state.user["employee_id"])
    if not fresh or not fresh.get("is_active"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.error("アカウントが無効になっています。管理者にお問い合わせください。")
        st.stop()
    if not db.user_can_access_tts(fresh):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.error(
            "TTS（音声合成ツール）の利用が許可されていません。"
            "管理者にお問い合わせください。"
        )
        st.stop()
    st.session_state.user = fresh
    render_main()


main()
