import os
import time

import streamlit as st
from dotenv import load_dotenv

from access_log_store import (
    access_logs_to_csv,
    clear_access_log,
    get_access_log_count,
    get_access_logs,
    init_access_log,
)

load_dotenv()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "nakaboshi_admin0123")

st.set_page_config(page_title="FAQボット アクセス履歴", page_icon="📊", layout="wide")
init_access_log()

st.title("📊 FAQボット アクセス履歴")
st.caption("質問のアクセス日時(JST)・IP・PC名・入力内容を参照・管理します。")

if "admin_authenticated" not in st.session_state:
    st.session_state.admin_authenticated = False

if not st.session_state.admin_authenticated:
    pw = st.text_input("管理者パスワード", type="password")
    if st.button("ログイン", type="primary"):
        if pw == ADMIN_PASSWORD:
            st.session_state.admin_authenticated = True
            st.rerun()
        else:
            st.error("パスワードが正しくありません。")
    st.stop()

with st.sidebar:
    if st.button("ログアウト", use_container_width=True):
        st.session_state.admin_authenticated = False
        st.rerun()

    st.divider()
    st.subheader("絞り込み")
    ip_filter = st.text_input("IPアドレス", placeholder="部分一致")
    hostname_filter = st.text_input("PC名", placeholder="部分一致")
    question_filter = st.text_input("質問", placeholder="部分一致")
    access_limit = st.selectbox("表示件数", [50, 100, 200, 500], index=1)

col_page, col_refresh = st.columns([1, 1])
with col_page:
    access_page = st.number_input("ページ", min_value=1, value=1, step=1)
with col_refresh:
    if st.button("🔄 再読み込み", use_container_width=True):
        st.rerun()

total_access = get_access_log_count(ip_filter, hostname_filter, question_filter)
max_page = max(1, (total_access + access_limit - 1) // access_limit)
if access_page > max_page:
    access_page = max_page

access_offset = (access_page - 1) * access_limit
access_logs = get_access_logs(
    limit=access_limit,
    offset=access_offset,
    ip_filter=ip_filter,
    hostname_filter=hostname_filter,
    question_filter=question_filter,
)

start = access_offset + 1 if total_access else 0
end = min(access_offset + access_limit, total_access)
st.caption(f"全 {total_access} 件（{start}〜{end} 件目を表示 / {max_page} ページ）")

if access_logs:
    st.dataframe(
        [
            {
                "アクセス日時(JST)": row[0],
                "IP": row[1],
                "PC名": row[2] or "",
                "質問": row[3],
            }
            for row in access_logs
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        "📥 CSVダウンロード（表示中のページ）",
        data=access_logs_to_csv(access_logs),
        file_name="access_log.csv",
        mime="text/csv",
    )
else:
    st.info("条件に一致するアクセス履歴はありません。")

st.divider()
if st.button("🧹 アクセス履歴をすべて削除", type="secondary"):
    clear_access_log()
    st.success("アクセス履歴を削除しました")
    time.sleep(1)
    st.rerun()
