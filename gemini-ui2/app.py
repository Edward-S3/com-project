"""
app.py — 社内 AI アシスタント (Open WebUI 風)
ログイン認証 / チャット / ファイル添付 / テンプレート / Web検索 / フィードバック
"""
import base64
import html
import io
import os
import re
import time
import uuid
import socket
import datetime
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

import db
import llm_providers as llm
import office_files as office
import ui_common

# ══════════════════════════════════════════════════════════
# 初期設定
# ══════════════════════════════════════════════════════════
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "nakaboshi_admin0123")
APP_DIR        = os.path.dirname(os.path.abspath(__file__))
UPLOAD_TMP_DIR = os.path.join(APP_DIR, "tmp", "uploads")
JST            = datetime.timezone(datetime.timedelta(hours=9))
DEFAULT_TEMPLATE_ID = 1
MAX_PENDING_FILES = 20

st.set_page_config(
    page_title="社内 AI アシスタント",
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════
# ユーティリティ
# ══════════════════════════════════════════════════════════

def get_client_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "unknown"


def new_sid() -> str:
    return str(uuid.uuid4())


def auto_title(text: str) -> str:
    text = text.strip().replace("\n", " ")
    return text[:40] + ("…" if len(text) > 40 else "")


def _ensure_upload_tmp_dir() -> None:
    os.makedirs(UPLOAD_TMP_DIR, exist_ok=True)


def _cleanup_stale_uploads(max_age_sec: int = 3600) -> None:
    """古い一時添付ファイルを削除（異常終了時の残骸対策）"""
    if not os.path.isdir(UPLOAD_TMP_DIR):
        return
    cutoff = time.time() - max_age_sec
    for name in os.listdir(UPLOAD_TMP_DIR):
        path = os.path.join(UPLOAD_TMP_DIR, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.unlink(path)
        except OSError:
            pass


def _clear_pending_xlsx() -> None:
    st.session_state.pending_xlsx = {}


def _pending_xlsx_map() -> dict:
    raw = st.session_state.get("pending_xlsx")
    if not raw:
        return {}
    if isinstance(raw, dict) and "sheets" in raw:
        return {}
    return raw if isinstance(raw, dict) else {}


def _sync_xlsx_files() -> None:
    """編集済み Excel を各一時ファイルへ反映"""
    xlsx_map = _pending_xlsx_map()
    if not xlsx_map:
        return
    for pf in _pending_files_list():
        fid = pf.get("id")
        xlsx = xlsx_map.get(fid) if fid else None
        if not xlsx or not xlsx.get("sheets") or not pf.get("path"):
            continue
        data = office.workbook_bytes_from_sheets(xlsx["sheets"])
        with open(pf["path"], "wb") as f:
            f.write(data)
        pf["size"] = len(data)


def _load_xlsx_attachment(file_id: str, name: str, data: bytes) -> None:
    xlsx_map = _pending_xlsx_map()
    xlsx_map[file_id] = {
        "name": name,
        "sheets": office.load_xlsx_sheets(data),
        "include_aggregation": True,
        "group_agg": None,
        "agg_instructions": [],
    }
    st.session_state.pending_xlsx = xlsx_map


def _pending_files_list() -> list[dict]:
    files = st.session_state.get("pending_files")
    if isinstance(files, list):
        return files
    legacy = st.session_state.get("pending_file")
    if legacy:
        if not legacy.get("id"):
            legacy["id"] = str(uuid.uuid4())
        return [legacy]
    return []


def _set_pending_files(files: list[dict]) -> None:
    st.session_state.pending_files = files
    st.session_state.pop("pending_file", None)


def _pending_files_total_size(files: list[dict] | None = None) -> int:
    files = files if files is not None else _pending_files_list()
    return sum(int(f.get("size") or 0) for f in files)


def _dispose_uploaded_files(files: list[dict] | None) -> None:
    for file_info in files or []:
        _dispose_uploaded_file(file_info)


def _clear_pending_files() -> None:
    _dispose_uploaded_files(_pending_files_list())
    st.session_state.pending_files = []
    st.session_state.pop("pending_file", None)
    _clear_pending_xlsx()


def _store_uploaded_file(name: str, data: bytes, mime: str) -> dict:
    """アップロードを一時ファイルへ保存（セッションにはパスのみ保持）"""
    _ensure_upload_tmp_dir()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "bin"
    path = os.path.join(UPLOAD_TMP_DIR, f"{uuid.uuid4()}.{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "path": path,
        "mime": mime,
        "size": len(data),
    }


def _attachments_from_file_infos(file_infos: list[dict]) -> list[llm.FileAttachment]:
    attachments: list[llm.FileAttachment] = []
    for file_info in file_infos:
        att = _attachment_from_file_info(file_info)
        if att:
            attachments.append(att)
    return attachments


def _prepare_message_attachments(
    file_infos: list[dict], user_text_api: str,
) -> tuple[list[llm.FileAttachment], str]:
    """API 送信用に添付を分割（編集済み Excel はテキスト化）"""
    attachments: list[llm.FileAttachment] = []
    text = user_text_api
    xlsx_map = _pending_xlsx_map()
    for pf in file_infos:
        fid = pf.get("id")
        xlsx = xlsx_map.get(fid) if fid else None
        if (
            office.is_xlsx(pf.get("name", ""), pf.get("mime", ""))
            and xlsx
            and xlsx.get("sheets")
        ):
            data = office.workbook_bytes_from_sheets(xlsx["sheets"])
            if pf.get("path"):
                with open(pf["path"], "wb") as fp:
                    fp.write(data)
                pf["size"] = len(data)
            text += (
                f"\n\n--- 編集済み Excel ({pf['name']}) ---\n"
                + office.sheets_to_text(xlsx["sheets"])
            )
            if xlsx.get("include_aggregation", True):
                active = xlsx.get("active_sheet")
                text += "\n\n" + office.sheets_aggregation_text(
                    xlsx["sheets"],
                    active_sheet=active,
                    agg_instructions=xlsx.get("agg_instructions"),
                    group_agg=xlsx.get("group_agg"),
                )
        else:
            att = _attachment_from_file_info(pf)
            if att:
                attachments.append(att)
    return attachments, text


def _pending_files_have_audio() -> bool:
    return llm.attachments_have_audio(_attachments_from_file_infos(_pending_files_list()))


def _merge_uploaded_files(
    uploaded_list: list,
    allowed_types: list[str],
    max_bytes: int | None,
    max_mb: int,
    max_files: int = MAX_PENDING_FILES,
) -> list[str]:
    """file_uploader から選ばれたファイルを pending_files に追加"""
    errors: list[str] = []
    pending = list(_pending_files_list())
    if max_files == 1 and pending and uploaded_list:
        _dispose_uploaded_files(pending)
        _clear_pending_xlsx()
        pending = []
    existing = {(f["name"], f.get("size")) for f in pending}
    changed = False

    for uploaded in uploaded_list:
        key = (uploaded.name, uploaded.size)
        if key in existing:
            continue
        if len(pending) >= max_files:
            errors.append(f"添付は最大 {max_files} 件までです。")
            break
        ext = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
        if ext and ext not in allowed_types:
            errors.append(f"「{uploaded.name}」の形式（.{ext}）は許可されていません。")
            continue
        new_total = _pending_files_total_size(pending) + uploaded.size
        if max_bytes and new_total > max_bytes:
            errors.append(
                f"合計サイズが上限（{max_mb} MB）を超えるため"
                f"「{uploaded.name}」を追加できません。"
            )
            continue
        file_bytes = uploaded.read()
        mime_type = uploaded.type or "application/octet-stream"
        pf = _store_uploaded_file(uploaded.name, file_bytes, mime_type)
        pending.append(pf)
        existing.add(key)
        changed = True
        if office.is_xlsx(uploaded.name, mime_type):
            _load_xlsx_attachment(pf["id"], uploaded.name, file_bytes)

    if changed:
        _set_pending_files(pending)
    return errors


def _remove_pending_file(file_id: str) -> None:
    pending = _pending_files_list()
    target = next((f for f in pending if f.get("id") == file_id), None)
    if target:
        _dispose_uploaded_file(target)
    pending = [f for f in pending if f.get("id") != file_id]
    xlsx_map = _pending_xlsx_map()
    xlsx_map.pop(file_id, None)
    st.session_state.pending_xlsx = xlsx_map
    _set_pending_files(pending)
    st.session_state.file_uploader_key = st.session_state.get("file_uploader_key", 0) + 1


def _migrate_pending_state() -> None:
    if st.session_state.get("pending_file") and not st.session_state.get("pending_files"):
        pf = st.session_state.pending_file
        if not pf.get("id"):
            pf["id"] = str(uuid.uuid4())
        st.session_state.pending_files = [pf]
        st.session_state.pop("pending_file", None)
    raw_xlsx = st.session_state.get("pending_xlsx")
    if isinstance(raw_xlsx, dict) and "sheets" in raw_xlsx:
        pf = (_pending_files_list() or [None])[0]
        if pf and pf.get("id"):
            st.session_state.pending_xlsx = {
                pf["id"]: {
                    "name": raw_xlsx.get("name") or pf.get("name", "book.xlsx"),
                    "sheets": raw_xlsx["sheets"],
                },
            }
        else:
            _clear_pending_xlsx()


def _normalize_messages(messages: list[dict]) -> list[dict]:
    """DB 読み込み時に Office 出力ファイルを復元"""
    normalized: list[dict] = []
    for msg in messages:
        row = dict(msg)
        text, outputs = office.split_content_and_outputs(row.get("content", ""))
        row["content"] = text
        if outputs:
            row["office_outputs"] = outputs
        normalized.append(row)
    return normalized


def _dispose_uploaded_file(file_info: dict | None) -> None:
    """処理完了後にサーバー上の一時ファイルを削除"""
    if not file_info:
        return
    path = file_info.get("path")
    if path and os.path.isfile(path):
        try:
            os.unlink(path)
        except OSError:
            pass
    for key in ("path", "size", "name", "mime", "bytes"):
        file_info.pop(key, None)


def _attachment_from_file_info(file_info: dict | None) -> llm.FileAttachment | None:
    if not file_info:
        return None
    if file_info.get("path"):
        return llm.FileAttachment(
            name=file_info["name"],
            mime=file_info["mime"],
            path=file_info["path"],
        )
    if file_info.get("bytes") is not None:
        return llm.FileAttachment(
            name=file_info["name"],
            mime=file_info["mime"],
            data=file_info["bytes"],
        )
    return None


db.init_db()
db.ensure_admin_user(ADMIN_PASSWORD)
_cleanup_stale_uploads()


def load_settings() -> dict:
    s = db.get_all_settings()
    return {
        "model":      s.get("model", "gemini-3.5-flash"),
        "temp":       float(s.get("temperature", "0.7")),
        "max_tokens": int(s.get("max_output_tokens", "8192")),
        "sys_prompt": s.get("system_prompt", ""),
    }


def _initial_selected_model(emp: str, user_models: dict[str, str]) -> str:
    """ユーザー管理の実効デフォルト LLM を初期選択にする（自動選択は使わない）"""
    default = db.get_effective_default_model(emp)
    if default in user_models:
        return default
    keys = list(user_models.keys())
    return keys[0] if keys else default


def _default_active_template(emp: str) -> dict | None:
    return db.get_default_template_for_user(emp, DEFAULT_TEMPLATE_ID)


def _ensure_active_template_allowed(emp: str) -> None:
    """選択中テンプレートが許可モデルと合わない場合は利用可能な既定に切り替え"""
    usable = db.get_active_templates_for_user(emp)
    usable_ids = {t["id"] for t in usable}
    cur = st.session_state.get("active_template")
    if cur and cur.get("id") not in usable_ids:
        st.session_state.active_template = db.get_default_template_for_user(
            emp, DEFAULT_TEMPLATE_ID,
        )
        st.session_state.pop("tmpl_sel", None)
    elif cur is None and usable:
        st.session_state.active_template = db.get_default_template_for_user(
            emp, DEFAULT_TEMPLATE_ID,
        )


def _sync_model_selectbox(user_models: dict[str, str], selected: str) -> None:
    """モデル selectbox の表示を selected_model と同期"""
    if selected not in user_models:
        return
    labels = list(user_models.values())
    keys = list(user_models.keys())
    st.session_state["model_sel"] = labels[keys.index(selected)]


def _sync_template_selectbox(templates: list[dict], active: dict | None) -> None:
    """テンプレート selectbox の表示を active_template と同期"""
    if active:
        st.session_state["tmpl_sel"] = active.get("name", "")
    else:
        st.session_state["tmpl_sel"] = "(デフォルト)"


def get_user_models(employee_id: str) -> dict[str, str]:
    """ユーザーが使用できるモデルの辞書を返す（API キー設定済みプロバイダのみ）"""
    available = llm.get_available_models()
    allowed = db.get_user_allowed_models(employee_id)
    if allowed:
        available = {k: v for k, v in available.items() if k in allowed}
    return available


def check_session_timeout() -> None:
    """未操作でセッションを切断（グローバル / ユーザー別設定）"""
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
        _clear_pending_files()
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        label = db.format_session_timeout_label(timeout_sec)
        st.warning(
            f"{label}間操作がなかったため、セキュリティのためログアウトしました。"
        )
        st.rerun()
    st.session_state.last_activity = now


_EMBEDDED_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(data:(image/[^;]+);base64,([^)]+)\)",
    re.IGNORECASE,
)


def _text_for_clipboard(content: str) -> str:
    """クリップボード用テキスト（埋め込み画像の base64 は除外）"""
    text, _ = office.split_content_and_outputs(content or "")
    text = _EMBEDDED_IMAGE_RE.sub("[生成画像]", text)
    return text.strip()


def _extract_embedded_images(content: str) -> list[tuple[bytes, str]]:
    """Markdown 埋め込みから生成画像（bytes, mime）を抽出"""
    images: list[tuple[bytes, str]] = []
    for m in _EMBEDDED_IMAGE_RE.finditer(content or ""):
        try:
            images.append((base64.standard_b64decode(m.group(2)), m.group(1)))
        except Exception:
            pass
    return images


def _strip_embedded_images(content: str) -> str:
    """表示用テキスト（埋め込み画像 Markdown を除去）"""
    text = _EMBEDDED_IMAGE_RE.sub("", content or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _image_file_ext(mime: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }.get((mime or "").lower(), "png")


def _image_download_filename(index: int, mime: str) -> str:
    ts = datetime.datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    return f"generated_{ts}_{index + 1}.{_image_file_ext(mime)}"


def _render_generated_images(
    images: list[tuple[bytes, str]], key_prefix: str,
) -> None:
    """生成画像の表示 + ダウンロードボタン"""
    for j, (img_data, img_mime) in enumerate(images):
        st.image(img_data)
        st.download_button(
            "📥 画像を保存",
            data=img_data,
            file_name=_image_download_filename(j, img_mime),
            mime=img_mime,
            key=f"dl_img_{key_prefix}_{j}",
        )


def _render_office_outputs(outputs: list[dict], key_prefix: str) -> None:
    for j, out in enumerate(outputs):
        if out["name"].endswith(".docx"):
            label = "Word"
        elif out["name"].endswith(".pptx"):
            label = "PowerPoint"
        else:
            label = "ファイル"
        st.download_button(
            f"📥 {label}を保存",
            data=out["data"],
            file_name=out["name"],
            mime=out["mime"],
            key=f"dl_office_{key_prefix}_{j}",
        )


def _render_message_body(
    content: str, key_prefix: str, office_outputs: list[dict] | None = None,
) -> None:
    """メッセージ本文（テキスト + 生成画像 + Office 出力）を表示"""
    text, parsed_outputs = office.split_content_and_outputs(content)
    outputs = office_outputs or parsed_outputs
    images = _extract_embedded_images(text)
    text = _strip_embedded_images(text)
    if text:
        st.markdown(text)
    if images:
        _render_generated_images(images, key_prefix)
    if outputs:
        _render_office_outputs(outputs, key_prefix)


def _format_agg_instruction(inst: dict) -> str:
    sheet = inst.get("sheet", "")
    grp = inst.get("group_col") or "（全体）"
    val = inst.get("value_col") or "（行数）"
    func = office.AGG_LABELS.get(inst.get("func", ""), inst.get("func", ""))
    label = (inst.get("label") or "").strip()
    base = f"シート「{sheet}」/ グループ: {grp} / 項目: {val} / {func}"
    return f"{label} — {base}" if label else base


def _save_pending_xlsx(fid: str, xlsx: dict) -> None:
    st.session_state.pending_xlsx = {**_pending_xlsx_map(), fid: xlsx}


def _render_xlsx_aggregation(fid: str, xlsx: dict, active: str, df) -> None:
    """Excel シートの直接集計 UI（任意シート・任意項目の集計指示）"""
    sheets = xlsx.get("sheets") or {}
    sheet_names = list(sheets.keys())
    if xlsx.get("agg_instructions") is None:
        xlsx["agg_instructions"] = []

    with st.expander("📊 シート集計", expanded=False):
        tab_overview, tab_basic, tab_inst = st.tabs(
            ["ブック概要", "数値サマリー", "集計指示"],
        )

        with tab_overview:
            st.dataframe(
                office.workbook_overview(sheets),
                width="stretch",
                hide_index=True,
            )

        with tab_basic:
            basic_sheet = st.selectbox(
                "対象シート",
                sheet_names,
                index=sheet_names.index(active) if active in sheet_names else 0,
                key=f"xlsx_basic_sheet_{fid}",
            )
            basic_df = sheets[basic_sheet]
            st.caption(f"シート「{basic_sheet}」の数値列を集計しています。")
            summary_df = office.sheet_basic_summary(basic_df)
            st.dataframe(summary_df, width="stretch", hide_index=True)
            st.download_button(
                "📥 サマリーを CSV",
                data=summary_df.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"summary_{basic_sheet}.csv",
                mime="text/csv",
                key=f"dl_xlsx_summary_{fid}_{basic_sheet}",
            )

        with tab_inst:
            st.caption(
                "ブック内の任意のシート・項目に対して集計指示を登録できます。"
                " 複数登録して一括実行した結果は AI 質問にも渡せます。"
            )
            inst_sheet = st.selectbox(
                "対象シート",
                sheet_names,
                key=f"xlsx_inst_sheet_{fid}",
            )
            inst_df = sheets[inst_sheet]
            inst_cols = [str(c) for c in inst_df.columns]
            func_keys = list(office.AGG_LABELS.keys())
            func_labels = [office.AGG_LABELS[k] for k in func_keys]

            c1, c2, c3 = st.columns(3)
            value_sel = c1.selectbox(
                "集計対象項目",
                ["（行数のみ）"] + inst_cols,
                key=f"xlsx_inst_val_{fid}_{inst_sheet}",
            )
            group_sel = c2.selectbox(
                "グループ項目（任意）",
                ["（全体）"] + inst_cols,
                key=f"xlsx_inst_grp_{fid}_{inst_sheet}",
            )
            func_sel = c3.selectbox(
                "集計方法",
                func_labels,
                key=f"xlsx_inst_func_{fid}_{inst_sheet}",
            )
            inst_label = st.text_input(
                "メモ（任意）",
                key=f"xlsx_inst_label_{fid}",
                placeholder="例: 部署別売上合計",
            )

            if st.button("＋ 指示を追加", key=f"xlsx_inst_add_{fid}"):
                func = func_keys[func_labels.index(func_sel)]
                value_col = None if value_sel == "（行数のみ）" else value_sel
                group_col = None if group_sel == "（全体）" else group_sel
                err = None
                if func in ("sum", "mean", "min", "max") and not value_col:
                    err = "合計・平均・最小・最大には集計対象項目を指定してください。"
                elif func not in ("count",) and not value_col and not group_col:
                    err = "集計対象項目またはグループ項目を指定してください。"
                if err:
                    st.error(err)
                else:
                    instructions = list(xlsx.get("agg_instructions") or [])
                    instructions.append({
                        "id": str(uuid.uuid4()),
                        "sheet": inst_sheet,
                        "group_col": group_col,
                        "value_col": value_col,
                        "func": func,
                        "label": inst_label.strip(),
                    })
                    xlsx["agg_instructions"] = instructions
                    _save_pending_xlsx(fid, xlsx)
                    st.session_state.pop(f"xlsx_inst_results_{fid}", None)
                    st.success("集計指示を追加しました。")
                    st.rerun()

            instructions = list(xlsx.get("agg_instructions") or [])
            if instructions:
                st.markdown("**登録済みの集計指示**")
                for i, inst in enumerate(instructions):
                    row_a, row_b = st.columns([6, 1])
                    with row_a:
                        st.text(f"{i + 1}. {_format_agg_instruction(inst)}")
                    with row_b:
                        if st.button(
                            "🗑",
                            key=f"xlsx_inst_del_{fid}_{inst.get('id', i)}",
                            help="この指示を削除",
                        ):
                            instructions.pop(i)
                            xlsx["agg_instructions"] = instructions
                            _save_pending_xlsx(fid, xlsx)
                            st.session_state.pop(f"xlsx_inst_results_{fid}", None)
                            st.rerun()

                if st.button("▶ すべて実行", key=f"xlsx_inst_run_all_{fid}"):
                    results = office.execute_agg_instructions(sheets, instructions)
                    st.session_state[f"xlsx_inst_results_{fid}"] = results

                results = st.session_state.get(f"xlsx_inst_results_{fid}")
                if results:
                    st.markdown("**実行結果**")
                    for idx, (title, result_df) in enumerate(results):
                        st.markdown(f"**{title}**")
                        st.dataframe(
                            result_df, width="stretch", hide_index=True,
                        )
                        st.download_button(
                            "📥 CSV",
                            data=result_df.to_csv(index=False).encode("utf-8-sig"),
                            file_name=f"agg_{idx + 1}.csv",
                            mime="text/csv",
                            key=f"dl_xlsx_inst_{fid}_{idx}",
                        )
            else:
                st.info("集計指示がありません。上のフォームから追加してください。")

        xlsx["include_aggregation"] = st.checkbox(
            "集計結果を次の AI 質問に含める",
            value=xlsx.get("include_aggregation", True),
            key=f"xlsx_include_agg_{fid}",
        )
        _save_pending_xlsx(fid, xlsx)


def _render_xlsx_editor() -> None:
    """添付 Excel の表編集・集計 UI（複数ブック対応）"""
    xlsx_map = _pending_xlsx_map()
    if not xlsx_map:
        return
    for pf in _pending_files_list():
        fid = pf.get("id")
        xlsx = xlsx_map.get(fid) if fid else None
        if not xlsx:
            continue
        st.markdown(f"**📊 Excel 編集 — {pf.get('name', 'book.xlsx')}**")
        sheet_names = list(xlsx.get("sheets", {}).keys())
        if not sheet_names:
            st.warning("シートがありません。")
            continue
        active = st.selectbox(
            "シート",
            sheet_names,
            key=f"xlsx_sheet_sel_{fid}",
        )
        xlsx["active_sheet"] = active
        edited = st.data_editor(
            xlsx["sheets"][active],
            key=f"xlsx_data_{fid}_{active}",
            num_rows="dynamic",
            width="stretch",
        )
        xlsx["sheets"][active] = edited
        xlsx_map[fid] = xlsx
        st.session_state.pending_xlsx = xlsx_map

        _render_xlsx_aggregation(fid, xlsx, active, edited)

        st.download_button(
            "📥 Excelを保存",
            data=office.workbook_bytes_from_sheets(xlsx["sheets"]),
            file_name=xlsx.get("name") or pf.get("name") or "edited.xlsx",
            mime=office.OFFICE_MIME_MAP["xlsx"],
            key=f"dl_xlsx_edited_{fid}",
        )
    _sync_xlsx_files()
    st.caption(
        "表の編集・集計ができます。編集内容と集計結果（オプション）は次の質問送信時に AI にも渡されます。"
    )


def _render_copy_button(text: str, key: str) -> None:
    """回答・メッセージをクリップボードへコピー（ポップオーバー + ワンクリック）"""
    if not text:
        st.caption("（コピーする内容がありません）")
        return
    with st.popover("📋 コピー"):
        st.caption(
            f"全文 {len(text):,} 文字。"
            "「コピー実行」を押すか、下のテキスト欄右上の ⎘ アイコンをご利用ください。"
        )
        escaped = html.escape(text)
        components.html(
            f"""<textarea id="copy-src-{key}" style="position:fixed;left:-9999px;top:0;"
            >{escaped}</textarea>
            <button id="copy-btn-{key}" type="button" onclick="
                (function() {{
                    var ta = document.getElementById('copy-src-{key}');
                    var btn = document.getElementById('copy-btn-{key}');
                    var done = function(ok) {{
                        btn.innerText = ok ? '✓ コピーしました' : 'コピーに失敗しました';
                        btn.style.borderColor = ok ? '#2d7dd2' : '#d9534f';
                        btn.style.color = ok ? '#2d7dd2' : '#d9534f';
                        setTimeout(function() {{
                            btn.innerText = '📋 コピー実行';
                            btn.style.borderColor = '#dde1e7';
                            btn.style.color = '#666';
                        }}, 2500);
                    }};
                    ta.focus();
                    ta.select();
                    ta.setSelectionRange(0, ta.value.length);
                    var ok = false;
                    try {{ ok = document.execCommand('copy'); }} catch (e) {{}}
                    if (ok) {{ done(true); return; }}
                    if (navigator.clipboard && navigator.clipboard.writeText) {{
                        navigator.clipboard.writeText(ta.value).then(function() {{
                            done(true);
                        }}).catch(function() {{ done(false); }});
                    }} else {{
                        done(false);
                    }}
                }})();
            " style="
                background:#fff;border:1px solid #dde1e7;border-radius:6px;
                padding:6px 14px;font-size:0.85rem;color:#666;cursor:pointer;
            ">📋 コピー実行</button>""",
            height=44,
        )
        preview = text if len(text) <= 8000 else text[:8000] + "\n\n…（以下省略）"
        st.code(preview, language=None)
        if len(text) > 8000:
            st.caption("長文の全文コピーは上の「コピー実行」をご利用ください。")


def _embed_images_markdown(text: str, images: list[tuple[bytes, str]]) -> str:
    if not images:
        return text
    parts = [text] if text.strip() else []
    for data, mime in images:
        b64 = base64.standard_b64encode(data).decode()
        parts.append(f"![生成画像](data:{mime};base64,{b64})")
    return "\n\n".join(parts)


def _resolve_user_text(prompt: str, tmpl: dict | None, file_infos: list[dict]) -> str:
    """API 送信用のユーザーテキスト（空プロンプト時はデフォルト文言）"""
    text = (prompt or "").strip()
    if text:
        return text
    if tmpl and llm.template_requires_audio(tmpl):
        if len(file_infos) > 1:
            return "添付の音声データをすべて処理してください。"
        return "添付の音声データを処理してください。"
    return "テンプレートの指示に従って処理してください。"


def _user_message_display(prompt: str, file_infos: list[dict]) -> str:
    """チャット履歴・画面表示用のユーザーメッセージ"""
    text = (prompt or "").strip()
    if file_infos:
        if len(file_infos) == 1:
            attach_line = f"📎 *{file_infos[0]['name']}*"
        else:
            names = "、".join(f["name"] for f in file_infos[:5])
            if len(file_infos) > 5:
                names += f" 他 {len(file_infos) - 5} 件"
            attach_line = f"📎 *{names}*（{len(file_infos)} 件）"
        if text:
            return f"{attach_line}\n\n{text}"
        return f"{attach_line}\n\n（プロンプトなしで実行）"
    if text:
        return text
    return "（プロンプトなしで実行）"


def _model_display_label(model_id: str) -> str:
    """UI 表示用のモデル名"""
    user_models = st.session_state.get("user_models") or {}
    if model_id in user_models:
        return user_models[model_id]
    info = llm.get_model_info(model_id) or {}
    return info.get("label") or model_id


def _ai_working_status(
    *,
    phase: str,
    tmpl: dict | None,
    has_attachment: bool,
    file_attachments: list[llm.FileAttachment] | None,
    effective_model: str = "",
    use_search: bool = False,
) -> tuple[str, str]:
    """処理中表示の (メイン文言, 補足)"""
    if phase == "routing":
        router = llm.get_router_model_label()
        detail = f"ルーター: {router}" if router else "プロンプト内容を分析しています"
        return "🎯 最適なモデルを選定中…", detail

    model_label = _model_display_label(effective_model) if effective_model else ""
    att_count = len(file_attachments or [])
    if llm.is_image_generation_model(effective_model):
        if file_attachments:
            return "🎨 参照画像をもとに生成中…", model_label
        return "🎨 画像を生成中…", model_label
    if tmpl and llm.template_requires_audio(tmpl):
        detail = "初回は数十秒かかる場合があります"
        if att_count > 1:
            detail = f"音声 {att_count} 件 — {detail}"
        return "🎧 音声を解析・文字起こし中…", detail
    if use_search:
        return "🌐 Web 検索しながら回答を生成中…", model_label
    if has_attachment or file_attachments:
        detail = model_label
        if att_count > 1:
            detail = f"{att_count} 件の添付 — {detail}".strip(" —")
        return "📎 添付を読み込み、回答を生成中…", detail or "応答が始まると自動的に切り替わります"
    return "✨ AI が回答を生成中…", model_label


def _render_ai_working_indicator(placeholder, status: str, detail: str = "") -> None:
    """応答待ち・生成中の視覚的インジケータ（経過秒数 + 不定プログレス）"""
    start_ms = int(time.time() * 1000)
    safe_status = html.escape(status)
    safe_detail = html.escape(detail) if detail else "応答が始まると自動的に切り替わります"
    with placeholder.container():
        components.html(
            f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{
                font-family: "Source Sans Pro", sans-serif;
                background: transparent;
                color: #2c2c2c;
                padding: 2px 0 4px;
            }}
            .wrap {{
                background: #f4f8ff;
                border: 1px solid #c5d9f2;
                border-radius: 10px;
                padding: 10px 14px;
            }}
            .head {{
                display: flex;
                align-items: center;
                gap: 10px;
                margin-bottom: 8px;
            }}
            .spinner {{
                width: 18px; height: 18px; flex-shrink: 0;
                border: 2.5px solid #c5d9f2;
                border-top-color: #2d7dd2;
                border-radius: 50%;
                animation: nai-spin 0.85s linear infinite;
            }}
            @keyframes nai-spin {{ to {{ transform: rotate(360deg); }} }}
            .status {{
                flex: 1;
                font-size: 0.92rem;
                font-weight: 600;
                color: #1a4f8a;
                line-height: 1.3;
            }}
            .elapsed {{
                font-size: 0.78rem;
                color: #5a6a7a;
                font-variant-numeric: tabular-nums;
                white-space: nowrap;
            }}
            .bar {{
                height: 4px;
                background: #dce8f8;
                border-radius: 2px;
                overflow: hidden;
                margin-bottom: 6px;
            }}
            .bar-fill {{
                height: 100%;
                width: 40%;
                background: linear-gradient(90deg, #2d7dd2, #5ba3e8, #2d7dd2);
                border-radius: 2px;
                animation: nai-indeterminate 1.4s ease-in-out infinite;
            }}
            @keyframes nai-indeterminate {{
                0% {{ transform: translateX(-120%); }}
                100% {{ transform: translateX(320%); }}
            }}
            .detail {{
                font-size: 0.76rem;
                color: #667788;
                line-height: 1.35;
            }}
            </style></head><body>
            <div class="wrap" role="status">
                <div class="head">
                    <div class="spinner" aria-hidden="true"></div>
                    <div class="status">{safe_status}</div>
                    <div class="elapsed" id="elapsed">0.0秒</div>
                </div>
                <div class="bar" aria-hidden="true"><div class="bar-fill"></div></div>
                <div class="detail">{safe_detail}</div>
            </div>
            <script>
            (function() {{
                var start = {start_ms};
                var el = document.getElementById("elapsed");
                function tick() {{
                    var sec = (Date.now() - start) / 1000;
                    el.textContent = sec.toFixed(1) + "秒";
                }}
                tick();
                setInterval(tick, 200);
            }})();
            </script>
            </body></html>""",
            height=88,
            scrolling=False,
        )


def _clear_generation_state() -> None:
    st.session_state.pop("gen_task", None)
    st.session_state.pop("_gen_iterator", None)


def _cleanup_generation_files() -> None:
    """生成完了・キャンセル後の添付ファイル後始末"""
    remaining: list[dict] = []
    for pf in _pending_files_list():
        if office.is_xlsx(pf.get("name", ""), pf.get("mime", "")):
            remaining.append(pf)
        else:
            _dispose_uploaded_file(pf)
    _set_pending_files(remaining)
    if not remaining:
        st.session_state.file_uploader_key = (
            st.session_state.get("file_uploader_key", 0) + 1
        )


def _finalize_generation(*, cancelled: bool = False) -> None:
    """生成結果を DB / セッションに保存して状態をクリア"""
    task = st.session_state.get("gen_task")
    if not task:
        return

    user = st.session_state.user
    emp = user["employee_id"]
    full_response = task.get("full_response", "")
    image_parts = task.get("image_parts") or []
    office_outputs = list(task.get("office_outputs") or [])

    if cancelled:
        suffix = "\n\n⏹ *（ユーザーにより処理がキャンセルされました）*"
        full_response = (full_response + suffix) if full_response.strip() else "⏹ *処理がキャンセルされました。*"

    output_fmt = task.get("output_fmt")
    if (
        not cancelled
        and output_fmt
        and full_response
        and not full_response.startswith("⚠️")
        and not office_outputs
    ):
        try:
            office_outputs = office.build_output_files(output_fmt, full_response)
        except Exception as ex:
            full_response += f"\n\n⚠️ {output_fmt.upper()} ファイルの生成に失敗: {ex}"

    full_response = _embed_images_markdown(full_response, image_parts)
    elapsed = int((time.time() - task["started_at"]) * 1000)

    content_to_save = full_response
    if office_outputs:
        content_to_save += office.serialize_office_outputs(office_outputs)

    log_id = db.log_query(
        session_id=st.session_state.current_sid,
        employee_id=emp,
        username=user["username"],
        department=user.get("department", ""),
        model=task.get("effective_model") or st.session_state.selected_model,
        system_prompt=task["sys_prompt"],
        question=task["user_display"],
        answer=content_to_save,
        has_attachment=task["has_attachment"],
        used_search=task.get("use_search", False),
        input_tokens=task.get("in_tok", 0),
        output_tokens=task.get("out_tok", 0),
        client_ip=get_client_ip(),
        elapsed_ms=elapsed,
    )
    db.save_message(st.session_state.current_sid, "assistant", content_to_save, log_id)
    assistant_msg = {
        "role": "assistant",
        "content": full_response,
        "log_id": log_id,
        "feedback_rating": None,
    }
    if office_outputs:
        assistant_msg["office_outputs"] = office_outputs
    st.session_state.messages.append(assistant_msg)

    if not cancelled:
        db.increment_daily_count(emp)

    db.touch_session(st.session_state.current_sid)
    st.session_state.sessions_cache = db.get_sessions(emp)
    is_first = task.get("is_first", False)

    _cleanup_generation_files()
    _clear_generation_state()

    if is_first:
        st.rerun()


def _render_generation_controls(task: dict, placeholder) -> None:
    """処理中ステータス + キャンセルボタン"""
    tmpl = st.session_state.active_template
    phase = task.get("phase", "generating")
    if phase == "routing":
        status, detail = _ai_working_status(
            phase="routing",
            tmpl=tmpl,
            has_attachment=task["has_attachment"],
            file_attachments=task.get("file_attachments"),
        )
    else:
        status, detail = _ai_working_status(
            phase="generating",
            tmpl=tmpl,
            has_attachment=task["has_attachment"],
            file_attachments=task.get("file_attachments"),
            effective_model=task.get("effective_model", ""),
            use_search=task.get("use_search", False),
        )

    elapsed = time.time() - task["started_at"]
    with placeholder.container():
        st.markdown(
            f'<div style="background:#fff8e6;border:1px solid #e6c200;border-radius:8px;'
            f'padding:8px 12px;margin-bottom:8px;font-size:0.85rem;color:#6a5500;">'
            f'⏳ <strong>処理中</strong> — {html.escape(status)} '
            f'（{elapsed:.1f}秒）</div>',
            unsafe_allow_html=True,
        )
        if not task.get("streaming_started"):
            _render_ai_working_indicator(st.empty(), status, detail)
        col_sp, col_cancel = st.columns([4, 1])
        with col_cancel:
            if st.button("⏹ 停止", key="btn_cancel_generation", type="secondary"):
                task["cancelled"] = True
                st.rerun(scope="fragment")


def _process_stream_chunk(item, task: dict) -> None:
    """ストリーム1チャンクを task に反映"""
    if isinstance(item, tuple):
        if item[0] == "__image__":
            _, img_data, img_mime = item
            task.setdefault("image_parts", []).append((img_data, img_mime))
            task["streaming_started"] = True
        elif item[0] == "__meta__":
            _, full_response, in_tok, out_tok = item
            task["full_response"] = full_response
            task["in_tok"] = in_tok
            task["out_tok"] = out_tok
            task["phase"] = "complete"
    else:
        task["streaming_started"] = True
        task["full_response"] = task.get("full_response", "") + item


def _render_streaming_partial(task: dict, placeholder) -> None:
    """ストリーミング中の部分応答を表示（キャンセルボタン付き）"""
    full_response = task.get("full_response", "")
    image_parts = task.get("image_parts") or []
    with placeholder.container():
        elapsed = time.time() - task["started_at"]
        model_label = _model_display_label(task.get("effective_model", ""))
        st.markdown(
            f'<div style="background:#fff8e6;border:1px solid #e6c200;border-radius:8px;'
            f'padding:8px 12px;margin-bottom:8px;font-size:0.85rem;color:#6a5500;">'
            f'⏳ <strong>回答を生成中</strong>（{elapsed:.1f}秒）— {html.escape(model_label)}</div>',
            unsafe_allow_html=True,
        )
        _, col_cancel = st.columns([5, 1])
        with col_cancel:
            if st.button("⏹ 停止", key="btn_cancel_generation_stream", type="secondary"):
                task["cancelled"] = True
                st.rerun(scope="fragment")
        if full_response:
            st.markdown(full_response + " ▌")
        if image_parts:
            _render_generated_images(
                image_parts, f"live_{st.session_state.current_sid}",
            )


@st.fragment
def _generation_fragment() -> None:
    """AI 応答を段階的に生成（キャンセル可能）"""
    task = st.session_state.get("gen_task")
    if not task:
        return

    with st.chat_message("assistant", avatar="✨"):
        placeholder = st.empty()

        if task.get("cancelled"):
            _finalize_generation(cancelled=True)
            st.rerun()
            return

        if task.get("error"):
            placeholder.error(task["error"])
            _cleanup_generation_files()
            _clear_generation_state()
            st.rerun()
            return

        try:
            if task["phase"] == "routing":
                _render_generation_controls(task, placeholder)
                if task.get("is_auto_model"):
                    pass  # 表示のみ（次ステップでモデル決定）
                task["effective_model"] = resolve_effective_model(
                    task["user_text_api"],
                    has_attachment=task["has_attachment"],
                )
                use_search = (
                    st.session_state.use_web_search
                    and llm.web_search_supported(task["effective_model"])
                )
                tmpl = st.session_state.active_template
                if llm.is_image_generation_model(task["effective_model"]) or (
                    tmpl and llm.template_requires_audio(tmpl)
                ):
                    use_search = False
                task["use_search"] = use_search
                task["phase"] = "generating"
                settings = st.session_state.settings
                st.session_state["_gen_iterator"] = llm.stream_response(
                    history=task["history_for_api"],
                    user_text=task["user_text_api"],
                    file_attachments=task.get("file_attachments"),
                    model=task["effective_model"],
                    system_prompt=task["sys_prompt"],
                    temperature=settings["temp"],
                    max_tokens=settings["max_tokens"],
                    use_web_search=use_search,
                )
                st.rerun(scope="fragment")
                return

            if task["phase"] == "generating":
                if not task.get("streaming_started"):
                    _render_generation_controls(task, placeholder)

                it = st.session_state.get("_gen_iterator")
                if it is None:
                    task["error"] = "⚠️ 生成セッションが見つかりません。再度お試しください。"
                    st.rerun(scope="fragment")
                    return

                try:
                    item = next(it)
                except StopIteration:
                    task["phase"] = "complete"
                    st.rerun(scope="fragment")
                    return

                _process_stream_chunk(item, task)

                if task["phase"] == "complete":
                    st.rerun(scope="fragment")
                    return

                _render_streaming_partial(task, placeholder)
                st.rerun(scope="fragment")
                return

            if task["phase"] == "complete":
                full_response = task.get("full_response", "")
                image_parts = task.get("image_parts") or []
                office_outputs = task.get("office_outputs") or []
                with placeholder.container():
                    if full_response:
                        st.markdown(full_response)
                    if image_parts:
                        _render_generated_images(
                            image_parts, f"live_{st.session_state.current_sid}",
                        )
                    if office_outputs:
                        _render_office_outputs(
                            office_outputs, f"live_{st.session_state.current_sid}",
                        )
                _finalize_generation(cancelled=False)
                st.rerun()
                return

        except Exception as e:
            task["error"] = f"⚠️ エラー: {e}"
            st.rerun(scope="fragment")


def resolve_effective_model(prompt: str, has_attachment: bool = False) -> str:
    """テンプレート / 自動選択 / ユーザー選択から実際のモデル ID を決定"""
    emp = st.session_state.user["employee_id"]
    tmpl = st.session_state.active_template
    user_models = st.session_state.user_models

    if tmpl:
        tmpl_model = (tmpl.get("default_model") or "").strip()
        if tmpl_model and tmpl_model in user_models:
            return tmpl_model
        user_default = db.get_effective_default_model(emp)
        if user_default in user_models:
            return user_default
        return st.session_state.selected_model if st.session_state.selected_model != llm.AUTO_MODEL_ID else next(iter(user_models))

    selected = st.session_state.selected_model
    if selected == llm.AUTO_MODEL_ID:
        return llm.resolve_model(
            selected, prompt, user_models,
            template_active=False, has_attachment=has_attachment,
        )
    return selected


# ══════════════════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════════════════
CUSTOM_CSS = """
<style>
[data-testid="stApp"] { background-color:#ffffff; color:#1a1a1a; }
section[data-testid="stSidebar"] { background-color:#f4f6f9; border-right:1px solid #dde1e7; }
section[data-testid="stSidebar"] * { color:#2c2c2c !important; }
""" + ui_common.SIDEBAR_FIX_CSS + """
#MainMenu { visibility:hidden; }
footer { visibility:hidden; }

/* ─ 処理中・キャンセル ─ */
[data-testid="stChatMessage"] .st-key-btn_cancel_generation button,
[data-testid="stChatMessage"] .st-key-btn_cancel_generation_stream button {
    border-color: #d9534f !important;
    color: #c9302c !important;
    font-size: 0.82rem !important;
}
[data-testid="stChatMessage"] .st-key-btn_cancel_generation button:hover,
[data-testid="stChatMessage"] .st-key-btn_cancel_generation_stream button:hover {
    background: #fdf0f0 !important;
}

/* ─ サイドバーボタン ─ */
section[data-testid="stSidebar"] .stButton button {
    background:#fff; border:1px solid #ccd0d9; border-radius:8px;
    color:#2c2c2c !important; font-size:0.84rem; padding:6px 10px;
    transition:background 0.15s; width:100%; text-align:left;
}
section[data-testid="stSidebar"] .stButton button:hover {
    background:#eaf0fb; border-color:#2d7dd2;
}
.new-chat-btn button {
    background:linear-gradient(135deg,#2d7dd2,#1a5fa8) !important;
    border:none !important; color:#fff !important; font-weight:600 !important;
    border-radius:10px !important; padding:10px !important;
}
.session-active button,
section[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] button {
    background:#dceeff !important; border-color:#2d7dd2 !important;
    color:#1a4f8a !important;
}

/* ─ チャット履歴（コンパクト + スクロール領域） ─ */
section[data-testid="stSidebar"] .st-key-session_list .stButton {
    margin-bottom: 0 !important;
}
section[data-testid="stSidebar"] .st-key-session_list .stButton button {
    padding: 3px 8px !important;
    font-size: 0.8rem !important;
    line-height: 1.25 !important;
    min-height: unset !important;
}
section[data-testid="stSidebar"] .st-key-session_actions .stButton,
section[data-testid="stSidebar"] .st-key-session_actions .stDownloadButton {
    margin-bottom: 4px !important;
}
section[data-testid="stSidebar"] hr {
    margin: 6px 0 !important;
}

/* ─ チャット ─ */
[data-testid="stChatMessage"] { background:transparent !important; padding:2px 0; }
[data-testid="stChatInput"] {
    background:#f9fafc; border:1px solid #c8cdd6; border-radius:12px;
}
[data-testid="stChatInput"] textarea { color:#1a1a1a !important; }

/* ─ テキストエリア / セレクト ─ */
.stTextArea textarea {
    background:#fff !important; color:#1a1a1a !important;
    border-color:#ccd0d9 !important; border-radius:8px !important;
    font-size:0.82rem !important;
}

/* ─ コードブロック ─ */
pre { background:#f3f4f6 !important; border:1px solid #dde1e7 !important; border-radius:8px !important; }

/* ─ スクロールバー ─ */
::-webkit-scrollbar { width:5px; }
::-webkit-scrollbar-track { background:#f0f2f5; }
::-webkit-scrollbar-thumb { background:#b0b8c4; border-radius:3px; }

/* ─ タイトルバー ─ */
.chat-title {
    font-size:1.25rem; font-weight:700; color:#1a4f8a;
    padding:8px 0 14px; border-bottom:1px solid #dde1e7; margin-bottom:14px;
}

/* ─ フィードバックボタン ─ */
.fb-row button {
    background:transparent !important; border:1px solid #dde1e7 !important;
    border-radius:6px !important; padding:2px 8px !important;
    font-size:0.8rem !important; color:#666 !important;
    min-width:0 !important; height:28px !important;
}
.fb-row button:hover { background:#f0f5ff !important; border-color:#2d7dd2 !important; }

/* ─ メッセージ操作（コピー等） ─ */
.msg-actions { margin-top:4px; }

/* ─ お知らせ ─ */
.memo-box {
    background:#f0f7f0; border:1px solid #7dbf7d; border-radius:10px;
    padding:10px 14px; margin:6px 0 14px; color:#2a5a2a; font-size:0.88rem;
}
</style>
"""


# ══════════════════════════════════════════════════════════
# ログイン
# ══════════════════════════════════════════════════════════

def show_login() -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    ui_common.render_login_page_style()
    ui_common.render_login_banner()
    st.markdown("## ✨ 社内 AI アシスタント")
    with st.form("login_form"):
        emp_id = st.text_input("社員番号 / ID", placeholder="例: 12345 または admin")
        password = st.text_input("パスワード", type="password")
        submitted = st.form_submit_button("ログイン", width="stretch")
        if submitted:
            user = db.authenticate_user(emp_id.strip(), password)
            if user and not db.user_can_access_nai(user["employee_id"]):
                st.error(
                    "NAI（社内 AI アシスタント）の利用が許可されていません。"
                    "管理者にお問い合わせください。"
                )
            elif user:
                st.session_state.user = user
                st.session_state.authenticated = True
                st.session_state.last_activity = time.time()
                for key in (
                    "selected_model", "model_sel", "active_template",
                    "tmpl_sel", "_template_initialized", "use_web_search",
                ):
                    st.session_state.pop(key, None)
                st.rerun()
            else:
                st.error("社員番号またはパスワードが正しくありません。")


# ══════════════════════════════════════════════════════════
# セッション状態初期化
# ══════════════════════════════════════════════════════════

def init_state() -> None:
    user = st.session_state.user
    emp  = user["employee_id"]

    if "current_sid" not in st.session_state:
        st.session_state.current_sid = new_sid()
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "sessions_cache" not in st.session_state:
        st.session_state.sessions_cache = db.get_sessions(emp)
    if "settings" not in st.session_state:
        st.session_state.settings = load_settings()
    if "active_template" not in st.session_state:
        st.session_state.active_template = _default_active_template(emp)
    _ensure_active_template_allowed(emp)
    if "pending_files" not in st.session_state:
        st.session_state.pending_files = []
    if "file_uploader_key" not in st.session_state:
        st.session_state.file_uploader_key = 0
    if "pending_xlsx" not in st.session_state:
        st.session_state.pending_xlsx = {}
    _migrate_pending_state()
    # ユーザーごとのモデル・権限（毎回リフレッシュ）
    fresh_user = db.get_user(emp)
    if fresh_user:
        st.session_state.user = fresh_user
    st.session_state.user_models = get_user_models(emp)
    st.session_state.web_search_ok = db.get_effective_web_search_allowed(emp)
    if "use_web_search" not in st.session_state:
        st.session_state.use_web_search = st.session_state.web_search_ok
    # 初期モデルはユーザー管理の実効デフォルトに合わせる（自動選択は初期値にしない）
    user_models_map = st.session_state.user_models
    if "selected_model" not in st.session_state:
        st.session_state.selected_model = _initial_selected_model(emp, user_models_map)
    elif st.session_state.selected_model not in user_models_map \
            and st.session_state.selected_model != llm.AUTO_MODEL_ID:
        st.session_state.selected_model = _initial_selected_model(emp, user_models_map)


def load_session(session_id: str) -> None:
    _clear_pending_files()
    st.session_state.current_sid = session_id
    st.session_state.messages = _normalize_messages(db.get_messages(session_id))
    st.session_state.file_uploader_key = st.session_state.get("file_uploader_key", 0) + 1
    emp = st.session_state.user["employee_id"]
    st.session_state.sessions_cache = db.get_sessions(emp)


def start_new_chat() -> None:
    emp = st.session_state.user["employee_id"]
    _clear_pending_files()
    st.session_state.current_sid = new_sid()
    st.session_state.messages = []
    st.session_state.file_uploader_key = st.session_state.get("file_uploader_key", 0) + 1
    st.session_state.active_template = _default_active_template(emp)
    st.session_state.pop("tmpl_sel", None)
    st.session_state.sessions_cache = db.get_sessions(emp)


# ══════════════════════════════════════════════════════════
# サイドバー
# ══════════════════════════════════════════════════════════

def render_sidebar() -> None:
    user = st.session_state.user
    emp  = user["employee_id"]

    with st.sidebar:
        st.markdown(
            '<div style="font-size:1.3rem;font-weight:700;color:#1a4f8a;padding:10px 0 6px;">✨ AI アシスタント</div>',
            unsafe_allow_html=True,
        )

        # 新規チャット
        st.markdown('<div class="new-chat-btn">', unsafe_allow_html=True)
        if st.button("＋ 新しいチャット", width="stretch", key="btn_new"):
            start_new_chat(); st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

        # ── モデル選択（ユーザーの許可リストでフィルタ済み） ──
        user_models = st.session_state.user_models
        tmpl_active = bool(st.session_state.active_template)
        if len(user_models) >= 1:
            st.markdown(
                '<div style="font-size:0.78rem;color:#555;padding:10px 0 2px;">🤖 モデル選択</div>',
                unsafe_allow_html=True,
            )
            model_keys = list(user_models.keys())
            model_labels = list(user_models.values())
            # 自動選択はテンプレート未使用時のみ
            if not tmpl_active and len(user_models) > 1:
                model_keys = [llm.AUTO_MODEL_ID] + model_keys
                model_labels = [llm.AUTO_MODEL_LABEL] + model_labels
            elif tmpl_active and st.session_state.selected_model == llm.AUTO_MODEL_ID:
                st.session_state.selected_model = _initial_selected_model(emp, user_models)
                _sync_model_selectbox(user_models, st.session_state.selected_model)

            cur = st.session_state.selected_model
            if "model_sel" not in st.session_state and cur in model_keys:
                _sync_model_selectbox(user_models, cur)
            cur_idx = model_keys.index(cur) if cur in model_keys else 0
            sel_label = st.selectbox(
                "model", model_labels, index=cur_idx,
                label_visibility="collapsed", key="model_sel",
            )
            st.session_state.selected_model = model_keys[model_labels.index(sel_label)]

            default_label = db.format_user_default_model_label(emp, user_models)
            if tmpl_active:
                st.caption("📋 テンプレート使用中 — 自動選択は無効")
                st.caption(f"ユーザー既定 LLM: {default_label}")
            elif st.session_state.selected_model == llm.AUTO_MODEL_ID:
                if llm.ollama_router_available():
                    rl = llm.get_router_model_label()
                    st.caption(f"🎯 {rl} で質問を分析 → 最適なクラウド LLM を自動選択")
                else:
                    st.caption("🎯 質問内容から最適なモデルを自動選択（ルールベース）")
                st.caption(f"ユーザー既定 LLM: {default_label}")
            else:
                st.caption(f"ユーザー既定 LLM: {default_label}")

        # ── テンプレート選択（許可モデルに合うもののみ） ──
        st.markdown(
            '<div style="font-size:0.78rem;color:#555;padding:8px 0 2px;">📋 プロンプトテンプレート</div>',
            unsafe_allow_html=True,
        )
        all_templates = db.get_active_templates()
        templates = db.get_active_templates_for_user(emp)
        if len(templates) < len(all_templates):
            st.caption(
                f"利用可能なモデルに合うテンプレート {len(templates)} 件を表示しています。"
            )
        if not templates:
            st.warning("利用可能なモデルに合うテンプレートがありません。")
            st.session_state.active_template = None
        tmpl_options = ["(デフォルト)"] + [t["name"] for t in templates]
        if st.session_state.active_template and st.session_state.active_template.get("id") not in {
            t["id"] for t in templates
        }:
            st.session_state.active_template = _default_active_template(emp)
            st.session_state.pop("tmpl_sel", None)
        if "tmpl_sel" not in st.session_state:
            _sync_template_selectbox(templates, st.session_state.active_template)
        current_idx = 0
        if st.session_state.active_template:
            for i, t in enumerate(templates):
                if t["id"] == st.session_state.active_template.get("id"):
                    current_idx = i + 1
                    break
        sel = st.selectbox("テンプレート", tmpl_options, index=current_idx,
                           label_visibility="collapsed", key="tmpl_sel")
        if sel == "(デフォルト)":
            st.session_state.active_template = None
        else:
            for t in templates:
                if t["name"] == sel:
                    st.session_state.active_template = t; break

        active_tmpl = st.session_state.active_template
        if active_tmpl:
            cat = (active_tmpl.get("category") or "").strip()
            if cat == "音声":
                hint = "🎙️ 音声ファイル（MP3/WAV等）を添付してご利用ください"
                if db.template_allows_empty_prompt(active_tmpl):
                    hint += "。プロンプト未入力でも実行できます"
                st.caption(hint)
            elif cat == "画像":
                st.caption(
                    "🎨 生成したい画像の説明を入力してください。"
                    " 参照画像を1件添付するとそのイメージを基に生成します。"
                )

        # ── Web 検索（Gemini のみ + ユーザー権限） ──
        effective_model = st.session_state.selected_model
        if effective_model == llm.AUTO_MODEL_ID:
            ws_model_ok = any(
                llm.web_search_supported(m) for m in user_models
            )
        else:
            ws_model_ok = llm.web_search_supported(effective_model)

        ws_blocked = (
            active_tmpl
            and (
                llm.template_requires_audio(active_tmpl)
                or llm.is_image_generation_model((active_tmpl.get("default_model") or "").strip())
            )
        )
        if st.session_state.web_search_ok and ws_model_ok and not ws_blocked:
            st.markdown(
                '<div style="font-size:0.78rem;color:#555;padding:6px 0 2px;">🌐 Web 検索 (Grounding)</div>',
                unsafe_allow_html=True,
            )
            st.session_state.use_web_search = st.toggle(
                "最新情報をWeb検索", value=st.session_state.use_web_search,
                key="web_toggle", label_visibility="collapsed",
            )
        else:
            st.session_state.use_web_search = False

        st.markdown(
            '<div style="font-size:0.78rem;color:#555;padding:6px 0 2px;">📂 チャット履歴</div>',
            unsafe_allow_html=True,
        )

        # セッション一覧（固定高さでスクロール、下部の会話操作を常に見える位置に）
        with st.container(height=240, border=False, key="session_list"):
            for s in st.session_state.sessions_cache[:40]:
                label = s["title"] or "新しいチャット"
                is_active = s["session_id"] == st.session_state.current_sid
                if st.button(
                    f"{'▶ ' if is_active else ''}{label}",
                    key=f"sess_{s['session_id']}",
                    width="stretch",
                    type="primary" if is_active else "secondary",
                ):
                    load_session(s["session_id"]); st.rerun()

        st.divider()

        # 会話操作
        if st.session_state.messages:
            st.markdown(
                '<div style="font-size:0.78rem;color:#555;padding:2px 0 4px;">📎 会話操作</div>',
                unsafe_allow_html=True,
            )
            with st.container(border=False, key="session_actions"):
                if st.button("🗑️ この会話を削除", width="stretch", key="btn_del"):
                    db.delete_session(st.session_state.current_sid)
                    start_new_chat(); st.rerun()
                st.download_button(
                    "📥 会話をエクスポート",
                    data=_build_export(),
                    file_name=f"chat_{datetime.datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.txt",
                    mime="text/plain",
                    width="stretch",
                )

        # ── パスワード変更 ──
        if db.user_can_change_password(emp):
            with st.expander("🔑 パスワードを変更"):
                with st.form("pw_change_form"):
                    cur_pw  = st.text_input("現在のパスワード", type="password", key="pw_cur")
                    new_pw  = st.text_input("新しいパスワード", type="password", key="pw_new")
                    conf_pw = st.text_input("新しいパスワード（確認）", type="password", key="pw_conf")
                    if st.form_submit_button("変更する", width="stretch"):
                        if not db.authenticate_user(emp, cur_pw):
                            st.error("現在のパスワードが正しくありません。")
                        elif len(new_pw) < 6:
                            st.error("パスワードは6文字以上必要です。")
                        elif new_pw != conf_pw:
                            st.error("確認パスワードが一致しません。")
                        else:
                            try:
                                db.change_password(emp, new_pw)
                                st.success("✅ パスワードを変更しました。")
                            except ValueError as e:
                                st.error(str(e))
        else:
            st.caption("🔒 パスワード変更は管理者により制限されています。")

        # ユーザー情報 & ログアウト
        st.divider()
        st.markdown(
            f'<div style="font-size:0.78rem;color:#555;">'
            f'👤 {user["username"]}（{user["department"] or "未設定"}）<br>'
            f'🆔 {emp}</div>',
            unsafe_allow_html=True,
        )

        # 日次利用状況
        limit = db.get_effective_daily_limit(emp)
        used  = db.get_daily_count(emp)
        if limit > 0:
            remain = max(0, limit - used)
            st.progress(
                min(used / limit, 1.0),
                text=f"本日 {used}/{limit} 件（残 {remain} 件）",
            )
        elif limit == 0:
            st.caption(f"本日 {used} 件使用（無制限）")

        if st.button("🚪 ログアウト", width="stretch", key="btn_logout"):
            _clear_pending_files()
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()


def _build_export() -> str:
    user = st.session_state.user
    tmpl = st.session_state.active_template
    lines = [
        "=== 社内 AI アシスタント 会話エクスポート ===",
        f"日時: {datetime.datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')} JST",
        f"ユーザー: {user['username']}（{user['employee_id']}）",
        f"テンプレート: {tmpl['name'] if tmpl else 'デフォルト'}",
        "=" * 40, "",
    ]
    for msg in st.session_state.messages:
        lbl = "【ユーザー】" if msg["role"] == "user" else "【アシスタント】"
        lines += [lbl, msg["content"], ""]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# チャット UI
# ══════════════════════════════════════════════════════════

def render_chat() -> None:
    ui_common.render_banner_and_memo()

    emp = st.session_state.user["employee_id"]
    max_mb = db.get_effective_upload_max_mb(emp)
    allowed_types = db.get_effective_upload_types(emp)
    max_bytes = max_mb * 1024 * 1024 if max_mb > 0 else None
    types_label = " / ".join(t.upper() for t in allowed_types)
    if max_mb > 0:
        limit_label = f"最大 {max_mb} MB"
    else:
        limit_label = "サイズ制限なし（アップロード上限 500 MB）"

    # タイトル
    title = "新しいチャット"
    for s in st.session_state.sessions_cache:
        if s["session_id"] == st.session_state.current_sid:
            title = s["title"]; break
    st.markdown(f'<div class="chat-title">✨ {title}</div>', unsafe_allow_html=True)

    audio_tmpl = llm.template_requires_audio(st.session_state.active_template)
    image_tmpl = llm.template_is_image_generation(st.session_state.active_template)
    max_pending = 1 if image_tmpl else MAX_PENDING_FILES
    image_types = [
        t for t in allowed_types if t in llm.IMAGE_REFERENCE_EXTENSIONS
    ]
    uploader_types = image_types if image_tmpl else allowed_types
    uploader_types_label = " / ".join(t.upper() for t in uploader_types)

    pending_files = _pending_files_list()
    pending_total_mb = _pending_files_total_size(pending_files) / (1024 * 1024)

    # ── ファイルアップロード ──
    with st.expander(
        "📎 ファイル / 画像 / 音声 / Office を添付",
        expanded=bool(pending_files) or audio_tmpl or image_tmpl,
    ):
        if audio_tmpl:
            st.info(
                f"🎙️ 音声ファイル（MP3, WAV, AAC, FLAC, M4A, OGG）を1件以上添付してください。"
                f" 合計上限: {limit_label}（最大 {MAX_PENDING_FILES} 件・混在可）"
            )
        elif image_tmpl:
            st.info(
                "🖼️ 参照したい画像を **1件** 添付できます（任意）。"
                " 添付がある場合はそのイメージを基に新しい画像を生成します。"
            )
        else:
            st.caption(
                f"最大 {MAX_PENDING_FILES} 件まで添付できます。"
                f" 合計サイズ上限: {limit_label}（形式の混在可）"
            )
        uploaded = st.file_uploader(
            f"対応形式: {uploader_types_label}（{limit_label}）",
            type=uploader_types,
            accept_multiple_files=not image_tmpl,
            key=f"file_uploader_{st.session_state.get('file_uploader_key', 0)}",
            label_visibility="collapsed",
        )
        if uploaded:
            merge_errors = _merge_uploaded_files(
                uploaded, uploader_types, max_bytes, max_mb, max_files=max_pending,
            )
            for msg in merge_errors:
                st.error(msg)
            pending_files = _pending_files_list()
            if pending_files and not merge_errors:
                pending_total_mb = _pending_files_total_size(pending_files) / (1024 * 1024)
                st.success(
                    f"📎 {len(pending_files)} 件を添付中"
                    f"（合計 {pending_total_mb:.1f} MB）"
                )
            elif pending_files and merge_errors:
                pending_total_mb = _pending_files_total_size(pending_files) / (1024 * 1024)
                st.info(
                    f"📎 現在 {len(pending_files)} 件"
                    f"（合計 {pending_total_mb:.1f} MB / 上限 {max_mb} MB）"
                )

        pending_files = _pending_files_list()
        if pending_files:
            pending_total_mb = _pending_files_total_size(pending_files) / (1024 * 1024)
            limit_note = f"{max_mb} MB" if max_mb > 0 else "制限なし"
            ref_label = "参照画像" if image_tmpl else "添付"
            st.info(
                f"📎 {ref_label} {len(pending_files)}/{max_pending} 件"
                f"（合計 {pending_total_mb:.1f} MB / 上限 {limit_note}）"
            )
            for pf in pending_files:
                size_label = f"{pf.get('size', 0) / (1024 * 1024):.1f} MB"
                col_name, col_rm = st.columns([6, 1])
                with col_name:
                    prefix = "🖼️ 参照: " if image_tmpl else "• "
                    st.caption(f"{prefix}{pf['name']}（{size_label}）")
                with col_rm:
                    if st.button("×", key=f"btn_rm_file_{pf['id']}"):
                        _remove_pending_file(pf["id"])
                        st.rerun()
            if st.button("すべて解除", key="btn_clear_all_files"):
                _clear_pending_files()
                st.session_state.file_uploader_key = (
                    st.session_state.get("file_uploader_key", 0) + 1
                )
                st.rerun()

        if _pending_xlsx_map():
            _render_xlsx_editor()

    # ── メッセージ一覧 ──
    for i, msg in enumerate(st.session_state.messages):
        role = msg["role"]
        with st.chat_message(role, avatar="🧑" if role == "user" else "✨"):
            _render_message_body(
                msg["content"], f"msg_{i}", msg.get("office_outputs"),
            )

            plain_text = _text_for_clipboard(msg["content"])
            st.markdown('<div class="msg-actions">', unsafe_allow_html=True)

            # コピー + フィードバック（アシスタント回答）
            if role == "assistant" and msg.get("log_id"):
                log_id = msg["log_id"]
                rated  = msg.get("feedback_rating")
                fb_key = f"fb_{i}_{log_id}"
                col_copy, col_good, col_bad, col_sp = st.columns([1.4, 1, 1, 7])
                with col_copy:
                    _render_copy_button(plain_text, f"copy_{i}")
                if rated is not None:
                    with col_good:
                        icon = "👍" if rated == 1 else "👎"
                        st.caption(f"{icon} 評価済み")
                else:
                    with col_good:
                        if st.button("👍", key=f"good_{fb_key}"):
                            db.save_feedback(log_id, 1)
                            st.session_state.messages[i]["feedback_rating"] = 1
                            st.rerun()
                    with col_bad:
                        if st.button("👎", key=f"bad_{fb_key}"):
                            db.save_feedback(log_id, -1)
                            st.session_state.messages[i]["feedback_rating"] = -1
                            st.rerun()
            else:
                col_copy, _ = st.columns([1.4, 11])
                with col_copy:
                    _render_copy_button(plain_text, f"copy_{i}")

            st.markdown("</div>", unsafe_allow_html=True)

    # ── AI 応答生成（処理中・キャンセル対応） ──
    _generation_fragment()

    # ── 入力 ──
    generating = bool(st.session_state.get("gen_task"))
    tmpl = st.session_state.active_template
    allow_empty = db.template_allows_empty_prompt(tmpl)
    chat_placeholder = (
        "AI が処理中です…"
        if generating
        else (
            "追加の指示があれば入力（空でも実行可）"
            if allow_empty else "メッセージを入力してください…"
        )
    )
    if generating:
        task = st.session_state.gen_task
        phase = task.get("phase", "generating")
        if phase == "routing":
            hint = "最適なモデルを選定しています…"
        elif task.get("streaming_started"):
            hint = "回答を生成しています…"
        else:
            hint = "AI が処理を開始しています…"
        st.caption(f"⏳ {hint} 停止する場合は上の「⏹ 停止」ボタンを押してください。")

    if allow_empty and not generating:
        needs_audio = llm.template_requires_audio(tmpl)
        can_run_empty = _pending_files_have_audio() if needs_audio else True
        if can_run_empty:
            if st.button("▶ 実行（プロンプト省略）", key="btn_run_empty_prompt", width="stretch"):
                _handle_message("")
        elif needs_audio:
            st.caption("音声ファイルを添付すると「実行（プロンプト省略）」ボタンが表示されます。")

    if prompt := st.chat_input(chat_placeholder, key="chat_input", disabled=generating):
        _handle_message(prompt)


def _handle_message(prompt: str) -> None:
    user      = st.session_state.user
    emp       = user["employee_id"]
    settings  = st.session_state.settings

    # 日次制限チェック
    limit = db.get_effective_daily_limit(emp)
    if limit > 0 and db.get_daily_count(emp) >= limit:
        st.error(f"⚠️ 本日の利用上限（{limit}件）に達しました。明日また利用してください。")
        st.stop()
        return

    # システムプロンプト決定（テンプレート優先）
    tmpl = st.session_state.active_template
    sys_prompt = tmpl["system_prompt"] if tmpl else settings["sys_prompt"]
    prompt_stripped = (prompt or "").strip()
    output_fmt = office.detect_output_format(prompt_stripped, sys_prompt)
    if output_fmt == "pptx":
        sys_prompt = (sys_prompt or "") + office.pptx_output_instruction()
    allow_empty = db.template_allows_empty_prompt(tmpl)

    if not prompt_stripped and not allow_empty:
        st.warning("メッセージを入力してください。")
        st.stop()
        return

    file_infos = _pending_files_list()
    image_tmpl = llm.template_is_image_generation(tmpl)

    if image_tmpl:
        if len(file_infos) > 1:
            st.error("⚠️ 画像生成では参照画像は1件のみ添付できます。")
            st.stop()
            return
        if len(file_infos) == 1 and not llm.attachment_is_image(
            _attachment_from_file_info(file_infos[0]),
        ):
            st.error(
                "⚠️ 画像生成の参照には画像ファイル"
                "（JPG/PNG/GIF/WebP）を指定してください。"
            )
            st.stop()
            return

    if llm.template_requires_audio(tmpl):
        if not _pending_files_have_audio():
            st.error(
                "⚠️ このテンプレートでは音声ファイル"
                "（MP3/WAV/AAC/FLAC/M4A/OGG）を1件以上添付してください。"
            )
            st.stop()
            return

    user_text_api = _resolve_user_text(prompt, tmpl, file_infos)
    file_attachments, user_text_api = _prepare_message_attachments(
        file_infos, user_text_api,
    )

    user_display = _user_message_display(prompt, file_infos)

    has_attachment = bool(file_infos) or bool(_pending_xlsx_map())
    is_auto_model = (
        not tmpl
        and st.session_state.selected_model == llm.AUTO_MODEL_ID
    )

    # ── ユーザーメッセージ表示 ──
    with st.chat_message("user", avatar="🧑"):
        st.markdown(user_display)

    # 初回メッセージ → セッション作成
    is_first = len(st.session_state.messages) == 0
    if is_first:
        title_src = prompt_stripped or (file_infos[0]["name"] if file_infos else "処理")
        if not prompt_stripped and len(file_infos) > 1:
            title_src = f"{file_infos[0]['name']} 他{len(file_infos) - 1}件"
        db.create_session(
            st.session_state.current_sid,
            settings["model"],
            emp,
            auto_title(title_src),
        )

    history_for_api = list(st.session_state.messages)

    db.save_message(st.session_state.current_sid, "user", user_display)
    st.session_state.messages.append({"role": "user", "content": user_display})

    # ── 非同期生成タスクを開始（fragment で段階実行・キャンセル可能） ──
    phase = "routing" if is_auto_model else "generating"
    st.session_state.gen_task = {
        "user_display": user_display,
        "user_text_api": user_text_api,
        "file_attachments": file_attachments or None,
        "sys_prompt": sys_prompt,
        "output_fmt": output_fmt,
        "has_attachment": has_attachment,
        "is_auto_model": is_auto_model,
        "is_first": is_first,
        "history_for_api": history_for_api,
        "started_at": time.time(),
        "phase": phase,
        "effective_model": "",
        "use_search": False,
        "full_response": "",
        "image_parts": [],
        "office_outputs": [],
        "in_tok": 0,
        "out_tok": 0,
        "streaming_started": False,
        "cancelled": False,
        "error": None,
    }

    if phase == "generating":
        effective_model = resolve_effective_model(
            user_text_api, has_attachment=has_attachment,
        )
        use_search = (
            st.session_state.use_web_search
            and llm.web_search_supported(effective_model)
        )
        if llm.is_image_generation_model(effective_model) or llm.template_requires_audio(tmpl):
            use_search = False
        st.session_state.gen_task["effective_model"] = effective_model
        st.session_state.gen_task["use_search"] = use_search
        st.session_state["_gen_iterator"] = llm.stream_response(
            history=history_for_api,
            user_text=user_text_api,
            file_attachments=file_attachments or None,
            model=effective_model,
            system_prompt=sys_prompt,
            temperature=settings["temp"],
            max_tokens=settings["max_tokens"],
            use_web_search=use_search,
        )

    st.rerun()


# ══════════════════════════════════════════════════════════
# エントリポイント
# ══════════════════════════════════════════════════════════

def main() -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    if not st.session_state.get("authenticated"):
        show_login()
        return

    check_session_timeout()
    if not db.user_can_access_nai(st.session_state.user["employee_id"]):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.error(
            "NAI（社内 AI アシスタント）の利用が許可されていません。"
            "管理者にお問い合わせください。"
        )
        st.stop()
    init_state()
    # 設定をリフレッシュ（管理者変更を反映）
    st.session_state.settings = load_settings()
    ui_common.render_sidebar_reopen_fab()
    render_sidebar()
    render_chat()


main()
