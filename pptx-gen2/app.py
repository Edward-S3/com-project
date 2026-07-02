"""Streamlit エントリーポイント — マルチLLM AI資料生成アプリ。"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from config.design_system import palette_choices
from core.cost_estimator import (
    build_scenarios,
    estimate_cost,
    fit_budget,
    prepare_sources_for_estimate,
)
from core.file_ingest import ingest_files
from core.slide_planner import recommend_locally
from core.llm_clients import LLMClientManager
from core.model_registry import list_gemini_models, list_models_for_provider
from core.orchestrator import DISPLAY_NAMES, Orchestrator
from core.pipeline import PipelineRunner
from core.web_source_ingest import parse_urls

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def ensure_env() -> None:
    if not ENV_PATH.exists() and ENV_EXAMPLE.exists():
        shutil.copy(ENV_EXAMPLE, ENV_PATH)
    load_dotenv(ENV_PATH)


def init_session() -> None:
    defaults = {
        "runner": PipelineRunner(),
        "estimate_done": False,
        "selected_scenario": None,
        "estimated_cost": 0.0,
        "scenarios": [],
        "web_cache": {},
        "sources_cache": [],
        "warnings": [],
        "manual_overrides": {},
        "model_overrides": {},
        "gemini_models_cache": [],
        "pricing_rates": {},
        "task_routing": {},
        "budget_lever_priority": ["reduce_qa", "compress_slides", "template_only"],
        "cost_over_confirm": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def load_json_config(path: Path) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json_config(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


STEP_LABELS = {
    "ingest": "ファイル取り込み",
    "content_synthesis": "内容統合",
    "slide_planning": "構成設計",
    "slide_structure_planning": "構成設計",
    "payload": "スライド構造化",
    "structured_json_payload": "スライド構造化",
    "narrative": "スピーカーノート生成",
    "japanese_narrative": "スピーカーノート生成",
    "render": "スライド描画",
    "slide_layout_code_generation": "スライド描画",
    "qa": "QAチェック",
    "design_visual_qa": "QAチェック",
    "file_parsing_text": "ファイル解析",
    "audio_video_understanding": "音声/動画理解",
}

TASK_LABELS = {
    "file_parsing_text": "ファイル解析",
    "audio_video_understanding": "音声/動画理解",
    "content_synthesis": "内容統合",
    "slide_structure_planning": "構成設計",
    "structured_json_payload": "JSON生成",
    "japanese_narrative": "日本語ナレーション",
    "slide_layout_code_generation": "レイアウトコード生成",
    "design_visual_qa": "デザインQA",
}

STATUS_META = {
    "idle": ("待機中", "gray", "ソースを入力して「生成開始」を押してください。"),
    "running": ("処理中", "blue", "バックグラウンドで生成しています。ページを閉じないでください。"),
    "completed": ("完了", "green", "PowerPoint の生成が完了しました。"),
    "cancelled": ("キャンセル", "orange", "処理はユーザーにより中断されました。"),
    "error": ("エラー", "red", "処理中にエラーが発生しました。"),
}


def _format_elapsed(started_at: float) -> str:
    if not started_at:
        return "—"
    sec = int(time.time() - started_at)
    m, s = divmod(sec, 60)
    return f"{m}分{s:02d}秒" if m else f"{s}秒"


def render_status_panel(state, runner: PipelineRunner, estimated_cost: float) -> bool:
    """画面上部に処理状態を常時表示。処理中なら True を返す。"""
    status = state.status or "idle"
    label, _, hint = STATUS_META.get(status, ("不明", "gray", ""))
    step_label = STEP_LABELS.get(state.current_step, state.current_step or "—")

    if status == "running":
        st.markdown(
            f"### 🔄 **{label}** — ジョブ `{state.job_id or '—'}` | 経過 {_format_elapsed(state.started_at)}"
        )
    elif status == "completed":
        st.markdown(f"### ✅ **{label}** — ジョブ `{state.job_id or '—'}`")
    elif status == "error":
        st.markdown(f"### ❌ **{label}** — ジョブ `{state.job_id or '—'}`")
    elif status == "cancelled":
        st.markdown(f"### ⏹ **{label}** — ジョブ `{state.job_id or '—'}`")
    else:
        st.markdown("### ⚪ **待機中** — 生成は実行されていません")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("状態", label)
    c2.metric("現在の工程", step_label if status == "running" else "—")
    c3.metric("使用モデル", state.current_model or "—")
    c4.metric("実コスト / 見積", f"${state.actual_cost:.4f} / ${estimated_cost:.4f}")

    if state.task_routing_live:
        st.caption("工程別LLM（確定済み）")
        rows = []
        for task, info in state.task_routing_live.items():
            label = TASK_LABELS.get(task, task)
            rows.append(
                f"- **{label}**: {info.get('display', '—')} (`{info.get('model', '—')}`) "
                f"[{info.get('mode', '—')}]"
            )
        st.markdown("\n".join(rows))

    if status == "running":
        st.progress(max(0.0, min(1.0, state.progress)), text=f"{int(state.progress * 100)}%")
        if state.message:
            st.info(state.message)
        else:
            st.info(hint)
        if state.cost_paused:
            st.warning("想定より費用がかさんでいます。「続行する」を押すまで一時停止中です。")
            if st.button("続行する", key="cost_continue_top"):
                runner.confirm_cost_continue()
        if st.button("キャンセル（処理を中断）", type="primary", key="cancel_top"):
            runner.cancel()
            st.rerun()
    elif status == "idle":
        st.caption(hint)
    elif status == "completed":
        st.success(hint)
    elif status == "cancelled":
        st.warning(state.message or hint)
    elif status == "error":
        st.error(state.error or state.message or hint)

    st.divider()
    return status == "running"


def has_any_source(files, yt_text, web_text) -> bool:
    return bool(files) or bool(parse_urls(yt_text)) or bool(parse_urls(web_text))


def perform_cost_estimate(
    uploaded,
    yt_text: str,
    web_text: str,
    *,
    orchestrator: Orchestrator,
    use_recommend: bool,
    min_slides: int,
    max_slides: int,
) -> bool:
    """コスト見積もりを実行。成功時 True。"""
    if not has_any_source(uploaded, yt_text, web_text):
        return False
    temp_dir = ROOT / "temp_uploads" / "estimate"
    temp_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in uploaded or []:
        p = temp_dir / f.name
        p.write_bytes(f.getvalue())
        paths.append(p)
    file_sources = ingest_files(paths)
    yt_urls = parse_urls(yt_text)
    web_urls = parse_urls(web_text)
    sources = prepare_sources_for_estimate(
        file_sources,
        [u for u in yt_urls if "youtube" in u or "youtu.be" in u],
        web_urls,
        st.session_state.web_cache,
    )
    st.session_state.sources_cache = sources
    if use_recommend:
        plan = recommend_locally(sources)
        slide_count = plan.recommended_slides
        st.session_state.plan_rationale = plan.rationale
    else:
        slide_count = int((min_slides + max_slides) / 2)
    base = estimate_cost(sources, slide_count, orchestrator)
    scenarios = build_scenarios(base, orchestrator, sources, int(min_slides))
    st.session_state.scenarios = scenarios
    st.session_state.estimated_cost = base.total_usd
    st.session_state.estimate_done = True
    st.session_state.slide_count_est = slide_count
    st.session_state.estimate_warnings = base.warnings
    if scenarios and not st.session_state.selected_scenario:
        st.session_state.selected_scenario = scenarios[0]
    return True


def inject_bright_theme() -> None:
    st.markdown(
        """
<style>
    .stApp { background: linear-gradient(180deg, #f0f9ff 0%, #ffffff 40%); }
    [data-testid="stSidebar"] { background: #e8f7fc; }
    h1, h2, h3 { color: #0077B6 !important; }
    .stMetric label { color: #028090 !important; }
</style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar(runner: PipelineRunner, llm: LLMClientManager, is_running: bool) -> None:
    st.sidebar.header("工程別LLM設定")

    if llm.available.get("gemini") and not st.session_state.gemini_models_cache:
        st.session_state.gemini_models_cache = list_gemini_models(llm)

    if llm.available.get("gemini"):
        if st.sidebar.button("Geminiモデル一覧を更新", disabled=is_running):
            st.session_state.gemini_models_cache = list_gemini_models(llm, refresh=True)
            st.sidebar.success(f"{len(st.session_state.gemini_models_cache)}件取得")
        if st.session_state.gemini_models_cache:
            preview = ", ".join(st.session_state.gemini_models_cache[:5])
            st.sidebar.caption(f"利用可能例: {preview}…")

    tasks = load_json_config(ROOT / "config/task_routing.json")
    overrides = st.session_state.manual_overrides
    model_overrides = st.session_state.model_overrides
    options = ["auto", "gemini", "claude", "gpt4o", "grok"]
    live = runner.state.task_routing_live if is_running or runner.state.status == "completed" else {}

    if is_running:
        st.sidebar.caption("処理中は自動選定結果を表示します（変更不可）")

    for task_name in tasks:
        if task_name == "fallback":
            continue
        label = TASK_LABELS.get(task_name, task_name)

        if task_name in live:
            info = live[task_name]
            st.sidebar.markdown(
                f"**{label}**  \n"
                f"✅ {info['display']}  \n"
                f"`{info.get('model', '—')}`  \n"
                f"_{info.get('mode', '')}選定_"
            )
            continue

        if is_running:
            pending = overrides.get(task_name, "auto")
            pending_label = DISPLAY_NAMES.get(pending, "自動")
            pending_model = model_overrides.get(task_name, "auto")
            model_label = pending_model if pending_model != "auto" else "自動"
            st.sidebar.markdown(f"**{label}**  \n⏳ {pending_label} / {model_label}（未実行）")
            continue

        current = overrides.get(task_name, "auto")
        idx = options.index(current) if current in options else 0
        choice = st.sidebar.selectbox(
            f"{label} — プロバイダ",
            options,
            index=idx,
            format_func=lambda x: DISPLAY_NAMES.get(x, x),
            key=f"override_{task_name}",
        )
        if choice == "auto":
            overrides.pop(task_name, None)
        else:
            overrides[task_name] = choice

        if choice != "auto" and llm.available.get(choice):
            models = list_models_for_provider(llm, choice)
            model_options = ["auto"] + models
            current_model = model_overrides.get(task_name, "auto")
            model_idx = model_options.index(current_model) if current_model in model_options else 0
            model_choice = st.sidebar.selectbox(
                f"{label} — モデル",
                model_options,
                index=model_idx,
                format_func=lambda x: "自動（デフォルト）" if x == "auto" else x,
                key=f"model_{task_name}",
            )
            if model_choice == "auto":
                model_overrides.pop(task_name, None)
            else:
                model_overrides[task_name] = model_choice
        elif task_name in model_overrides:
            model_overrides.pop(task_name, None)


def render_advanced_settings() -> None:
    with st.expander("詳細設定", expanded=False):
        st.subheader("タスクルーティング (JSON)")
        routing = load_json_config(ROOT / "config/task_routing.json")
        routing_text = st.text_area("task_routing.json", json.dumps(routing, ensure_ascii=False, indent=2), height=200)
        if st.button("ルーティングを保存"):
            save_json_config(ROOT / "config/task_routing.json", json.loads(routing_text))
            st.success("保存しました")

        st.subheader("料金テーブル")
        pricing = load_json_config(ROOT / "config/pricing_rates.json")
        pricing_text = st.text_area("pricing_rates.json", json.dumps(pricing, ensure_ascii=False, indent=2), height=200)
        if st.button("料金を保存"):
            save_json_config(ROOT / "config/pricing_rates.json", json.loads(pricing_text))
            st.success("保存しました")

        st.subheader("予算レバー優先順位")
        cfg = load_json_config(ROOT / "config/budget_scenarios.json")
        levers = cfg.get("budget_lever_priority", [])
        lever_text = st.text_input("優先順位(カンマ区切り)", ",".join(levers))
        if st.button("レバー優先順位を保存"):
            cfg["budget_lever_priority"] = [x.strip() for x in lever_text.split(",") if x.strip()]
            save_json_config(ROOT / "config/budget_scenarios.json", cfg)
            st.session_state.budget_lever_priority = cfg["budget_lever_priority"]
            st.success("保存しました")


def main() -> None:
    st.set_page_config(page_title="AI資料生成", page_icon="📊", layout="wide")
    ensure_env()
    init_session()
    inject_bright_theme()

    llm = LLMClientManager(ENV_PATH)
    if not llm.has_any_key():
        st.error("APIキーが1つも設定されていません。.env にキーを記入してください。")
        st.stop()

    orchestrator = Orchestrator(llm)
    runner: PipelineRunner = st.session_state.runner
    state = runner.state

    st.title("マルチLLMオーケストレーション型 AI資料生成")
    st.caption("複数ソースから PowerPoint (.pptx) を自動生成します")

    is_running = render_status_panel(state, runner, st.session_state.estimated_cost)

    if is_running:
        st.warning("⏳ **処理実行中です。** 下の入力欄は変更できますが、反映されません。完了までお待ちください。")
    else:
        st.info("処理にはソース数に応じて数分程度かかる場合があります。処理中はページを閉じないでください。")

    render_sidebar(runner, llm, is_running)

    uploaded = st.file_uploader(
        "1. ファイルアップロード (複数選択可)",
        accept_multiple_files=True,
        type=["txt", "md", "csv", "pdf", "docx", "pptx", "xlsx", "xlsm", "mp3", "m4a", "mp4", "m4v"],
        help="対応: txt, md, csv, pdf, docx, pptx, xlsx, mp3/m4a, mp4/m4v",
    )
    yt_text = st.text_area("YouTube動画URL (改行区切り・任意)", placeholder="https://www.youtube.com/watch?v=...")
    st.caption("※ YouTube URL直接入力はGeminiプレビュー機能です。将来的に料金・制限が変更される可能性があります。")
    web_text = st.text_area("参考WebサイトURL (改行区切り・任意)", placeholder="https://example.com/article")
    st.caption("ファイル・YouTube・Webサイトのいずれか1つ以上あれば生成できます（すべて必須ではありません）。")

    purpose = st.text_area("2. 生成目的・用途", placeholder="経営層向け、生成AI活用方針の説明")
    audience = st.text_area("3. 想定利用相手", placeholder="役員5名、ITリテラシーは高くない")

    st.subheader("4. スライド枚数・時間設定")
    use_recommend = st.checkbox("おすすめ構成で生成", value=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        min_slides = st.number_input("最低枚数", min_value=1, max_value=50, value=5, disabled=use_recommend)
    with c2:
        max_slides = st.number_input("最大枚数", min_value=1, max_value=50, value=15, disabled=use_recommend)
    with c3:
        minutes = st.number_input("想定プレゼン時間(分)", min_value=1, max_value=120, value=15, disabled=use_recommend)

    palette = st.selectbox("デザインパレット", ["内容に応じて自動選定"] + palette_choices())

    st.subheader("5. 背景フレーム")
    use_background_frame = st.radio(
        "背景フレーム",
        options=["無", "有"],
        horizontal=True,
        help="「有」を選ぶと、フレーム画像をスライド中央に配置します（四辺に約5mmの余白）。",
    ) == "有"
    if use_background_frame:
        frame_path = ROOT / "assets" / "slide_background_frame.png"
        if frame_path.exists():
            st.caption("プレビュー: スライド中央に配置し、上下左右に約5mmの余白を設けます。")
            st.image(str(frame_path), width=320)
        else:
            st.warning("背景フレーム画像が見つかりません: assets/slide_background_frame.png")

    render_advanced_settings()

    if use_recommend:
        st.caption("おすすめ構成: ローカル統計による無料の簡易推定を使用します。")
        use_ai_analysis = st.button("AIで精密分析する (課金あり)")
        if use_ai_analysis:
            st.warning("この分析には LLM API 費用が発生します。生成開始時に精密分析が実行されます。")
            st.session_state.use_ai_analysis = True
    else:
        st.session_state.use_ai_analysis = False

    # --- コスト見積もり ---
    source_ready = has_any_source(uploaded, yt_text, web_text)
    col_est, col_gen = st.columns(2)
    with col_est:
        estimate_clicked = st.button("コストを見積もる", type="secondary", disabled=not source_ready)
    with col_gen:
        gen_disabled = not source_ready or is_running
        start_clicked = st.button("生成開始", type="primary", disabled=gen_disabled)

    if not source_ready:
        st.caption("「生成開始」を有効にするには、ファイル・YouTube URL・Webサイト URL のいずれか1つ以上を入力してください。")
    elif not st.session_state.estimate_done:
        st.caption("「生成開始」を押すと、コスト見積もり（無料・API呼び出しなし）を自動実行してから生成を開始します。")

    if estimate_clicked:
        if perform_cost_estimate(
            uploaded,
            yt_text,
            web_text,
            orchestrator=orchestrator,
            use_recommend=use_recommend,
            min_slides=int(min_slides),
            max_slides=int(max_slides),
        ):
            if st.session_state.get("plan_rationale"):
                st.info(st.session_state.plan_rationale)
            for w in st.session_state.get("estimate_warnings", []):
                st.warning(w)
            st.caption("※ コスト見積もりは目安であり保証ではありません。実コストとの乖離が生じる場合があります。")
        else:
            st.error("ファイル・YouTube URL・Webサイト URL のいずれかを入力してください。")

    if st.session_state.estimate_done and st.session_state.scenarios:
        st.subheader("予算シナリオ")
        scenario_labels = [f"{s.label}: ${s.total_usd:.4f}" for s in st.session_state.scenarios]
        choice = st.radio("シナリオを選択", range(len(scenario_labels)), format_func=lambda i: scenario_labels[i])
        st.session_state.selected_scenario = st.session_state.scenarios[choice]
        budget_input = st.number_input("任意の予算上限 (USD)", min_value=0.0, value=0.0, step=0.5)
        if budget_input > 0:
            fit = fit_budget(
                budget_input,
                st.session_state.sources_cache,
                orchestrator,
                st.session_state.slide_count_est,
                int(min_slides),
                st.session_state.budget_lever_priority,
            )
            st.info(f"予算内提案: 枚数={fit['slides']}, QA={fit['qa_max_rounds']}回, テンプレート固定={fit['template_only']}, 概算=${fit['estimated_usd']:.4f}")
            st.session_state.budget_fit = fit
        if st.button("キャンセル / 最初からやり直す"):
            st.session_state.estimate_done = False
            st.session_state.selected_scenario = None
            st.rerun()

    # --- 生成開始 ---
    if start_clicked and source_ready:
        if not st.session_state.estimate_done:
            perform_cost_estimate(
                uploaded,
                yt_text,
                web_text,
                orchestrator=orchestrator,
                use_recommend=use_recommend,
                min_slides=int(min_slides),
                max_slides=int(max_slides),
            )
        if st.session_state.scenarios and not st.session_state.selected_scenario:
            st.session_state.selected_scenario = st.session_state.scenarios[0]

    if start_clicked and source_ready and st.session_state.selected_scenario:
        temp_dir = ROOT / "temp_uploads" / "run"
        shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        file_paths = []
        for f in uploaded or []:
            p = temp_dir / f.name
            p.write_bytes(f.getvalue())
            file_paths.append(p)
        sc = st.session_state.selected_scenario
        fit = st.session_state.get("budget_fit")
        config = {
            "file_paths": [str(p) for p in file_paths],
            "youtube_urls": yt_text,
            "web_urls": web_text,
            "purpose": purpose,
            "audience": audience,
            "use_recommend": use_recommend,
            "use_ai_analysis": st.session_state.get("use_ai_analysis", False),
            "min_slides": int(min_slides),
            "max_slides": int(max_slides),
            "minutes": int(minutes),
            "palette": "auto" if palette.startswith("内容") else palette,
            "use_background_frame": use_background_frame,
            "manual_overrides": dict(st.session_state.manual_overrides),
            "model_overrides": dict(st.session_state.model_overrides),
            "estimated_cost": st.session_state.estimated_cost,
            "qa_max_rounds": fit["qa_max_rounds"] if fit else sc.qa_rounds,
            "template_only": fit["template_only"] if fit else sc.template_only,
            "compress_slides": fit is not None or sc.compress_slides,
        }
        runner.start(config)
        st.session_state.estimate_done = False
        st.rerun()

    # --- 処理中は自動更新 ---
    if state.status == "running":
        time.sleep(1.5)
        st.rerun()

    if state.status == "completed" and state.output_path:
        st.success("生成完了")
        st.write(state.plan_rationale)
        st.write(f"構成: {state.slide_count}枚 / 約{state.presentation_minutes}分")
        st.write(state.qa_summary)
        st.write(f"最終コスト: ${state.actual_cost:.4f}")
        with open(state.output_path, "rb") as f:
            st.download_button("PPTXをダウンロード", f, file_name=Path(state.output_path).name)
        if st.button("新しい生成を開始"):
            runner.state = type(state)()
            st.rerun()

    if state.status == "cancelled":
        st.warning(state.message)

    if state.status == "error":
        st.error(state.error or state.message)


if __name__ == "__main__":
    main()
