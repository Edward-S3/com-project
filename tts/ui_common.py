"""ui_common.py — ログイン画面共通 UI"""
import os

import streamlit as st

LOGIN_PAGE_CSS = """
<style>
.login-page-title {
    text-align: center; max-width: 420px; margin: 0 auto 4px; color: #1a4f8a;
}
section.main > div[data-testid="stMainBlockContainer"] h2 {
    text-align: center; max-width: 420px; margin: 0 auto 4px; color: #1a4f8a;
}
section.main div[data-testid="stForm"] {
    max-width: 420px; margin: 8px auto 32px; padding: 28px 32px;
    background: #fff; border: 1px solid #dde1e7; border-radius: 14px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.06);
}
</style>
"""


def render_login_page_style() -> None:
    st.markdown(LOGIN_PAGE_CSS, unsafe_allow_html=True)


def render_login_banner(max_width: int = 220) -> None:
    """NAI と同じバナー画像があれば表示"""
    for base in (os.path.dirname(os.path.abspath(__file__)), "/opt/gemini-ui"):
        for ext in ("png", "jpg", "jpeg"):
            path = os.path.join(base, f"banner.{ext}")
            if os.path.exists(path):
                _, col, _ = st.columns([1, 1.2, 1])
                with col:
                    st.image(path, width=max_width)
                return
