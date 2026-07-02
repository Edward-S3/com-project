"""NotebookLM スライド画像 → 編集可能 PPTX 変換（Streamlit UI）"""
from __future__ import annotations

import logging
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from converter import convert_pptx

logging.basicConfig(level=logging.INFO)
JST = timezone(timedelta(hours=9))

st.set_page_config(
    page_title="画像PPTX → 編集可能PPTX",
    page_icon="🖼️",
    layout="wide",
)

APP_CSS = """
<style>
.block-container { padding-top: 1.5rem; }
header[data-testid="stHeader"] [data-testid="stToolbar"],
header[data-testid="stHeader"] [data-testid="stDecoration"] { display: none; }
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
</style>
"""
st.markdown(APP_CSS, unsafe_allow_html=True)


def main() -> None:
    st.title("🖼️ NotebookLM スライド → 編集可能 PPTX 変換")
    st.markdown(
        "NotebookLM の Studio 機能が出力した **画像埋め込み型 PPTX** をアップロードすると、"
        "Gemini API で OCR・レイアウト解析を行い、テキストボックスと画像オブジェクトに分離した"
        "**編集可能な PPTX** を生成します。"
    )
    st.caption(
        "※ OCR・画像解析に基づく近似復元です。フォント・配色・レイアウトの完全再現は保証しません。"
    )

    with st.sidebar:
        st.header("使い方")
        st.markdown(
            "1. NotebookLM 出力の `.pptx` をアップロード\n"
            "2. **変換開始** をクリック\n"
            "3. 変換済み PPTX をダウンロード"
        )
        st.info("API キーは `/opt/gemini-ui/.env` の `GOOGLE_API_KEY` を使用します。")

    uploaded = st.file_uploader(
        "入力 PPTX（NotebookLM 出力）",
        type=["pptx"],
        help="各スライドに1枚の画像が埋め込まれた PPTX",
    )

    if not st.button("🚀 変換開始", type="primary", use_container_width=True):
        return
    if uploaded is None:
        st.error("PPTX ファイルをアップロードしてください。")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        input_file = tmp_path / uploaded.name
        input_file.write_bytes(uploaded.getvalue())
        ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        output_file = tmp_path / f"{Path(uploaded.name).stem}_editable_{ts}.pptx"

        progress = st.progress(0.0, text="準備中...")
        status = st.empty()
        started_at = time.monotonic()

        def on_progress(message: str, current: int, total: int) -> None:
            elapsed = int(time.monotonic() - started_at)
            detail = f"{message} （経過 {elapsed}s）"
            status.caption(detail)
            progress.progress(min(current / max(total, 1), 1.0), text=detail)

        try:
            with st.spinner("変換処理中..."):
                report = convert_pptx(input_file, output_file, on_progress=on_progress)
            progress.progress(1.0, text="完了")
        except Exception as exc:
            st.error(f"変換エラー: {exc}")
            return

        if report.fallback_count:
            st.warning(
                f"{report.fallback_count} 枚のスライドでフォールバック（元画像のまま配置）が発生しました。"
            )
        else:
            st.success(f"変換完了（{len(report.slides)} 枚）")

        with st.expander("スライド別ログ", expanded=True):
            for slide in report.slides:
                flag = "⚠ フォールバック" if slide.fallback else "✓"
                st.markdown(
                    f"**スライド {slide.slide_index + 1}** {flag} — "
                    f"テキスト {slide.text_count} / 画像 {slide.image_count}"
                )
                if slide.error:
                    st.caption(f"詳細: {slide.error}")

        pptx_bytes = output_file.read_bytes()
        st.download_button(
            label="📥 編集可能 PPTX をダウンロード",
            data=pptx_bytes,
            file_name=output_file.name,
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            type="primary",
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
