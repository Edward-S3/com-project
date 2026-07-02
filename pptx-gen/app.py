"""AI駆動型 PPTX 自動生成 — Streamlit メインアプリ（V5）"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DEFAULT_SLIDE_COUNT, MAX_SLIDE_COUNT, MIN_SLIDE_COUNT, OUTPUT_DIR, SUPPORTED_EXTENSIONS
from src.document_parser import DocumentParseError, extract_multiple
from src.gemini_client import GeminiClientError, generate_presentation_data
from src.pptx_creator import build_pptx
from src.qa_validator import try_reduce_overflow, validate_pptx_bytes

JST = timezone(timedelta(hours=9))

st.set_page_config(
    page_title="PPTX 自動生成",
    page_icon="📊",
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


def safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", (title or "presentation").strip())
    return name[:80] or "presentation"


def main() -> None:
    st.title("📊 AI駆動型プレゼンテーション資料（PPTX）自動生成")
    st.markdown(
        "ソース資料と利用目的から、Gemini が**章立て目次 → 章ごと分割生成**でスライド JSON を設計し、"
        "python-pptx で高品質 PPTX を生成します。"
    )
    st.caption("V5: 分割生成・章扉スライド・Unicode絵文字アイコン・情報圧縮禁止")

    with st.sidebar:
        st.header("使い方")
        st.markdown(
            "1. ソースファイルをアップロード\n"
            "2. 利用目的を入力\n"
            "3. 目標スライド枚数を調整\n"
            "4. **生成開始**"
        )
        slide_count = st.slider(
            "目標スライド枚数", min_value=MIN_SLIDE_COUNT, max_value=MAX_SLIDE_COUNT, value=DEFAULT_SLIDE_COUNT,
        )
        st.caption("対応形式: PDF, TXT, MD, DOCX, PPTX, XLSX")

    col1, col2 = st.columns([1, 1])
    with col1:
        uploaded = st.file_uploader(
            "ソースドキュメント（複数選択可）",
            type=[ext.lstrip(".") for ext in sorted(SUPPORTED_EXTENSIONS)],
            accept_multiple_files=True,
        )
    with col2:
        user_prompt = st.text_area(
            "スライド生成目的",
            height=180,
            placeholder="例: 新入社員向け AI 活用研修。30分の社内勉強会向け。",
        )

    generate = st.button("🚀 生成開始", type="primary", use_container_width=True)

    if not generate:
        return
    if not uploaded:
        st.error("ソースファイルを1つ以上アップロードしてください。")
        return
    if not user_prompt.strip():
        st.error("スライド生成目的を入力してください。")
        return

    files = [(f.name, f.getvalue()) for f in uploaded]
    presentation = None
    pptx_bytes = None
    validation_warnings: list[str] = []
    out_name = ""
    gen_result = None

    try:
        with st.spinner("テキスト抽出中..."):
            source_text = extract_multiple(files)

        progress_bar = st.progress(0.0, text="準備中...")
        status_lines: list[str] = []

        def on_progress(message: str, current: int, total: int) -> None:
            status_lines.append(message)
            ratio = min(current / max(total, 1), 1.0)
            progress_bar.progress(ratio, text=message)

        with st.status("Gemini で分割生成中...", expanded=True) as status:
            gen_result = generate_presentation_data(
                source_text,
                user_prompt,
                slide_count=slide_count,
                on_progress=on_progress,
            )
            for line in status_lines:
                status.write(line)
            status.update(label="分割生成が完了しました", state="complete")

        progress_bar.progress(1.0, text="生成完了")
        presentation = gen_result.data
        validation_warnings = gen_result.validation_warnings
        if gen_result.retries_used > 0:
            st.info(f"検証リトライ: {gen_result.retries_used} 回")

        if validation_warnings:
            st.warning("一部の検証項目を満たせませんでした。可能な範囲で生成を続行します。")
            for w in validation_warnings[:8]:
                st.caption(f"⚠ {w}")

        with st.spinner("スライド描画中..."):
            pptx_bytes = build_pptx(presentation)

        with st.spinner("品質検証（QA）中..."):
            qa = validate_pptx_bytes(pptx_bytes)
            if not qa.ok:
                pptx_bytes = try_reduce_overflow(pptx_bytes, qa.warnings)

        ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        out_name = f"{safe_filename(presentation.title)}_{ts}.pptx"
        (OUTPUT_DIR / out_name).write_bytes(pptx_bytes)

        st.success(f"プレゼンテーション「{presentation.title}」を生成しました。（{len(presentation.slides)} 枚）")

        if gen_result.outline:
            with st.expander("章構成（目次）", expanded=False):
                for ch in gen_result.outline.chapters:
                    st.markdown(
                        f"**第{ch.chapter_number}章: {ch.chapter_title}** "
                        f"（{ch.estimated_slides} 枚） — {ch.source_context_focus}"
                    )

        with st.expander("生成内容のプレビュー", expanded=True):
            t = presentation.theme
            st.markdown(
                f"**パレット:** {t.palette_name} "
                f"({t.dominant_color} / {t.support_color} / {t.accent_color} / alert {t.alert_color})"
            )
            for slide in presentation.slides:
                bullets = "\n".join(f"- {b}" for b in slide.bullet_points)
                vis = ""
                if slide.grid_cells:
                    vis = "\n".join(
                        f"  📦 **{c.header}**: " + " / ".join(c.body[:2])
                        for c in slide.grid_cells
                    )
                elif slide.visual:
                    v = slide.visual
                    vis = f"\n🎨 `{v.kind}`"
                    if v.stat_value:
                        vis += f" — {v.stat_value} / {v.stat_label}"
                    if v.icon_items:
                        vis += "\n" + "\n".join(
                            f"  • {it.icon} {it.header}: {it.body[:30]}" for it in v.icon_items[:3]
                        )
                layout_badge = f"`{slide.layout_type}`"
                if slide.layout_type == "CHAPTER_TITLE":
                    layout_badge = f"📖 `{slide.layout_type}`"
                st.markdown(
                    f"**{slide.slide_number}. {slide.title}** {layout_badge} — _{slide.key_message}_\n{bullets}{vis}"
                )

        st.download_button(
            label="📥 PPTX をダウンロード",
            data=pptx_bytes,
            file_name=out_name,
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            type="primary",
            use_container_width=True,
        )

    except DocumentParseError as exc:
        st.error(str(exc))
    except GeminiClientError as exc:
        st.error(f"AI 処理エラー: {exc}")
        if exc.partial and pptx_bytes is None:
            st.warning("部分的なデータで PPTX を生成します。")
            for issue in exc.issues[:8]:
                st.caption(f"⚠ {issue}")
            try:
                pptx_bytes = build_pptx(exc.partial)
                out_name = f"{safe_filename(exc.partial.title)}_partial.pptx"
                st.download_button(
                    label="📥 部分生成 PPTX をダウンロード",
                    data=pptx_bytes,
                    file_name=out_name,
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
            except Exception as inner:
                st.error(f"部分生成にも失敗: {inner}")
    except Exception as exc:
        st.error(f"予期しないエラー: {exc}")


if __name__ == "__main__":
    main()
