"""ExcelExtra テンプレート — pandas 前処理 + Gemini 分析（案A）"""
from __future__ import annotations

import contextlib
import io
import os
import re
import traceback

import office_files as office

PYTHON_BLOCK_RE = re.compile(
    r"```(?:python|py)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)

MAX_ANALYSIS_DATA_CHARS = 100_000
PREVIEW_SAMPLE_ROWS = 3
FALLBACK_MAX_ROWS = 100

EXCEL_PREPROCESS_CODE_PROMPT = """\
あなたは Excel データ処理の専門家です。
ユーザーの指示に従い、添付 Excel を pandas で読み込み・集計・加工する Python コードのみを生成してください。

ルール:
- 回答は ```python ... ``` コードブロック1つのみ（説明文は不要）
- XLSX_PATH（先頭ファイル）, XLSX_PATHS（全ファイル）, pd, pandas, openpyxl が利用可能
- pd.read_excel(path, sheet_name=..., engine="openpyxl") でデータのみ読み込む（数式は評価済み値）
- 空行・空列は dropna で除去してから処理すること
- SELECTED_SHEETS（先頭ファイルの選択シート名リスト）, SHEET_SELECTION（全ファイルの選択）が注入される
- **SELECTED_SHEETS / SHEET_SELECTION に含まれるシートのみ** を対象にすること（他シートは読み込まない）
- 加工結果は print(df.to_csv(index=False)) で CSV 形式出力すること（to_markdown は使用しない）
- 分析に必要な集約データに絞る（生データ全行のダンプは避ける）
"""

EXCEL_ANALYSIS_PROMPT = """\
あなたはデータアナリストです。
ユーザーから提供された「加工済み Excel データ」と質問に基づき、
分析結果・傾向・気づき・提言をわかりやすい日本語で回答してください。

- 数値根拠を示しながら説明すること
- 表形式データを引用する場合は Markdown 表を使うこと
- 加工済みデータにない内容を推測で補完しないこと
- 不明点は「データからは判断できない」と明記すること
"""


def extract_python_blocks(text: str) -> list[str]:
    return [
        m.group(1).strip()
        for m in PYTHON_BLOCK_RE.finditer(text or "")
        if m.group(1).strip()
    ]


def run_python_on_xlsx(
    code: str,
    xlsx_paths: list[str],
    *,
    selected_sheets: dict[str, list[str]] | None = None,
) -> str:
    """添付 xlsx パスを注入して Python を実行し stdout を返す"""
    import openpyxl
    import pandas as pd

    try:
        import xlwings as xw
    except ImportError:
        xw = None

    paths = [p for p in (xlsx_paths or []) if p]
    sel = selected_sheets or {}
    stdout = io.StringIO()
    namespace = {
        "__builtins__": __builtins__,
        "pd": pd,
        "pandas": pd,
        "openpyxl": openpyxl,
        "xlwings": xw,
        "XLSX_PATH": paths[0] if paths else "",
        "XLSX_PATHS": paths,
        "INPUT_FILE": paths[0] if paths else "",
        "SELECTED_SHEETS": sel.get(paths[0], []) if paths else [],
        "SHEET_SELECTION": sel,
    }

    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, namespace)  # noqa: S102
    except Exception:
        stdout.write(traceback.format_exc())

    return stdout.getvalue().strip()


def build_sheet_preview(
    xlsx_paths: list[str],
    *,
    sample_rows: int = PREVIEW_SAMPLE_ROWS,
    selected_sheets: dict[str, list[str]] | None = None,
) -> str:
    """コード生成用 — 各ブックのシート概要と先頭行サンプル"""
    parts: list[str] = []
    sel = selected_sheets or {}
    for path in [p for p in (xlsx_paths or []) if p]:
        try:
            sheets = office.load_xlsx_data_only(path)
            sheets = office.filter_sheets_by_names(sheets, sel.get(path))
        except Exception as ex:
            parts.append(f"### ファイル: {path}\n（読込エラー: {ex}）")
            continue
        parts.append(f"### ファイル: {path}")
        if sel.get(path):
            parts.append(f"（選択シート: {', '.join(sel[path])}）")
        if not sheets:
            parts.append("（選択シートなし）")
            continue
        for name, df in sheets.items():
            cols = [str(c) for c in df.columns]
            parts.append(
                f"- シート「{name}」: {len(df)} 行 × {len(df.columns)} 列"
                f" / 列: {', '.join(cols[:20])}"
                f"{' …' if len(cols) > 20 else ''}"
            )
            if not df.empty:
                sample = df.head(sample_rows).to_csv(index=False)
                parts.append(f"  先頭行サンプル:\n{sample}")
    return "\n".join(parts) if parts else "（Excel ファイルなし）"


def fallback_processed_data(
    xlsx_paths: list[str],
    *,
    max_rows: int = FALLBACK_MAX_ROWS,
    selected_sheets: dict[str, list[str]] | None = None,
) -> str:
    """LLM コード失敗時 — データのみ抽出して CSV 化"""
    parts: list[str] = ["--- pandas 自動抽出（データのみ） ---"]
    sel = selected_sheets or {}
    for path in [p for p in (xlsx_paths or []) if p]:
        try:
            sheets = office.load_xlsx_data_only(path)
            sheets = office.filter_sheets_by_names(sheets, sel.get(path))
        except Exception as ex:
            parts.append(f"### {path}\n（読込エラー: {ex}）")
            continue
        parts.append(f"### ファイル: {path}")
        if sel.get(path):
            parts.append(f"（選択シート: {', '.join(sel[path])}）")
        parts.append(office.sheets_data_only_text(sheets, max_rows=max_rows))
    return "\n\n".join(parts)


def format_sheet_selection_text(selected_sheets: dict[str, list[str]]) -> str:
    if not selected_sheets:
        return "（全シート — 未指定）"
    lines: list[str] = []
    for path, names in selected_sheets.items():
        label = os.path.basename(path) if path else path
        lines.append(f"- {label}: {', '.join(names) if names else '（なし）'}")
    return "\n".join(lines)


def _truncate_for_analysis(text: str) -> str:
    if len(text) <= MAX_ANALYSIS_DATA_CHARS:
        return text
    return (
        text[:MAX_ANALYSIS_DATA_CHARS]
        + "\n\n（以下省略 — 分析用トークン上限のため切り詰め）"
    )


def _is_usable_preprocess_output(text: str) -> bool:
    if not text or not text.strip():
        return False
    if text.strip() == "(出力なし)":
        return False
    if "Traceback (most recent call last)" in text:
        return False
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    # 説明文のみの出力（CSV/表形式でない）は集計結果として不十分
    if not any("," in ln or "\t" in ln or ln.startswith("|") for ln in lines[:5]):
        return False
    return True


def build_analysis_user_text(user_prompt: str, processed_data: str) -> str:
    data = _truncate_for_analysis(processed_data)
    return (
        f"【ユーザーの質問】\n{user_prompt.strip()}\n\n"
        f"【加工済みデータ（pandas による抽出・集計結果）】\n{data}"
    )


def build_preprocess_user_text(
    user_prompt: str,
    xlsx_paths: list[str],
    *,
    selected_sheets: dict[str, list[str]] | None = None,
) -> str:
    preview = build_sheet_preview(xlsx_paths, selected_sheets=selected_sheets)
    selection = format_sheet_selection_text(selected_sheets or {})
    return (
        f"【Excel ファイル概要】\n{preview}\n\n"
        f"【集計対象シート（ユーザー選択）】\n{selection}\n\n"
        f"【ユーザーの指示】\n{user_prompt.strip()}"
    )


def generate_preprocess_code(
    user_prompt: str,
    xlsx_paths: list[str],
    *,
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    selected_sheets: dict[str, list[str]] | None = None,
) -> tuple[str, int, int]:
    """第1段階-A: Gemini に集計用 Python コードを生成"""
    import llm_providers as llm

    user_text = build_preprocess_user_text(
        user_prompt, xlsx_paths, selected_sheets=selected_sheets,
    )
    return llm.generate_text(
        model,
        user_text,
        EXCEL_PREPROCESS_CODE_PROMPT,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def execute_preprocess_code(
    code_response: str,
    xlsx_paths: list[str],
    *,
    selected_sheets: dict[str, list[str]] | None = None,
) -> tuple[str, str]:
    """第1段階-B: 生成コードを実行し加工済みデータを返す"""
    blocks = extract_python_blocks(code_response)
    if blocks:
        result = run_python_on_xlsx(
            blocks[-1], xlsx_paths, selected_sheets=selected_sheets,
        )
        if _is_usable_preprocess_output(result):
            return result, "LLM 生成コードによる集計"

    fallback = fallback_processed_data(xlsx_paths, selected_sheets=selected_sheets)
    note = "自動抽出（コード生成/実行フォールバック）"
    if blocks:
        note = "LLM 生成コード実行失敗 → 自動抽出"
    return fallback, note


def run_preprocess_phase(
    user_prompt: str,
    xlsx_paths: list[str],
    *,
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    selected_sheets: dict[str, list[str]] | None = None,
) -> tuple[str, str, int, int]:
    """
    第1段階: Gemini にコード生成 → ローカル実行 → 加工済みデータ文字列を返す。
    戻り値: (processed_data, method_note, input_tokens, output_tokens)
    """
    code_response, in_tok, out_tok = generate_preprocess_code(
        user_prompt, xlsx_paths,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        selected_sheets=selected_sheets,
    )
    processed, method = execute_preprocess_code(
        code_response, xlsx_paths, selected_sheets=selected_sheets,
    )
    return processed, method, in_tok, out_tok


PROCESSED_SECTION_HEADING = "### 集計結果"
_PROCESSED_SECTION_MARKER = f"\n---\n\n{PROCESSED_SECTION_HEADING}\n\n"
_LEGACY_PROCESSED_SECTION_MARKER = "\n---\n\n### 加工データ（参考）\n\n"


def append_processed_data_reference(analysis: str, processed_data: str) -> str:
    """分析回答の末尾に集計結果セクションを付与（履歴保存・テキスト復元用）"""
    if not processed_data.strip():
        return analysis
    return (
        f"{analysis.rstrip()}\n\n---\n\n"
        f"{PROCESSED_SECTION_HEADING}\n\n{processed_data.strip()}"
    )


def parse_processed_to_dataframe(processed_data: str):
    """集計結果文字列を DataFrame に変換（失敗時 None）"""
    import pandas as pd

    text = (processed_data or "").strip()
    if not text:
        return None
    for sep in (",", "\t"):
        try:
            df = pd.read_csv(io.StringIO(text), sep=sep)
            if not df.empty and len(df.columns) >= 1:
                return df.fillna("")
        except Exception:
            continue
    return None


def split_analysis_and_processed(full_text: str) -> tuple[str, str]:
    """表示テキストから分析本文と集計結果を分離"""
    text = full_text or ""
    for marker in (_PROCESSED_SECTION_MARKER, _LEGACY_PROCESSED_SECTION_MARKER):
        if marker in text:
            analysis, processed = text.split(marker, 1)
            return analysis.rstrip(), processed.strip()
    return text.rstrip(), ""


_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")


def _clean_xlsx_cell_text(text: str) -> str:
    return _MD_BOLD_RE.sub(r"\1", (text or "").strip())


def _parse_markdown_table_row(line: str) -> list[str]:
    stripped = (line or "").strip()
    if not stripped.startswith("|"):
        return []
    inner = stripped.strip("|")
    return [_clean_xlsx_cell_text(part) for part in inner.split("|")]


def _is_markdown_table_separator(line: str) -> bool:
    return bool(_MD_TABLE_SEP_RE.match((line or "").strip()))


def _pad_table_rows(table_rows: list[list[str]]) -> list[list[str]]:
    if not table_rows:
        return []
    width = max(len(row) for row in table_rows)
    return [row + [""] * (width - len(row)) for row in table_rows]


def analysis_text_to_sheet_rows(analysis_text: str) -> list[list[str]]:
    """分析 Markdown を Excel 行リストへ（表は列ごとに分割）"""
    rows: list[list[str]] = []
    table_buffer: list[list[str]] = []
    in_table = False

    def flush_table() -> None:
        nonlocal table_buffer, in_table
        if table_buffer:
            rows.extend(_pad_table_rows(table_buffer))
            rows.append([""])
        table_buffer = []
        in_table = False

    for line in (analysis_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            if _is_markdown_table_separator(stripped):
                in_table = bool(table_buffer)
                continue
            cells = _parse_markdown_table_row(stripped)
            if cells:
                if not in_table and table_buffer:
                    flush_table()
                table_buffer.append(cells)
                in_table = True
            continue
        if in_table:
            flush_table()
        if stripped:
            rows.append([_clean_xlsx_cell_text(stripped)])
        elif rows and rows[-1] != [""]:
            rows.append([""])

    flush_table()
    while rows and rows[-1] == [""]:
        rows.pop()
    return rows or [[""]]


def analysis_text_to_xlsx(analysis_text: str) -> bytes:
    """分析結果テキストを xlsx 1シートに書き出す（Markdown 表は列分割）"""
    return office.workbook_bytes_from_rows(
        analysis_text_to_sheet_rows(analysis_text),
        sheet_name="分析結果",
    )


def build_download_outputs(processed_data: str, analysis_text: str) -> list[dict]:
    """ExcelExtra 回答の CSV / xlsx ダウンロード用ファイルを生成"""
    from datetime import datetime

    ts = datetime.now(office.JST).strftime("%Y%m%d_%H%M%S")
    outputs: list[dict] = []
    proc = (processed_data or "").strip()
    if proc:
        outputs.append({
            "name": f"excelextra_aggregated_{ts}.csv",
            "mime": "text/csv",
            "data": proc.encode("utf-8-sig"),
            "label": "集計結果（CSV）",
        })
    analysis = (analysis_text or "").strip()
    if analysis:
        outputs.append({
            "name": f"excelextra_analysis_{ts}.xlsx",
            "mime": office.OFFICE_MIME_MAP["xlsx"],
            "data": analysis_text_to_xlsx(analysis),
            "label": "分析結果（xlsx）",
        })
    return outputs
