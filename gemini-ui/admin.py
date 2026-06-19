"""
admin.py — 管理者パネル
ユーザー管理 / テンプレート管理 / システム設定 / ログ / フィードバック / 統計
"""
import os
import sys
import datetime
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

_SHARED_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shared"))
if _SHARED_ROOT not in sys.path:
    sys.path.insert(0, _SHARED_ROOT)

import db
import llm_providers as llm
import maintenance_log
import sync_env_job
import template_registry as tmpl_reg
import ui_common
from user_admin import render_exam_users, render_fback_users, render_nai_v2_users

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "nakaboshi_admin0123")
JST = datetime.timezone(datetime.timedelta(hours=9))


def _selectable_models() -> dict[str, str]:
    """API キー設定済みのモデルのみ（選択肢・設定用）"""
    return llm.get_available_models()


def _user_default_model_label(employee_id: str) -> str:
    """チャット画面と同じデフォルト LLM 表示（db 未再読込時も動作）"""
    models = _selectable_models()
    fn = getattr(db, "format_user_default_model_label", None)
    if fn:
        return fn(employee_id, models)
    eff = db.get_effective_default_model(employee_id)
    user = db.get_user(employee_id)
    raw = (user.get("default_model") or "").strip() if user else ""
    if raw:
        return models.get(raw, raw)
    return f"グローバル（{models.get(eff, eff)}）"


def _model_display_name(model_id: str) -> str:
    """モデル ID を一覧表示用ラベルに変換"""
    mid = (model_id or "").strip()
    if not mid:
        return "（未指定 — ユーザー/グローバル）"
    if mid == llm.AUTO_MODEL_ID:
        return llm.AUTO_MODEL_LABEL
    info = llm.get_model_registry().get(mid)
    if info:
        return info.get("label", mid)
    return mid


def _log_filter_models() -> dict[str, str]:
    """クエリログ絞り込み用（定義済みモデル名をすべて含む）"""
    return {k: v["label"] for k, v in llm.get_model_registry().items()}

UPLOAD_TYPE_OPTIONS = [
    "jpg", "jpeg", "png", "gif", "webp", "pdf", "txt", "csv", "md",
    "xlsx", "docx", "pptx",
    "mp3", "wav", "aac", "flac", "m4a", "ogg",
]

st.set_page_config(
    page_title="管理者パネル — AI アシスタント",
    page_icon="🔐",
    layout="wide",
    initial_sidebar_state="expanded",
)

db.init_db()
db.ensure_admin_user(ADMIN_PASSWORD)

# ══════════════════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════════════════
st.markdown("""
<style>
[data-testid="stApp"] { background-color:#ffffff; color:#1a1a1a; }
section[data-testid="stSidebar"] { background-color:#f4f6f9; border-right:1px solid #dde1e7; }
section[data-testid="stSidebar"] * { color:#2c2c2c !important; }
""" + ui_common.SIDEBAR_FIX_CSS + """
#MainMenu { visibility:hidden; }
footer { visibility:hidden; }
.stDataFrame { border:1px solid #dde1e7; border-radius:8px; }
.metric-card {
    background:#f0f5ff; border:1px solid #c0d0ee; border-radius:10px;
    padding:14px; text-align:center; margin-bottom:8px;
}
.metric-value { font-size:1.8rem; font-weight:700; color:#1a4f8a; }
.metric-label { font-size:0.78rem; color:#555; margin-top:2px; }
""" + ui_common.BANNER_MEMO_CSS + """
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
# 認証（管理者アカウントが必要）
# ══════════════════════════════════════════════════════════

def check_auth() -> bool:
    if st.session_state.get("admin_user"):
        return True
    ui_common.render_login_page_style()
    ui_common.render_login_banner()
    st.markdown("## 🔐 管理者ログイン")
    with st.form("admin_login"):
        emp_id   = st.text_input("社員番号 / ID", placeholder="管理者アカウント")
        password = st.text_input("パスワード", type="password")
        if st.form_submit_button("ログイン", width="stretch"):
            user = db.authenticate_user(emp_id.strip(), password)
            if user and user["is_admin"]:
                st.session_state.admin_user = user
                st.rerun()
            elif user:
                st.error("管理者権限がありません。")
            else:
                st.error("社員番号またはパスワードが正しくありません。")
    return False


# ══════════════════════════════════════════════════════════
# サイドバー
# ══════════════════════════════════════════════════════════

def render_sidebar() -> str:
    with st.sidebar:
        st.markdown(
            '<div style="font-size:1.2rem;font-weight:700;color:#1a4f8a;padding:10px 0 6px;">🔐 管理者パネル</div>',
            unsafe_allow_html=True,
        )
        admin = st.session_state.admin_user
        st.caption(f"ログイン中: {admin['username']}（{admin['employee_id']}）")
        st.divider()
        page = st.radio(
            "ページ",
            ["📊 ダッシュボード", "📋 クエリログ", "👍 フィードバック",
             "👤 ユーザー管理", "📝 テンプレート管理",
             "🔧 メンテナンスログ", "⚙️ システム設定"],
            label_visibility="collapsed",
        )
        if db.maintenance_mode_enabled():
            st.caption("🛠 メンテナンスモード ON — 実行ログ記録中")
        st.divider()
        st.caption(f"🕐 {datetime.datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')} JST")
        if st.button("🚪 ログアウト", width="stretch"):
            del st.session_state.admin_user
            st.rerun()
    return page


# ══════════════════════════════════════════════════════════
# 📊 ダッシュボード
# ══════════════════════════════════════════════════════════

def page_dashboard() -> None:
    st.markdown("# 📊 ダッシュボード")
    stats = db.get_stats()

    cols = st.columns(5)
    metrics = [
        ("総問い合わせ", f"{stats['total']:,}件", "💬"),
        ("今日の問い合わせ", f"{stats['today']:,}件", "📅"),
        ("登録ユーザー数", f"{stats['user_count']:,}名", "👤"),
        ("総入力Token", f"{stats['input_tokens']:,}", "📥"),
        ("総出力Token", f"{stats['output_tokens']:,}", "📤"),
    ]
    for col, (label, value, icon) in zip(cols, metrics):
        with col:
            st.markdown(
                f'<div class="metric-card">'
                f'<div style="font-size:1.5rem">{icon}</div>'
                f'<div class="metric-value">{value}</div>'
                f'<div class="metric-label">{label}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 🤖 モデル別利用")
        if stats["by_model"]:
            df = pd.DataFrame(stats["by_model"])
            df.columns = ["モデル", "件数"]
            st.dataframe(df, width="stretch", hide_index=True)

    with col2:
        st.markdown("#### 🏢 部署別利用")
        if stats["by_dept"]:
            df = pd.DataFrame(stats["by_dept"])
            df.columns = ["部署", "件数", "総Token"]
            st.dataframe(df, width="stretch", hide_index=True)

    # 日次推移
    st.markdown("#### 📈 日次利用推移（直近14日）")
    daily = db.get_daily_usage_summary(14)
    if daily:
        df_d = pd.DataFrame(daily)
        df_d.columns = ["日付", "件数"]
        df_d = df_d.set_index("日付")
        st.bar_chart(df_d)
    else:
        st.caption("データがありません。")


# ══════════════════════════════════════════════════════════
# 📋 クエリログ
# ══════════════════════════════════════════════════════════

def page_logs() -> None:
    st.markdown("# 📋 クエリログ")

    with st.expander("🔍 絞り込み", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        q_filter    = c1.text_input("問い合わせキーワード", key="lf_q")
        emp_filter  = c2.text_input("社員番号 / 氏名", key="lf_emp")
        dept_filter = c3.text_input("部署", key="lf_dept")
        model_opts  = ["(すべて)"] + list(_log_filter_models().keys())
        m_filter    = c4.selectbox("モデル", model_opts, key="lf_model")
        c5, c6 = st.columns(2)
        date_from = c5.date_input("開始日", value=None, key="lf_from")
        date_to   = c6.date_input("終了日", value=None, key="lf_to")

    model_val    = "" if m_filter == "(すべて)" else m_filter
    date_from_s  = date_from.strftime("%Y-%m-%d") if date_from else ""
    date_to_s    = date_to.strftime("%Y-%m-%d") if date_to else ""

    total = db.get_query_log_count(
        question_filter=q_filter, model_filter=model_val,
        employee_filter=emp_filter, dept_filter=dept_filter,
        date_from=date_from_s, date_to=date_to_s,
    )

    PAGE = 50
    pages = max(1, (total + PAGE - 1) // PAGE)
    if "log_page" not in st.session_state:
        st.session_state.log_page = 1

    inf_col, prev_col, pg_col, nxt_col, exp_col = st.columns([3, 1, 1, 1, 2])
    inf_col.caption(f"全 {total:,} 件 | ページ {st.session_state.log_page}/{pages}")
    if prev_col.button("◀") and st.session_state.log_page > 1:
        st.session_state.log_page -= 1; st.rerun()
    pg_col.caption(f"p.{st.session_state.log_page}")
    if nxt_col.button("▶") and st.session_state.log_page < pages:
        st.session_state.log_page += 1; st.rerun()

    rows = db.get_query_logs(
        limit=PAGE, offset=(st.session_state.log_page - 1) * PAGE,
        question_filter=q_filter, model_filter=model_val,
        employee_filter=emp_filter, dept_filter=dept_filter,
        date_from=date_from_s, date_to=date_to_s,
    )

    if rows:
        all_rows = db.get_query_logs(
            limit=100000, offset=0,
            question_filter=q_filter, model_filter=model_val,
            employee_filter=emp_filter, dept_filter=dept_filter,
            date_from=date_from_s, date_to=date_to_s,
        )
        csv = db.logs_to_csv(all_rows)
        exp_col.download_button(
            "📥 CSV", data=csv.encode("utf-8-sig"),
            file_name=f"logs_{datetime.datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", width="stretch",
        )

    if not rows:
        st.info("ログがありません。"); return

    df = pd.DataFrame(rows)
    show_cols = ["logged_at", "username", "department", "model",
                 "question", "answer", "has_attachment", "used_search",
                 "input_tokens", "output_tokens", "elapsed_ms"]
    show_cols = [c for c in show_cols if c in df.columns]
    rename = {
        "logged_at": "日時(JST)", "username": "氏名", "department": "部署",
        "model": "モデル", "question": "問い合わせ", "answer": "回答",
        "has_attachment": "添付", "used_search": "Web検索",
        "input_tokens": "入力Tok", "output_tokens": "出力Tok", "elapsed_ms": "ms",
    }
    st.dataframe(
        df[show_cols].rename(columns=rename),
        width="stretch", hide_index=True,
        column_config={
            "問い合わせ": st.column_config.TextColumn(width="large"),
            "回答": st.column_config.TextColumn(width="large"),
        },
    )

    # 詳細
    if rows:
        st.markdown("#### 🔎 詳細")
        idx = st.selectbox(
            "行を選択",
            range(len(rows)),
            format_func=lambda i: f"[{rows[i]['logged_at']}] {rows[i].get('username','')} — {rows[i]['question'][:40]}",
            key="log_detail_sel",
        )
        r = rows[idx]
        qa1, qa2 = st.columns(2)
        qa1.markdown("**📨 問い合わせ**")
        qa1.text_area("q", value=r["question"], height=180, disabled=True, label_visibility="collapsed")
        qa2.markdown("**🤖 回答**")
        qa2.text_area("a", value=r["answer"], height=180, disabled=True, label_visibility="collapsed")
        mc = st.columns(6)
        mc[0].metric("日時", r["logged_at"][:16])
        mc[1].metric("氏名", r.get("username", ""))
        mc[2].metric("部署", r.get("department", ""))
        mc[3].metric("入力Token", f"{r.get('input_tokens',0):,}")
        mc[4].metric("出力Token", f"{r.get('output_tokens',0):,}")
        mc[5].metric("処理時間", f"{r.get('elapsed_ms',0):,} ms")


# ══════════════════════════════════════════════════════════
# 👍 フィードバック
# ══════════════════════════════════════════════════════════

def page_feedback() -> None:
    st.markdown("# 👍 フィードバック統計")
    stats = db.get_feedback_stats()

    c1, c2, c3 = st.columns(3)
    c1.metric("総フィードバック", f"{stats['total']:,}件")
    c2.metric("👍 良い", f"{stats['good']:,}件")
    c3.metric("👎 改善必要", f"{stats['bad']:,}件")
    if stats["total"] > 0:
        rate = stats["good"] / stats["total"] * 100
        st.progress(stats["good"] / stats["total"], text=f"満足度: {rate:.1f}%")

    st.markdown("#### 最近のフィードバック")
    if stats["recent"]:
        df = pd.DataFrame(stats["recent"])
        df["rating"] = df["rating"].map({1: "👍 良い", -1: "👎 改善"})
        df.columns = ["日時", "ユーザー", "問い合わせ", "評価"]
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info("フィードバックがまだありません。")


# ══════════════════════════════════════════════════════════
# 👤 ユーザー管理
# ══════════════════════════════════════════════════════════

def _filter_users(
    users: list[dict],
    *,
    emp_query: str = "",
    dept_query: str = "",
    status: str = "すべて",
    role: str = "すべて",
    app_access: str = "すべて",
) -> list[dict]:
    """ユーザー一覧・編集用の絞り込み"""
    emp_q = emp_query.strip().lower()
    dept_q = dept_query.strip().lower()
    filtered: list[dict] = []
    for user in users:
        if emp_q:
            emp_id = str(user.get("employee_id", "")).lower()
            name = str(user.get("username", "")).lower()
            if emp_q not in emp_id and emp_q not in name:
                continue
        if dept_q:
            dept = str(user.get("department", "")).lower()
            if dept_q not in dept:
                continue
        if status == "有効のみ" and not int(user.get("is_active", 0)):
            continue
        if status == "無効のみ" and int(user.get("is_active", 0)):
            continue
        if role == "管理者のみ" and not int(user.get("is_admin", 0)):
            continue
        if role == "一般ユーザーのみ" and int(user.get("is_admin", 0)):
            continue
        if app_access == "NAI許可" and int(user.get("nai_enabled", 1)) == 0:
            continue
        if app_access == "TTS許可" and int(user.get("tts_enabled", 0)) == 0:
            continue
        filtered.append(user)
    return filtered


def _user_filters_active() -> bool:
    """ユーザー絞り込みが有効か"""
    return bool(
        st.session_state.get("uf_emp", "").strip()
        or st.session_state.get("uf_dept", "").strip()
        or st.session_state.get("uf_status", "すべて") != "すべて"
        or st.session_state.get("uf_role", "すべて") != "すべて"
        or st.session_state.get("uf_app", "すべて") != "すべて"
    )


def _clear_user_filters() -> None:
    """ユーザー絞り込み条件をリセット"""
    st.session_state["uf_emp"] = ""
    st.session_state["uf_dept"] = ""
    st.session_state["uf_status"] = "すべて"
    st.session_state["uf_role"] = "すべて"
    st.session_state["uf_app"] = "すべて"
    st.session_state["user_flash"] = "絞り込みを解除しました。"


def page_users() -> None:
    st.markdown("# 👤 ユーザー管理")
    st.caption("アプリごとのタブでユーザーアカウントを一元管理します。各アプリ内のユーザー管理画面からも同じ内容を編集できます。")

    tab_nai, tab_nai2, tab_exam, tab_fback = st.tabs([
        "NAI / TTS",
        "NAI v2",
        "試験 (exam)",
        "アンケート (fback)",
    ], key="unified_user_app_tabs")

    with tab_nai:
        page_users_nai()
    with tab_nai2:
        render_nai_v2_users()
    with tab_exam:
        render_exam_users()
    with tab_fback:
        render_fback_users()


def page_users_nai() -> None:
    st.markdown("#### NAI / TTS — ユーザー管理")
    st.caption("社内 AI アシスタント (NAI) と TTS のログインアカウントを管理します。")

    flash = st.session_state.pop("user_flash", None)
    if flash:
        st.success(flash)
    flash_err = st.session_state.pop("user_flash_error", None)
    if flash_err:
        st.error(flash_err)

    tab_list, tab_add = st.tabs(["ユーザー一覧・編集", "新規ユーザー追加"])

    all_model_keys   = list(_selectable_models().keys())
    all_model_labels = list(_selectable_models().values())

    def _parse_allowed(val: str) -> list[str]:
        return [m.strip() for m in (val or "").split(",") if m.strip()]

    def _models_to_str(selected: list[str]) -> str:
        return ",".join(selected)

    # ─ 一覧 ─
    with tab_list:
        users = db.get_all_users()
        if not users:
            st.info("ユーザーが登録されていません。"); return

        df = pd.DataFrame(users)
        ws_map = {-1: "グローバル設定", 0: "禁止", 1: "許可"}
        if "web_search_enabled" not in df.columns:
            df["web_search_enabled"] = -1
        df["web_search_enabled"] = df["web_search_enabled"].map(ws_map).fillna("グローバル設定")
        if "allowed_models" not in df.columns:
            df["allowed_models"] = ""
        df["allowed_models"] = df["allowed_models"].apply(
            lambda v: "（全て）" if not v else v
        )
        if "upload_max_mb" not in df.columns:
            df["upload_max_mb"] = -1
        df["upload_limit"] = df.apply(
            lambda r: db.format_user_upload_limit(r["employee_id"], r.to_dict()),
            axis=1,
        )
        if "password_change_allowed" not in df.columns:
            df["password_change_allowed"] = 1
        df["password_change_allowed"] = df["password_change_allowed"].apply(
            lambda v: "許可" if int(v or 1) != 0 else "制限"
        )
        if "nai_enabled" not in df.columns:
            df["nai_enabled"] = 1
        if "tts_enabled" not in df.columns:
            df["tts_enabled"] = 0
        df["nai_enabled"] = df["nai_enabled"].apply(
            lambda v: "許可" if int(v or 1) != 0 else "禁止"
        )
        df["tts_enabled"] = df["tts_enabled"].apply(
            lambda v: "許可" if int(v or 0) != 0 else "禁止"
        )
        rename = {
            "employee_id": "社員番号", "username": "氏名", "department": "部署",
            "is_admin": "管理者", "daily_limit": "日次上限",
            "web_search_enabled": "Web検索", "allowed_models": "使用可能モデル",
            "upload_limit": "添付上限", "password_change_allowed": "PW変更",
            "nai_enabled": "NAI", "tts_enabled": "TTS",
            "is_active": "有効", "created_at": "登録日時",
        }
        show = ["employee_id", "username", "department", "is_admin", "daily_limit",
                "web_search_enabled", "allowed_models", "upload_limit",
                "password_change_allowed", "nai_enabled", "tts_enabled",
                "is_active", "created_at"]
        show = [c for c in show if c in df.columns]

        with st.expander("🔍 ユーザー絞り込み", expanded=False):
            uf1, uf2 = st.columns(2)
            uf_emp = uf1.text_input(
                "社員番号 / 氏名",
                key="uf_emp",
                placeholder="部分一致（例: 10001 / 山田）",
            )
            uf_dept = uf2.text_input(
                "部署",
                key="uf_dept",
                placeholder="部分一致",
            )
            uf3, uf4, uf5 = st.columns(3)
            uf_status = uf3.selectbox(
                "アカウント状態",
                ["すべて", "有効のみ", "無効のみ"],
                key="uf_status",
            )
            uf_role = uf4.selectbox(
                "権限",
                ["すべて", "管理者のみ", "一般ユーザーのみ"],
                key="uf_role",
            )
            uf_app = uf5.selectbox(
                "アプリ利用",
                ["すべて", "NAI許可", "TTS許可"],
                key="uf_app",
            )
            st.button(
                "✕ 絞り込みを解除",
                key="btn_clear_user_filters_exp",
                on_click=_clear_user_filters,
                disabled=not _user_filters_active(),
                use_container_width=True,
            )

        filtered_users = _filter_users(
            users,
            emp_query=uf_emp,
            dept_query=uf_dept,
            status=uf_status,
            role=uf_role,
            app_access=uf_app,
        )
        filtered_ids = {u["employee_id"] for u in filtered_users}
        df_view = df[df["employee_id"].isin(filtered_ids)] if filtered_ids else df.iloc[0:0]

        col_list_title, col_list_refresh = st.columns([5, 1])
        with col_list_title:
            st.markdown("#### 📋 ユーザー一覧")
        with col_list_refresh:
            if st.button("🔄 再表示", key="btn_refresh_user_list", width="stretch"):
                st.session_state["user_flash"] = "ユーザー一覧を再読み込みしました。"
                st.session_state["_user_needs_rerun"] = True

        st.dataframe(df_view[show].rename(columns=rename), width="stretch", hide_index=True)
        filter_active = len(filtered_users) < len(users) or _user_filters_active()
        if filter_active:
            cap_col, clear_col = st.columns([5, 1])
            with cap_col:
                st.caption(
                    f"表示 {len(filtered_users)} 件 / 全 {len(users)} 件（絞り込み中）"
                )
            with clear_col:
                st.button(
                    "✕ 絞り込み解除",
                    key="btn_clear_user_filters",
                    on_click=_clear_user_filters,
                    use_container_width=True,
                )
        else:
            st.caption(f"全 {len(users)} 件")

        st.markdown("#### ✏️ ユーザー編集")
        if not filtered_users:
            st.warning("条件に一致するユーザーがありません。絞り込み条件を変更してください。")
            if _user_filters_active():
                st.button(
                    "✕ 絞り込みを解除して全件表示",
                    key="btn_clear_user_filters_empty",
                    on_click=_clear_user_filters,
                )
            return

        emp_options = [u["employee_id"] for u in filtered_users]
        pending_emp = st.session_state.pop("user_edit_sel_pending", None)
        if pending_emp is not None and pending_emp in emp_options:
            st.session_state["edit_emp_sel"] = pending_emp
        elif st.session_state.get("edit_emp_sel") not in emp_options and emp_options:
            st.session_state["edit_emp_sel"] = emp_options[0]

        sel_emp = st.selectbox(
            "編集するユーザー", emp_options,
            format_func=lambda e: (
                f"{e} — {next(u['username'] for u in filtered_users if u['employee_id'] == e)}"
            ),
            key="edit_emp_sel",
        )
        target = next((u for u in filtered_users if u["employee_id"] == sel_emp), None)
        if not target:
            return

        # パスワード参照（管理者のみ・表示ボタンで復号）
        stored_ref = target.get("plain_password", "")
        reveal_key = f"reveal_pw_{sel_emp}"
        if stored_ref:
            if st.button("🔑 パスワードを表示", key=f"btn_{reveal_key}"):
                st.session_state[reveal_key] = True
            if st.session_state.get(reveal_key):
                plain_pw = db.decrypt_password_ref(stored_ref)
                if plain_pw:
                    st.info(f"🔑 現在のパスワード（参照）: `{plain_pw}`")
                else:
                    st.warning("パスワード参照を復号できません。PASSWORD_REF_KEY / ADMIN_PASSWORD を確認してください。")
        else:
            st.caption("🔑 パスワードはユーザー登録後の変更履歴がありません。")

        with st.form("edit_user_form"):
            eu_name   = st.text_input("氏名",  value=target["username"])
            eu_dept   = st.text_input("部署",  value=target.get("department") or "")
            eu_admin  = st.checkbox("管理者権限", value=bool(target["is_admin"]))
            eu_active = st.checkbox("アカウント有効", value=bool(target["is_active"]))
            eu_limit  = st.number_input(
                "日次質問数上限（0=無制限 / -1=グローバル設定に従う）",
                value=int(target["daily_limit"]), min_value=-1, step=1,
            )
            eu_timeout = st.number_input(
                "セッションタイムアウト秒（0=無制限 / -1=グローバル設定）",
                value=int(target.get("session_timeout_sec", -1)),
                min_value=-1, step=60,
                help="未操作で自動ログアウトするまでの秒数。NAI / TTS 共通です。",
            )
            st.caption(
                f"現在の実効タイムアウト: **{db.format_user_session_timeout(sel_emp, target)}**"
            )

            st.markdown("**🌐 Web検索（Grounding）**")
            ws_val = int(target.get("web_search_enabled", -1))
            ws_opts = ["グローバル設定に従う（-1）", "禁止（0）", "許可（1）"]
            ws_sel  = st.radio(
                "Web検索", ws_opts,
                index=ws_val + 1,  # -1→0, 0→1, 1→2
                horizontal=True, key="ws_radio_edit",
                label_visibility="collapsed",
            )
            eu_ws = ws_opts.index(ws_sel) - 1  # 0→-1, 1→0, 2→1

            st.markdown("**🤖 使用可能モデル（空=全て許可）**")
            cur_allowed = _parse_allowed(target.get("allowed_models", ""))
            eu_models = st.multiselect(
                "使用可能モデル", options=all_model_keys,
                default=[m for m in cur_allowed if m in all_model_keys],
                format_func=lambda k: _selectable_models().get(k, k),
                key="model_multi_edit",
                label_visibility="collapsed",
            )

            st.markdown("**🎯 デフォルト LLM（空=グローバル設定）**")
            dm_opts = ["（グローバル設定に従う）"] + all_model_keys
            cur_dm = (target.get("default_model") or "").strip()
            dm_idx = (all_model_keys.index(cur_dm) + 1) if cur_dm in all_model_keys else 0
            eu_dm_sel = st.selectbox(
                "デフォルト LLM", dm_opts,
                index=dm_idx,
                format_func=lambda k: _selectable_models().get(k, k) if k != "（グローバル設定に従う）" else k,
                key="default_model_edit",
                label_visibility="collapsed",
            )
            eu_default_model = "" if eu_dm_sel == "（グローバル設定に従う）" else eu_dm_sel
            st.caption(
                f"チャット画面の初期表示: **{_user_default_model_label(sel_emp)}**"
            )

            st.markdown("**📎 添付ファイル設定**")
            st.caption(
                f"現在の実効上限: **{db.format_user_upload_limit(sel_emp, target)}** "
                "（-1=グローバル設定 / 0=無制限。音声利用時は 100MB 以上を推奨）"
            )
            eu_upload_mb = st.number_input(
                "最大ファイルサイズ MB（-1=グローバル設定 / 0=無制限）",
                value=int(target.get("upload_max_mb", -1)), min_value=-1, step=5,
                key="upload_mb_edit",
            )
            cur_types = [t.strip() for t in (target.get("upload_allowed_types") or "").split(",") if t.strip()]
            eu_upload_types = st.multiselect(
                "許可ファイル形式（空=グローバル設定）",
                options=UPLOAD_TYPE_OPTIONS,
                default=[t for t in cur_types if t in UPLOAD_TYPE_OPTIONS],
                key="upload_types_edit",
            )

            eu_pw = st.text_input("新しいパスワード（変更しない場合は空欄）", type="password")
            eu_pw_change = st.checkbox(
                "利用者によるパスワード変更を許可",
                value=int(target.get("password_change_allowed", 1)) != 0,
                help="オフにするとチャット画面の「パスワードを変更」が非表示になります（テスト公開用アカウント向け）。",
            )

            st.markdown("**🔐 アプリ利用許可**")
            eu_nai = st.checkbox(
                "NAI（社内 AI アシスタント）の利用を許可",
                value=int(target.get("nai_enabled", 1)) != 0,
            )
            eu_tts = st.checkbox(
                "TTS（音声合成ツール）の利用を許可",
                value=int(target.get("tts_enabled", 0)) != 0,
            )
            if eu_admin:
                st.caption("管理者権限を付与すると、保存時に NAI / TTS 両方が許可されます。")

            if st.form_submit_button("💾 保存", width="stretch"):
                nai_flag = 1 if (eu_nai or eu_admin) else 0
                tts_flag = 1 if (eu_tts or eu_admin) else 0
                db.update_user(
                    sel_emp,
                    username=eu_name, department=eu_dept,
                    is_admin=1 if eu_admin else 0,
                    daily_limit=eu_limit,
                    is_active=1 if eu_active else 0,
                    web_search_enabled=eu_ws,
                    allowed_models=_models_to_str(eu_models),
                    default_model=eu_default_model,
                    upload_max_mb=eu_upload_mb,
                    upload_allowed_types=",".join(eu_upload_types),
                    password_change_allowed=1 if eu_pw_change else 0,
                    nai_enabled=nai_flag,
                    tts_enabled=tts_flag,
                    session_timeout_sec=eu_timeout,
                    new_password=eu_pw if eu_pw.strip() else None,
                )
                st.session_state["user_flash"] = (
                    f"✅ ユーザー「{eu_name}」（{sel_emp}）を保存しました。"
                )
                st.session_state["user_edit_sel_pending"] = sel_emp
                st.session_state["_user_needs_rerun"] = True

        if st.session_state.get("user_delete_confirm") not in (None, sel_emp):
            st.session_state.pop("user_delete_confirm", None)

        if st.session_state.get("user_delete_confirm") == sel_emp:
            st.warning(
                f"⚠️ ユーザー **{target['username']}**（ID: {sel_emp}）を削除します。"
                " この操作は取り消せません。よろしいですか？"
            )
            col_ud_ok, col_ud_cancel = st.columns(2)
            if col_ud_ok.button("🗑️ 削除を実行", type="primary", key="btn_delete_user_confirm"):
                deleted_name = target["username"]
                db.delete_user(sel_emp)
                st.session_state.pop("user_delete_confirm", None)
                remaining = [e for e in emp_options if e != sel_emp]
                if remaining:
                    st.session_state["user_edit_sel_pending"] = remaining[0]
                st.session_state["user_flash"] = (
                    f"✅ ユーザー「{deleted_name}」（{sel_emp}）を削除しました。"
                )
                st.session_state["_user_needs_rerun"] = True
            if col_ud_cancel.button("キャンセル", key="btn_delete_user_cancel"):
                st.session_state.pop("user_delete_confirm", None)
                st.session_state["_user_needs_rerun"] = True
        elif st.button(f"🗑️ {sel_emp} を削除", type="secondary", key="btn_delete_user"):
            st.session_state["user_delete_confirm"] = sel_emp
            st.session_state["_user_needs_rerun"] = True

    # ─ 新規追加 ─
    with tab_add:
        with st.form("add_user_form"):
            na_emp   = st.text_input("社員番号 / ID *", placeholder="例: 10001")
            na_name  = st.text_input("氏名 *")
            na_dept  = st.text_input("部署")
            na_pw    = st.text_input("パスワード *", type="password")
            na_admin = st.checkbox("管理者権限")
            na_limit = st.number_input(
                "日次上限（0=無制限 / -1=グローバル設定）",
                value=-1, min_value=-1, step=1,
            )
            na_timeout = st.number_input(
                "セッションタイムアウト秒（0=無制限 / -1=グローバル設定）",
                value=-1, min_value=-1, step=60,
                help="未操作で自動ログアウトするまでの秒数。NAI / TTS 共通です。",
            )
            st.markdown("**🌐 Web検索（Grounding）**")
            na_ws_opts = ["グローバル設定に従う（-1）", "禁止（0）", "許可（1）"]
            na_ws_sel  = st.radio(
                "Web検索", na_ws_opts, index=0, horizontal=True,
                key="ws_radio_add", label_visibility="collapsed",
            )
            na_ws = na_ws_opts.index(na_ws_sel) - 1

            st.markdown("**🤖 使用可能モデル（空=全て）**")
            na_models = st.multiselect(
                "使用可能モデル", options=all_model_keys,
                format_func=lambda k: _selectable_models().get(k, k),
                key="model_multi_add", label_visibility="collapsed",
            )

            st.markdown("**📎 添付ファイル設定**")
            global_upload_mb = int(db.get_setting("upload_max_mb_default") or 50)
            st.caption(
                f"グローバル添付上限: {db.format_upload_limit_mb(global_upload_mb)} "
                "（システム設定で変更）"
            )
            na_upload_mb = st.number_input(
                "最大ファイルサイズ MB（-1=グローバル設定 / 0=無制限）",
                value=-1, min_value=-1, step=5,
                key="upload_mb_add",
            )
            na_upload_types = st.multiselect(
                "許可ファイル形式（空=グローバル設定）",
                options=UPLOAD_TYPE_OPTIONS,
                default=[],
                key="upload_types_add",
            )

            na_pw_change = st.checkbox(
                "利用者によるパスワード変更を許可",
                value=True,
                help="テスト公開用アカウントではオフにしてください。",
            )

            st.markdown("**🔐 アプリ利用許可**")
            na_nai = st.checkbox("NAI（社内 AI アシスタント）の利用を許可", value=True)
            na_tts = st.checkbox("TTS（音声合成ツール）の利用を許可", value=False)

            if st.form_submit_button("➕ ユーザーを追加", width="stretch"):
                if not (na_emp.strip() and na_name.strip() and na_pw.strip()):
                    st.session_state["user_flash_error"] = "社員番号・氏名・パスワードは必須です。"
                    st.session_state["_user_needs_rerun"] = True
                else:
                    ok = db.create_user(
                        employee_id=na_emp.strip(),
                        username=na_name.strip(),
                        department=na_dept.strip(),
                        password=na_pw,
                        is_admin=na_admin,
                        daily_limit=na_limit,
                        web_search_enabled=na_ws,
                        allowed_models=_models_to_str(na_models),
                        upload_max_mb=na_upload_mb,
                        upload_allowed_types=",".join(na_upload_types),
                        password_change_allowed=1 if na_pw_change else 0,
                        nai_enabled=1 if (na_nai or na_admin) else 0,
                        tts_enabled=1 if (na_tts or na_admin) else 0,
                        session_timeout_sec=na_timeout,
                    )
                    if ok:
                        st.session_state["user_flash"] = (
                            f"✅ ユーザー「{na_name.strip()}」（{na_emp.strip()}）を追加しました。"
                        )
                        st.session_state["user_edit_sel_pending"] = na_emp.strip()
                        st.session_state["_user_needs_rerun"] = True
                    else:
                        st.session_state["user_flash_error"] = f"社員番号 {na_emp.strip()} は既に存在します。"
                        st.session_state["_user_needs_rerun"] = True

    if st.session_state.pop("_user_needs_rerun", False):
        st.rerun()


# ══════════════════════════════════════════════════════════
# 📝 テンプレート管理
# ══════════════════════════════════════════════════════════

def _template_kind_options() -> list[tuple[str, str]]:
    return [(k, tmpl_reg.HANDLER_SPECS[k]["label"]) for k in tmpl_reg.ALL_KINDS]


def _render_office_format_select(key: str, current: str = "docx") -> str:
    options = ["docx", "pptx"]
    idx = options.index(current) if current in options else 0
    sel = st.selectbox(
        "出力形式",
        options,
        index=idx,
        format_func=lambda v: "Word（docx）" if v == "docx" else "PowerPoint（pptx）",
        key=key,
    )
    return sel


def _handler_config_for_form(kind: str, output_format: str | None = None) -> str:
    if tmpl_reg.normalize_kind(kind) != tmpl_reg.KIND_OFFICE_OUTPUT:
        return tmpl_reg.handler_config_json({})
    fmt = output_format or "docx"
    return tmpl_reg.handler_config_json({"output_format": fmt})


def _clear_tmpl_edit_widget_keys(template_id: int) -> None:
    """保存後に編集フォームの widget 状態を破棄し DB 値を再表示する"""
    suffix = f"_{template_id}"
    for key in list(st.session_state.keys()):
        if isinstance(key, str) and key.startswith("tmpl_edit_") and key.endswith(suffix):
            st.session_state.pop(key, None)


def page_templates() -> None:
    st.markdown("# 📝 プロンプトテンプレート管理")

    flash = st.session_state.pop("tmpl_flash", None)
    if flash:
        st.success(flash)
    flash_err = st.session_state.pop("tmpl_flash_error", None)
    if flash_err:
        st.error(flash_err)

    tab_list, tab_add, tab_special = st.tabs([
        "テンプレート一覧・編集",
        "新規テンプレート追加",
        "特殊テンプレート作成",
    ])

    with tab_list:
        templates = db.get_all_templates()
        if not templates:
            st.info("テンプレートがありません。")
            return

        df = pd.DataFrame(templates)
        df["default_model_label"] = df["default_model"].apply(
            lambda m: _model_display_name(m or "")
        )
        if "allow_empty_prompt" not in df.columns:
            df["allow_empty_prompt"] = 0
        df["empty_prompt_label"] = df["allow_empty_prompt"].apply(
            lambda v: "可" if int(v or 0) == 1 else "不可",
        )
        if "template_kind" not in df.columns:
            df["template_kind"] = tmpl_reg.KIND_STANDARD
        df["kind_label"] = df["template_kind"].apply(tmpl_reg.kind_label)
        df = df[[
            "id", "name", "kind_label", "category", "default_model_label",
            "empty_prompt_label", "is_active", "sort_order", "created_at",
        ]]
        df.columns = [
            "ID", "名称", "種別", "カテゴリ", "デフォルト LLM",
            "空プロンプト", "有効", "順序", "作成日時",
        ]

        col_list_title, col_list_refresh = st.columns([5, 1])
        with col_list_title:
            st.markdown("#### 📋 テンプレート一覧")
        with col_list_refresh:
            if st.button("🔄 再表示", key="btn_refresh_tmpl_list", width="stretch"):
                st.session_state["tmpl_flash"] = "テンプレート一覧を再読み込みしました。"
                st.session_state["_tmpl_needs_rerun"] = True

        st.dataframe(df, width="stretch", hide_index=True)
        st.caption(f"全 {len(df)} 件（表示順 → ID 昇順）")

        st.markdown("#### ✏️ テンプレート編集")
        valid_ids = [t["id"] for t in templates]
        pending_sel = st.session_state.pop("tmpl_edit_sel_pending", None)
        if pending_sel is not None and pending_sel in valid_ids:
            st.session_state["tmpl_edit_sel"] = pending_sel
        elif st.session_state.get("tmpl_edit_sel") not in valid_ids and valid_ids:
            st.session_state["tmpl_edit_sel"] = valid_ids[0]

        sel_id = st.selectbox(
            "編集するテンプレート",
            valid_ids,
            format_func=lambda i: (
                f"[{i}] {next(t['name'] for t in templates if t['id'] == i)}"
                f" ({tmpl_reg.kind_label(next(t for t in templates if t['id'] == i).get('template_kind'))})"
            ),
            key="tmpl_edit_sel",
        )
        t = next((x for x in templates if x["id"] == sel_id), None)
        if not t:
            st.warning("選択中のテンプレートが見つかりません。")
            return

        cur_kind = tmpl_reg.get_template_kind(t)
        cur_cfg = tmpl_reg.template_handler_config(t)
        kind_keys = [k for k, _ in _template_kind_options()]
        kind_labels = dict(_template_kind_options())
        if tmpl_reg.is_special_kind(cur_kind):
            st.info(
                f"**種別:** {kind_labels.get(cur_kind, cur_kind)} — "
                f"ID `{sel_id}` で特殊処理が紐づいています。"
                " 名称は自由に変更できます。"
            )

        with st.form("edit_tmpl_form", clear_on_submit=False):
            et_id = st.number_input(
                "ID", value=int(t["id"]), min_value=1, step=1,
                key=f"tmpl_edit_id_{sel_id}",
            )
            et_name = st.text_input("名称", value=t["name"], key=f"tmpl_edit_name_{sel_id}")
            et_kind = st.selectbox(
                "種別（template_kind）",
                kind_keys,
                index=kind_keys.index(cur_kind) if cur_kind in kind_keys else 0,
                format_func=lambda k: kind_labels.get(k, k),
                key=f"tmpl_edit_kind_{sel_id}",
                help="特殊処理の種類。通常テンプレートは「通常」を選んでください。",
            )
            et_cat = st.text_input("カテゴリ", value=t["category"], key=f"tmpl_edit_cat_{sel_id}")
            et_office_fmt = cur_cfg.get("output_format", "docx")
            if et_kind == tmpl_reg.KIND_OFFICE_OUTPUT:
                et_office_fmt = _render_office_format_select(
                    f"tmpl_edit_office_fmt_{sel_id}",
                    et_office_fmt,
                )
            et_prompt = st.text_area(
                "システムプロンプト", value=t["system_prompt"], height=200,
                key=f"tmpl_edit_prompt_{sel_id}",
            )
            st.markdown("**🤖 テンプレート用デフォルト LLM（空=ユーザー/グローバル設定）**")
            tmpl_model_keys = list(_selectable_models().keys())
            cur_tm = (t.get("default_model") or "").strip()
            tm_opts = ["（未指定 — ユーザー/グローバル設定）"] + tmpl_model_keys
            tm_idx = (tmpl_model_keys.index(cur_tm) + 1) if cur_tm in tmpl_model_keys else 0
            et_dm_sel = st.selectbox(
                "デフォルト LLM", tm_opts, index=tm_idx,
                format_func=lambda k: _selectable_models().get(k, k) if not k.startswith("（") else k,
                key=f"tmpl_edit_llm_{sel_id}",
                label_visibility="collapsed",
            )
            et_default_model = "" if et_dm_sel.startswith("（") else et_dm_sel
            et_active = st.checkbox("有効", value=bool(t["is_active"]), key=f"tmpl_edit_active_{sel_id}")
            et_allow_empty = st.checkbox(
                "プロンプト入力なしで実行を許可",
                value=bool(int(t.get("allow_empty_prompt") or 0)),
                key=f"tmpl_edit_allow_empty_{sel_id}",
                help="オンにすると、利用者画面でメッセージ未入力でも実行できます。"
                     " 音声テンプレートでは音声添付が別途必要です。",
            )
            et_order = st.number_input(
                "表示順", value=int(t["sort_order"]), step=1,
                key=f"tmpl_edit_order_{sel_id}",
            )
            if st.form_submit_button("保存", width="stretch"):
                new_id = int(et_id)
                if new_id != int(t["id"]) and db.template_id_exists(new_id):
                    st.session_state["tmpl_flash_error"] = f"ID {new_id} は既に使用されています。"
                else:
                    err = db.update_template(
                        sel_id, et_name, et_cat, et_prompt,
                        1 if et_active else 0, int(et_order),
                        default_model=et_default_model,
                        new_id=new_id,
                        allow_empty_prompt=1 if et_allow_empty else 0,
                        template_kind=et_kind,
                        handler_config=_handler_config_for_form(et_kind, et_office_fmt),
                    )
                    if err:
                        st.session_state["tmpl_flash_error"] = err
                    else:
                        st.session_state["tmpl_flash"] = (
                            f"✅ テンプレート「{et_name}」（ID: {new_id}）を更新しました。"
                        )
                        _clear_tmpl_edit_widget_keys(sel_id)
                        if new_id != sel_id:
                            _clear_tmpl_edit_widget_keys(new_id)
                        st.session_state["tmpl_edit_sel_pending"] = new_id
                        st.session_state["_tmpl_needs_rerun"] = True

        if st.session_state.get("tmpl_delete_confirm") not in (None, sel_id):
            st.session_state.pop("tmpl_delete_confirm", None)

        if st.session_state.get("tmpl_delete_confirm") == sel_id:
            st.warning(
                f"⚠️ テンプレート **「{t['name']}」**（ID: {sel_id}）を削除します。"
                " この操作は取り消せません。よろしいですか？"
            )
            col_del_ok, col_del_cancel = st.columns(2)
            if col_del_ok.button("🗑️ 削除を実行", type="primary", key="btn_delete_tmpl_confirm"):
                deleted_name = t["name"]
                db.delete_template(sel_id)
                remaining = [i for i in valid_ids if i != sel_id]
                st.session_state.pop("tmpl_delete_confirm", None)
                if remaining:
                    st.session_state["tmpl_edit_sel_pending"] = remaining[0]
                st.session_state["tmpl_flash"] = (
                    f"✅ テンプレート「{deleted_name}」（ID: {sel_id}）を削除しました。"
                )
                st.session_state["_tmpl_needs_rerun"] = True
            if col_del_cancel.button("キャンセル", key="btn_delete_tmpl_cancel"):
                st.session_state.pop("tmpl_delete_confirm", None)
                st.session_state["_tmpl_needs_rerun"] = True
        elif st.button("🗑️ このテンプレートを削除", type="secondary", key="btn_delete_tmpl"):
            st.session_state["tmpl_delete_confirm"] = sel_id
            st.session_state["_tmpl_needs_rerun"] = True

        if st.button(
            "📋 コピーして下書き作成",
            type="secondary",
            key="btn_copy_tmpl_draft",
            help="内容を複製した新規テンプレートを「有効=OFF」で作成します。",
        ):
            ok, err, new_id = db.copy_template_as_draft(sel_id)
            if ok and new_id:
                st.session_state["tmpl_flash"] = (
                    f"✅ 下書きテンプレートを作成しました（ID: {new_id}）。"
                    " 有効=OFF のため利用者画面には表示されません。"
                )
                st.session_state["tmpl_edit_sel_pending"] = new_id
                st.session_state["_tmpl_needs_rerun"] = True
            else:
                st.session_state["tmpl_flash_error"] = err or "下書きの作成に失敗しました。"
                st.session_state["_tmpl_needs_rerun"] = True

    with tab_add:
        with st.form("add_tmpl_form"):
            at_id = st.number_input(
                "ID（-1 = 自動採番: 最大ID+1）", min_value=-1, step=1, value=-1,
            )
            at_name   = st.text_input("名称 *")
            at_cat    = st.text_input("カテゴリ", value="汎用")
            at_prompt = st.text_area("システムプロンプト *", height=200)
            st.markdown("**🤖 テンプレート用デフォルト LLM（空=ユーザー/グローバル設定）**")
            add_model_keys = list(_selectable_models().keys())
            add_tm_opts = ["（未指定 — ユーザー/グローバル設定）"] + add_model_keys
            at_dm_sel = st.selectbox(
                "デフォルト LLM", add_tm_opts,
                format_func=lambda k: _selectable_models().get(k, k) if not k.startswith("（") else k,
                key="tmpl_add_default_model",
                label_visibility="collapsed",
            )
            at_default_model = "" if at_dm_sel.startswith("（") else at_dm_sel
            at_allow_empty = st.checkbox(
                "プロンプト入力なしで実行を許可",
                value=False,
                key="tmpl_add_allow_empty",
                help="オンにすると、利用者画面でメッセージ未入力でも実行できます。",
            )
            at_order  = st.number_input("表示順", value=99, step=1)
            if st.form_submit_button("追加", width="stretch"):
                if not (at_name.strip() and at_prompt.strip()):
                    st.session_state["tmpl_flash_error"] = "名称とシステムプロンプトは必須です。"
                    st.session_state["_tmpl_needs_rerun"] = True
                else:
                    template_id = int(at_id) if int(at_id) >= 1 else None
                    ok, err, assigned_id = db.create_template(
                        at_name.strip(), at_cat.strip(), at_prompt.strip(),
                        at_order, at_default_model, template_id=template_id,
                        allow_empty_prompt=1 if at_allow_empty else 0,
                    )
                    if ok:
                        st.session_state["tmpl_flash"] = (
                            f"✅ テンプレート「{at_name.strip()}」を追加しました（ID: {assigned_id}）。"
                        )
                        st.session_state["tmpl_edit_sel_pending"] = assigned_id
                        st.session_state["_tmpl_needs_rerun"] = True
                    else:
                        st.session_state["tmpl_flash_error"] = err or "テンプレートを追加できませんでした。"
                        st.session_state["_tmpl_needs_rerun"] = True

    with tab_special:
        st.markdown("#### 🧩 特殊テンプレート作成")
        st.caption(
            "登録済みの処理種別（Excel 集計・分析、音声、画像、Office 出力など）から"
            " 新しいテンプレートを作成します。新しい種別そのものの追加は開発依頼が必要です。"
        )
        wizard_kinds = tmpl_reg.wizard_creatable_kinds()
        kind_values = [k for k, _ in wizard_kinds]
        kind_labels_w = {k: label for k, label in wizard_kinds}

        with st.form("special_tmpl_form"):
            ws_kind = st.selectbox(
                "特殊種別 *",
                kind_values,
                format_func=lambda k: kind_labels_w.get(k, k),
                key="special_tmpl_kind",
            )
            spec = tmpl_reg.HANDLER_SPECS.get(ws_kind, {})
            st.info(spec.get("description", ""))

            ws_name = st.text_input("表示名称 *", key="special_tmpl_name")
            ws_cat = st.text_input(
                "カテゴリ",
                value=spec.get("default_category", "汎用"),
                key="special_tmpl_cat",
            )
            ws_prompt = st.text_area(
                "システムプロンプト *",
                value=spec.get("default_prompt", ""),
                height=200,
                key="special_tmpl_prompt",
            )
            ws_office_fmt = "docx"
            if ws_kind == tmpl_reg.KIND_OFFICE_OUTPUT:
                ws_office_fmt = _render_office_format_select(
                    "special_tmpl_office_fmt", "docx",
                )

            st.markdown("**🤖 テンプレート用デフォルト LLM**")
            sp_model_keys = list(_selectable_models().keys())
            sp_default = spec.get("default_model", "")
            sp_opts = ["（未指定 — ユーザー/グローバル設定）"] + sp_model_keys
            sp_idx = (sp_model_keys.index(sp_default) + 1) if sp_default in sp_model_keys else 0
            ws_dm_sel = st.selectbox(
                "デフォルト LLM", sp_opts, index=sp_idx,
                format_func=lambda k: _selectable_models().get(k, k) if not k.startswith("（") else k,
                key="special_tmpl_llm",
                label_visibility="collapsed",
            )
            ws_default_model = "" if ws_dm_sel.startswith("（") else ws_dm_sel
            ws_allow_empty = st.checkbox(
                "プロンプト入力なしで実行を許可",
                value=bool(spec.get("default_allow_empty_prompt", False)),
                key="special_tmpl_allow_empty",
            )
            ws_order = st.number_input("表示順", value=99, step=1, key="special_tmpl_order")
            ws_active = st.checkbox("有効", value=True, key="special_tmpl_active")

            if st.form_submit_button("特殊テンプレートを作成", width="stretch"):
                if not (ws_name.strip() and ws_prompt.strip()):
                    st.session_state["tmpl_flash_error"] = "表示名称とシステムプロンプトは必須です。"
                    st.session_state["_tmpl_needs_rerun"] = True
                else:
                    cfg = tmpl_reg.default_handler_config(
                        ws_kind,
                        {"output_format": ws_office_fmt}
                        if ws_kind == tmpl_reg.KIND_OFFICE_OUTPUT else None,
                    )
                    ok, err, assigned_id = db.create_special_template(
                        ws_kind,
                        ws_name.strip(),
                        ws_cat.strip(),
                        ws_prompt.strip(),
                        int(ws_order),
                        ws_default_model,
                        allow_empty_prompt=1 if ws_allow_empty else 0,
                        handler_config=cfg,
                        is_active=1 if ws_active else 0,
                    )
                    if ok:
                        st.session_state["tmpl_flash"] = (
                            f"✅ 特殊テンプレート「{ws_name.strip()}」を作成しました"
                            f"（ID: {assigned_id}、種別: {kind_labels_w.get(ws_kind, ws_kind)}）。"
                        )
                        st.session_state["tmpl_edit_sel_pending"] = assigned_id
                        st.session_state["_tmpl_needs_rerun"] = True
                    else:
                        st.session_state["tmpl_flash_error"] = err or "作成に失敗しました。"
                        st.session_state["_tmpl_needs_rerun"] = True

    if st.session_state.pop("_tmpl_needs_rerun", False):
        st.rerun()


# ══════════════════════════════════════════════════════════
# 🔧 メンテナンスログ
# ══════════════════════════════════════════════════════════

def page_maintenance_logs() -> None:
    st.markdown("# 🔧 メンテナンスログ")
    enabled = db.maintenance_mode_enabled()
    log_count = db.count_maintenance_logs()

    if enabled:
        st.warning(
            "メンテナンスモード中です。利用者の AI 実行プロセスが記録されています。"
            " 通常運用に戻すとログはすべて削除されます。"
        )
    else:
        st.info(
            "現在は通常運用です。実行ログの記録は行われません。"
            " システム設定でメンテナンスモードを有効にしてください。"
        )

    st.metric("記録ステップ数", f"{log_count:,} 件")

    traces = db.list_maintenance_traces(100)
    if not traces:
        st.caption("メンテナンスログはまだありません。")
        return

    df = pd.DataFrame(traces)
    df.columns = ["トレースID", "開始", "終了", "社員ID", "セッションID", "ステップ数"]
    st.markdown("#### 実行トレース一覧（直近100件）")
    st.dataframe(df, width="stretch", hide_index=True)

    trace_ids = [t["trace_id"] for t in traces]
    sel = st.selectbox(
        "詳細を表示するトレース",
        trace_ids,
        format_func=lambda tid: next(
            (f"{tid[:8]}… — {t['started_at']} ({t['employee_id'] or '-'})"
             for t in traces if t["trace_id"] == tid),
            tid,
        ),
    )
    if not sel:
        return

    steps = db.get_maintenance_logs_for_trace(sel)
    if not steps:
        st.warning("ステップがありません。")
        return

    st.markdown("#### プロセス詳細")
    for row in steps:
        with st.expander(
            f"{row['logged_at']}  [{row['phase']}] {row['step']}"
            f"  (+{row.get('elapsed_ms', 0)} ms)",
            expanded=(row["step"] in ("trace_start", "trace_end")),
        ):
            detail = (row.get("detail_json") or "").strip()
            if detail:
                try:
                    import json
                    parsed = json.loads(detail)
                    st.json(parsed)
                except Exception:
                    st.code(detail)
            else:
                st.caption("（詳細なし）")


# ══════════════════════════════════════════════════════════
# ⚙️ システム設定
# ══════════════════════════════════════════════════════════

def page_settings() -> None:
    st.markdown("# ⚙️ システム設定")

    # ── 運用モード（即時反映） ──
    st.markdown("#### 🛠 運用モード")
    maint_on = db.maintenance_mode_enabled()
    log_count = db.count_maintenance_logs()
    mode_label = "メンテナンスモード" if maint_on else "通常運用"
    st.caption(
        f"現在: **{mode_label}**"
        + (f"（記録中ログ: {log_count:,} ステップ）" if log_count else "")
    )
    col_m1, col_m2 = st.columns([2, 3])
    with col_m1:
        want_maint = st.toggle(
            "メンテナンスモード",
            value=maint_on,
            help="ON の間、利用者の AI 実行プロセスを検証用ログとして記録します。"
                 " 通常運用に戻すとログはすべて削除されます。",
            key="maint_mode_toggle",
        )
    with col_m2:
        st.caption(
            "メンテナンスモードではモデル選定・Excel 集計・ストリーム生成など"
            " 各フェーズの状態を記録します。"
            " 実行ユーザーの許可モデル・Web検索・添付上限なども記録されます（API キー等はマスク）。"
            " デバッグ後は必ず通常運用に戻してください。"
        )
    if want_maint != maint_on:
        deleted, msg = maintenance_log.set_mode(want_maint)
        if want_maint:
            st.success(msg)
        else:
            st.success(msg)
        st.rerun()

    st.divider()
    st.info("以下の設定はすべてのユーザーのチャットに即時反映されます。")

    # ── LLM 同期（.env 再読み込み） ──
    st.markdown("#### 🔄 LLM 更新")
    st.caption(
        "`.env` の API キー設定を再読み込みし、利用可能な LLM 一覧を更新します。"
        "深夜バッチ（0:30）と同じ処理です。"
    )
    selectable_now = _selectable_models()
    with st.expander(f"現在の利用可能 LLM（{len(selectable_now)} 件）", expanded=False):
        if selectable_now:
            for mid, label in sorted(selectable_now.items()):
                st.text(f"{mid} — {label}")
        else:
            st.warning("利用可能なモデルがありません。")

    if st.button("🔄 LLM 更新", type="primary", key="btn_llm_sync"):
        with st.spinner("同期中..."):
            result = sync_env_job.run_sync()
        if result["ok"]:
            st.success("✅ LLM 更新が完了しました。")
            for line in result["messages"]:
                if "警告" in line or "→" in line or "整理" in line or "クリア" in line or "更新" in line:
                    st.info(line)
                else:
                    st.caption(line)
            updated = result["available"]
            with st.expander(f"更新後の利用可能 LLM（{len(updated)} 件）", expanded=True):
                for mid, label in sorted(updated.items()):
                    st.text(f"{mid} — {label}")
        else:
            st.error("⚠️ LLM 更新に失敗しました。")
            for line in result["messages"]:
                st.warning(line)

    st.divider()

    s = db.get_all_settings()

    with st.form("settings_form"):
        st.markdown("#### 🤖 AI モデル")
        providers = []
        if llm.get_provider_keys("google"):
            providers.append("Google")
        if llm.get_provider_keys("openai"):
            providers.append("OpenAI")
        if llm.get_provider_keys("anthropic"):
            providers.append("Anthropic")
        if llm.provider_available("ollama"):
            providers.append("Ollama(ローカル)")
        st.caption(f"API キー設定済み: {', '.join(providers) or 'なし（.env を確認）'}")
        selectable = _selectable_models()
        model_keys   = list(selectable.keys())
        model_labels = list(selectable.values())
        cur_idx = model_keys.index(s.get("model", model_keys[0])) if s.get("model") in model_keys else 0
        sel_model = st.selectbox("グローバルデフォルトモデル", model_labels, index=cur_idx)

        st.markdown("#### 🎛️ 生成パラメータ")
        col1, col2 = st.columns(2)
        sel_temp = col1.slider("Temperature", 0.0, 2.0, float(s.get("temperature", 0.7)), 0.05)
        sel_maxt = col2.select_slider(
            "最大出力トークン",
            options=[1024, 2048, 4096, 8192, 16384, 32768, 65536],
            value=int(s.get("max_output_tokens", 8192)),
        )

        st.markdown("#### 📋 デフォルトシステムプロンプト")
        sel_sysp = st.text_area("（テンプレート未選択時に使用）",
                                value=s.get("system_prompt", ""), height=140)

        st.markdown("#### 🔒 利用制限")
        col3, col4 = st.columns(2)
        sel_limit = col3.number_input(
            "デフォルト日次上限（0=無制限）",
            value=int(s.get("daily_limit_default", 50)),
            min_value=0, step=5,
        )
        sel_search = col4.checkbox("Web検索（Grounding）をユーザーに開放",
                                   value=s.get("web_search_allowed", "1") == "1")
        sel_timeout = st.number_input(
            "デフォルトセッションタイムアウト（秒 / 0=無制限）",
            value=int(s.get("session_timeout_default", 600)),
            min_value=0, step=60,
            help="未操作で自動ログアウトするまでの秒数。NAI / TTS 共通。ユーザー個別設定が -1 の場合に適用されます。",
        )
        st.caption(
            f"現在のグローバル設定: **{db.format_session_timeout_label(sel_timeout)}**"
        )

        st.markdown("#### 📎 添付ファイル（グローバルデフォルト）")
        st.caption(
            "全ユーザーのデフォルト添付上限です。ユーザー個別設定（-1）の場合に適用されます。"
            " 音声文字起こし・議事録では 100MB 以上、長時間録音では 200MB 以上を推奨します。"
            " 0 = 無制限。"
        )
        col5, col6 = st.columns(2)
        sel_upload_mb = col5.number_input(
            "デフォルト最大サイズ MB（0=無制限）",
            value=int(s.get("upload_max_mb_default", 50)),
            min_value=0, step=5,
        )
        default_types = [t.strip() for t in (s.get("upload_allowed_types_default") or "").split(",") if t.strip()]
        sel_upload_types = col6.multiselect(
            "デフォルト許可形式",
            options=UPLOAD_TYPE_OPTIONS,
            default=[t for t in default_types if t in UPLOAD_TYPE_OPTIONS] or UPLOAD_TYPE_OPTIONS,
        )

        if st.form_submit_button("✅ 設定を保存", width="stretch"):
            selected_model = model_keys[model_labels.index(sel_model)]
            db.set_setting("model",               selected_model)
            db.set_setting("temperature",         str(sel_temp))
            db.set_setting("max_output_tokens",   str(sel_maxt))
            db.set_setting("system_prompt",       sel_sysp)
            db.set_setting("daily_limit_default", str(sel_limit))
            db.set_setting("session_timeout_default", str(sel_timeout))
            db.set_setting("web_search_allowed",  "1" if sel_search else "0")
            db.set_setting("upload_max_mb_default", str(sel_upload_mb))
            db.set_setting("upload_allowed_types_default", ",".join(sel_upload_types))
            st.success("✅ 設定を保存しました。次回リクエストから反映されます。")


# ══════════════════════════════════════════════════════════
# エントリポイント
# ══════════════════════════════════════════════════════════

def main() -> None:
    if not check_auth():
        return

    ui_common.render_sidebar_reopen_fab()
    page = render_sidebar()

    if page == "📊 ダッシュボード":
        page_dashboard()
    elif page == "📋 クエリログ":
        page_logs()
    elif page == "👍 フィードバック":
        page_feedback()
    elif page == "👤 ユーザー管理":
        page_users()
    elif page == "📝 テンプレート管理":
        page_templates()
    elif page == "🔧 メンテナンスログ":
        page_maintenance_logs()
    elif page == "⚙️ システム設定":
        page_settings()


main()
