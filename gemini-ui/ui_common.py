"""ui_common.py — チャット / 管理画面共通 UI"""
import os

import streamlit as st
import streamlit.components.v1 as components

APP_DIR = os.path.dirname(os.path.abspath(__file__))

SIDEBAR_FIX_CSS = """
/* ヘッダーは非表示だが、サイドバー折りたたみ時の再表示ボタンは残す */
header[data-testid="stHeader"] {
    visibility: hidden;
    height: 0 !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: visible !important;
    background: transparent !important;
    border: none !important;
    pointer-events: none;
}
header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"],
[data-testid="stExpandSidebarButton"] {
    visibility: visible !important;
    display: flex !important;
    pointer-events: auto !important;
    position: fixed !important;
    top: 0.75rem !important;
    left: 0.75rem !important;
    z-index: 999999 !important;
    background: #fff !important;
    border: 1px solid #ccd0d9 !important;
    border-radius: 8px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08) !important;
}
"""

SIDEBAR_REOPEN_FAB_HTML = """
<style>
#nai-sidebar-fab {
    position: fixed; top: 12px; left: 12px; z-index: 999998;
    width: 38px; height: 38px; border-radius: 8px;
    border: 1px solid #ccd0d9; background: #fff;
    cursor: pointer; font-size: 20px; color: #1a4f8a;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1); line-height: 38px;
    text-align: center; padding: 0;
    display: none;
}
#nai-sidebar-fab:hover { background: #eaf0fb; border-color: #2d7dd2; }
</style>
<button id="nai-sidebar-fab" type="button" title="サイドメニューを表示" aria-label="サイドメニューを表示">☰</button>
<script>
(function() {
    const pdoc = window.parent.document;
    const fab = document.getElementById('nai-sidebar-fab');
    if (!fab) return;

    function isSidebarCollapsed() {
        const sidebar = pdoc.querySelector('section[data-testid="stSidebar"]');
        if (!sidebar) return true;
        return sidebar.getBoundingClientRect().width < 10;
    }

    function updateFab() {
        fab.style.display = isSidebarCollapsed() ? 'block' : 'none';
    }

    fab.onclick = function() {
        const btn = pdoc.querySelector('[data-testid="stExpandSidebarButton"]');
        if (btn) btn.click();
        setTimeout(updateFab, 300);
    };

    updateFab();
    setInterval(updateFab, 800);
})();
</script>
"""

BANNER_MEMO_CSS = """
.memo-box {
    background:#f0f7f0; border:1px solid #7dbf7d; border-radius:10px;
    padding:10px 14px; margin:6px 0 14px; color:#2a5a2a; font-size:0.88rem;
}
"""

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


def render_sidebar_reopen_fab() -> None:
    """サイドバー非表示時に再表示できるフローティングボタン"""
    components.html(SIDEBAR_REOPEN_FAB_HTML, height=0)


def render_login_page_style() -> None:
    """ログイン画面用 CSS を適用"""
    st.markdown(LOGIN_PAGE_CSS, unsafe_allow_html=True)


def _banner_path() -> str | None:
    for ext in ("jpg", "jpeg", "png"):
        path = os.path.join(APP_DIR, f"banner.{ext}")
        if os.path.exists(path):
            return path
    return None


def render_login_banner(max_width: int = 220) -> None:
    """ログイン画面用: コンパクトなバナーのみ（お知らせなし）"""
    banner_path = _banner_path()
    if not banner_path:
        return
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        st.image(banner_path, width=max_width)


def render_banner_and_memo() -> None:
    """チャット画面用: バナー + memo.txt のお知らせ"""
    banner_path = _banner_path()
    if banner_path:
        _, col_img, _ = st.columns([1, 3, 1])
        with col_img:
            st.image(banner_path, width="stretch")

    memo_path = os.path.join(APP_DIR, "memo.txt")
    if os.path.exists(memo_path):
        try:
            with open(memo_path, "r", encoding="utf-8") as f:
                memo = f.read().strip()
            if memo:
                st.markdown(
                    f'<div class="memo-box">📢 {memo.replace(chr(10), "<br>")}</div>',
                    unsafe_allow_html=True,
                )
        except Exception:
            pass
