"""アンケート (fback) ユーザー管理 UI"""
from __future__ import annotations

import streamlit as st
import pandas as pd

from . import fback_store as store


def _role_index(role: str) -> int:
    return 0 if role == "creator" else 1


def render_fback_users(*, exclude_user_id: int | None = None) -> None:
    st.markdown("#### アンケートシステム — ユーザー管理")
    st.caption("ログインIDにはメールアドレスを使用します。")

    if store.is_smtp_configured():
        st.success(f"メール送信: 有効（管理画面URL: {store.get_admin_portal_url()}）")
    else:
        st.warning(
            "メール送信: 無効（/opt/exam/.env の SMTP_SERVER / SMTP_USER / SMTP_PASSWORD / EMAIL_FROM を確認）"
        )

    tab_list, tab_add = st.tabs(["ユーザー一覧・編集", "新規ユーザー追加"], key="fback_user_tabs")

    with tab_add:
        with st.form("fback_add_user_form"):
            new_email = st.text_input("ユーザーID（メールアドレス）", placeholder="例: user@example.com")
            new_company = st.text_input("所属名・組織名", placeholder="例: 東京支社")
            new_password = st.text_input("ログインパスワード", type="password")
            send_mail = st.checkbox("登録完了メールを送信する", value=True, key="fback_add_send_mail")
            if st.form_submit_button("ユーザーを登録する", type="primary"):
                ok, msg = store.create_user(new_email, new_company, new_password, "creator")
                if ok:
                    st.success(msg)
                    if send_mail:
                        mail_ok, mail_msg = store.send_user_registration_email(
                            store.normalize_email(new_email),
                            new_password,
                            new_company,
                        )
                        if mail_ok:
                            st.info(mail_msg)
                        else:
                            st.warning(mail_msg)
                    st.rerun()
                else:
                    st.error(msg)

    with tab_list:
        users = store.list_users(exclude_id=exclude_user_id)
        if not users:
            st.info("登録済みのユーザーはいません。")
            return

        df = pd.DataFrame(users)
        df.columns = ["DB ID", "メールアドレス", "所属", "権限", "登録日時"]
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.markdown("##### 📧 登録完了メールの再送信")
        mail_options = {f"{u['username']} ({u['company_name']})": u for u in users}
        mail_label = st.selectbox("再送信先ユーザー", list(mail_options.keys()), key="fback_resend_sel")
        mail_user = mail_options[mail_label]
        resend_password = st.text_input(
            "メールに記載するパスワード（空欄で自動再発行）",
            type="password",
            key="fback_resend_pw",
        )
        if st.button("📧 登録完了メールを再送信", key="fback_resend_btn"):
            password_for_mail = resend_password.strip() or store.generate_random_password()
            if not resend_password.strip():
                store.update_password(mail_user["id"], password_for_mail)
                st.info("新しいパスワードを自動生成し、アカウントに反映しました。")
            mail_ok, mail_msg = store.send_user_registration_email(
                mail_user["username"], password_for_mail, mail_user["company_name"]
            )
            if mail_ok:
                st.success(mail_msg)
            else:
                st.error(mail_msg)

        st.divider()
        options = {
            f"{u['username']} ({u['company_name']} - {u['role']})": u for u in users
        }
        selected_label = st.selectbox("編集するユーザー", list(options.keys()), key="fback_edit_sel")
        selected = options[selected_label]

        with st.form("fback_edit_user_form"):
            edit_company = st.text_input("所属名・組織名", value=selected["company_name"])
            edit_role = st.selectbox(
                "アカウント権限",
                ["一般作成者 (creator)", "システム管理者 (admin)"],
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
                    key=f"fback_del_confirm_{selected['id']}",
                )
                if st.button("ユーザーを削除する", type="primary", key="fback_delete_btn"):
                    if not confirm:
                        st.error("確認チェックボックスをオンにしてください。")
                    else:
                        ok, msg = store.delete_user(selected["id"])
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
