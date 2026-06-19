"""NAI v2 (gemini-ui2) ユーザー管理 UI — 統合管理者パネル用"""
from __future__ import annotations

import importlib
import sys

import pandas as pd
import streamlit as st

from .constants import NAI2_ROOT


def _load_nai2_db():
    if NAI2_ROOT not in sys.path:
        sys.path.insert(0, NAI2_ROOT)
    db_mod = importlib.import_module("db")
    db_mod.init_db()
    return db_mod


def render_nai_v2_users() -> None:
    st.markdown("#### NAI v2 — ユーザー管理")
    st.caption(
        f"データベース: `{NAI2_ROOT}/gemini_ui.db` "
        "（NAI v1 とは別 DB です。両方のタブで個別に管理してください。）"
    )

    db = _load_nai2_db()
    tab_list, tab_add = st.tabs(["ユーザー一覧・編集", "新規ユーザー追加"], key="nai2_user_tabs")

    with tab_list:
        users = db.get_all_users()
        if not users:
            st.info("ユーザーが登録されていません。")
            return

        df = pd.DataFrame(users)
        show_cols = [
            c for c in [
                "employee_id", "username", "department", "is_admin",
                "nai_enabled", "tts_enabled", "is_active", "created_at",
            ] if c in df.columns
        ]
        rename = {
            "employee_id": "社員番号",
            "username": "氏名",
            "department": "部署",
            "is_admin": "管理者",
            "nai_enabled": "NAI",
            "tts_enabled": "TTS",
            "is_active": "有効",
            "created_at": "登録日時",
        }
        st.dataframe(df[show_cols].rename(columns=rename), use_container_width=True, hide_index=True)

        emp_options = [u["employee_id"] for u in users]
        sel_emp = st.selectbox(
            "編集するユーザー",
            emp_options,
            format_func=lambda e: f"{e} — {next(u['username'] for u in users if u['employee_id'] == e)}",
            key="nai2_edit_emp",
        )
        target = next(u for u in users if u["employee_id"] == sel_emp)

        stored_ref = target.get("plain_password", "")
        if stored_ref and hasattr(db, "decrypt_password_ref"):
            if st.button("🔑 パスワードを表示", key=f"nai2_reveal_{sel_emp}"):
                plain = db.decrypt_password_ref(stored_ref)
                if plain:
                    st.info(f"現在のパスワード（参照）: `{plain}`")

        with st.form("nai2_edit_user_form"):
            eu_name = st.text_input("氏名", value=target["username"])
            eu_dept = st.text_input("部署", value=target.get("department") or "")
            eu_admin = st.checkbox("管理者権限", value=bool(target.get("is_admin")))
            eu_active = st.checkbox("アカウント有効", value=bool(target.get("is_active")))
            eu_nai = st.checkbox("NAI v2 利用を許可", value=int(target.get("nai_enabled", 1)) != 0)
            eu_tts = st.checkbox("TTS 利用を許可", value=int(target.get("tts_enabled", 0)) != 0)
            eu_pw = st.text_input("新しいパスワード（変更しない場合は空欄）", type="password")
            if st.form_submit_button("💾 保存", type="primary"):
                nai_flag = 1 if (eu_nai or eu_admin) else 0
                tts_flag = 1 if (eu_tts or eu_admin) else 0
                db.update_user(
                    sel_emp,
                    username=eu_name,
                    department=eu_dept,
                    is_admin=1 if eu_admin else 0,
                    is_active=1 if eu_active else 0,
                    nai_enabled=nai_flag,
                    tts_enabled=tts_flag,
                    new_password=eu_pw.strip() or None,
                )
                st.success(f"ユーザー「{eu_name}」（{sel_emp}）を保存しました。")
                st.rerun()

        if sel_emp != "admin":
            with st.expander("⚠️ このユーザーを削除する"):
                confirm = st.checkbox(
                    f"「{sel_emp}」の削除を承諾します",
                    key=f"nai2_del_confirm_{sel_emp}",
                )
                if st.button("🗑️ 削除", type="primary", key="nai2_del_btn"):
                    if confirm:
                        db.delete_user(sel_emp)
                        st.success(f"ユーザー {sel_emp} を削除しました。")
                        st.rerun()
                    else:
                        st.error("確認チェックボックスをオンにしてください。")

    with tab_add:
        with st.form("nai2_add_user_form"):
            na_emp = st.text_input("社員番号 / ID *", placeholder="例: 10001")
            na_name = st.text_input("氏名 *")
            na_dept = st.text_input("部署")
            na_pw = st.text_input("パスワード *", type="password")
            na_admin = st.checkbox("管理者権限")
            na_nai = st.checkbox("NAI v2 利用を許可", value=True)
            na_tts = st.checkbox("TTS 利用を許可", value=False)
            if st.form_submit_button("➕ ユーザーを追加", type="primary"):
                if not (na_emp.strip() and na_name.strip() and na_pw.strip()):
                    st.error("社員番号・氏名・パスワードは必須です。")
                else:
                    ok = db.create_user(
                        employee_id=na_emp.strip(),
                        username=na_name.strip(),
                        department=na_dept.strip(),
                        password=na_pw,
                        is_admin=na_admin,
                        nai_enabled=1 if (na_nai or na_admin) else 0,
                        tts_enabled=1 if (na_tts or na_admin) else 0,
                    )
                    if ok:
                        st.success(f"ユーザー「{na_name.strip()}」（{na_emp.strip()}）を追加しました。")
                        st.rerun()
                    else:
                        st.error(f"社員番号 {na_emp.strip()} は既に存在します。")
