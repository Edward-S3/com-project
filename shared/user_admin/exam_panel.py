"""試験 (exam) ユーザー管理 UI"""
from __future__ import annotations

import streamlit as st
import pandas as pd

from . import exam_store as store

ROLE_LABELS = {
    "creator": "一般問題作成者 (creator)",
    "admin": "システム管理者 (admin)",
}


def _role_index(role: str) -> int:
    return 0 if role == "creator" else 1


def render_exam_users(*, exclude_user_id: int | None = None) -> None:
    st.markdown("#### 試験システム — ユーザー管理")
    st.caption("ログインID（username）と所属で問題作成者・管理者アカウントを管理します。")

    tab_list, tab_add = st.tabs(["ユーザー一覧・編集", "新規ユーザー追加"], key="exam_user_tabs")

    with tab_add:
        with st.form("exam_add_user_form"):
            new_username = st.text_input("ログインID（ユーザー名）", placeholder="例: user_tokyo")
            new_company = st.text_input("所属名・組織名", placeholder="例: 東京支社開発チーム")
            new_password = st.text_input("パスワード", type="password")
            new_role = st.selectbox(
                "アカウント権限",
                ["一般問題作成者 (creator)", "システム管理者 (admin)"],
            )
            if st.form_submit_button("ユーザーを登録する", type="primary"):
                role = "creator" if new_role.startswith("一般") else "admin"
                ok, msg = store.create_user(new_username, new_company, new_password, role)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    with tab_list:
        users = store.list_users(exclude_id=exclude_user_id)
        if not users:
            st.info("登録済みのユーザーはいません。")
            return

        df = pd.DataFrame(users)
        df.columns = ["DB ID", "ログインID", "所属名", "権限", "登録日時"]
        st.dataframe(df, use_container_width=True, hide_index=True)

        options = {
            f"{u['username']} ({u['company_name']} - {u['role']})": u for u in users
        }
        selected_label = st.selectbox("編集するユーザー", list(options.keys()), key="exam_edit_sel")
        selected = options[selected_label]

        with st.form("exam_edit_user_form"):
            st.write(f"DB ID: **{selected['id']}** | ログインID: **{selected['username']}**")
            edit_company = st.text_input("所属名・組織名", value=selected["company_name"])
            edit_role = st.selectbox(
                "アカウント権限",
                ["一般問題作成者 (creator)", "システム管理者 (admin)"],
                index=_role_index(selected["role"]),
            )
            edit_password = st.text_input("新しいパスワード（変更する場合のみ）", type="password")
            if st.form_submit_button("変更を保存する", type="primary"):
                role = "creator" if edit_role.startswith("一般") else "admin"
                ok, msg = store.update_user(
                    selected["id"],
                    edit_company,
                    role,
                    edit_password.strip() or None,
                )
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

        with st.expander("⚠️ このユーザーを削除する"):
            if selected["role"] == "admin" and selected["username"] == "admin":
                st.warning("組み込み admin アカウントは削除できません。")
            else:
                confirm = st.checkbox(
                    f"「{selected['username']}」の削除を承諾します",
                    key=f"exam_del_confirm_{selected['id']}",
                )
                if st.button("ユーザーを削除する", type="primary", key="exam_delete_btn"):
                    if not confirm:
                        st.error("確認チェックボックスをオンにしてください。")
                    else:
                        ok, msg = store.delete_user(selected["id"])
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
