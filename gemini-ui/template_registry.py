"""テンプレート種別（template_kind）レジストリ — 特殊処理の判定と管理画面向けメタデータ"""
from __future__ import annotations

import json
from typing import Any

KIND_STANDARD = "standard"
KIND_EXCEL_EXTRA = "excel_extra"
KIND_AUDIO = "audio"
KIND_IMAGE = "image"
KIND_OFFICE_OUTPUT = "office_output"

ALL_KINDS = (
    KIND_STANDARD,
    KIND_EXCEL_EXTRA,
    KIND_AUDIO,
    KIND_IMAGE,
    KIND_OFFICE_OUTPUT,
)

SPECIAL_KINDS = frozenset({
    KIND_EXCEL_EXTRA,
    KIND_AUDIO,
    KIND_IMAGE,
    KIND_OFFICE_OUTPUT,
})

HANDLER_SPECS: dict[str, dict[str, Any]] = {
    KIND_STANDARD: {
        "label": "通常",
        "description": "通常の LLM チャット。添付ファイルはテキスト抽出して利用します。",
        "model_filter": None,
        "creatable_in_wizard": False,
    },
    KIND_EXCEL_EXTRA: {
        "label": "Excel 集計・分析",
        "description": (
            "xlsx を pandas で集計した結果をもとに Gemini が分析します。"
            " xlsx 添付とシート選択が必須です。"
        ),
        "model_filter": "gemini_only",
        "creatable_in_wizard": True,
        "default_category": "Excel",
        "default_model": "gemini-3.1-pro-preview",
        "default_prompt": (
            "Excel データを pandas で加工した結果に基づき、"
            "分析・傾向・インサイト・提言を日本語で提供する。"
        ),
        "default_allow_empty_prompt": False,
    },
    KIND_AUDIO: {
        "label": "音声処理",
        "description": "音声ファイル添付が必須。Gemini モデルのみ利用可能です。",
        "model_filter": "gemini_only",
        "creatable_in_wizard": True,
        "default_category": "音声",
        "default_model": "gemini-3.5-flash",
        "default_prompt": (
            "あなたは音声文字起こしの専門家です。\n"
            "添付された音声データを正確に文字起こししてください。"
        ),
        "default_allow_empty_prompt": True,
    },
    KIND_IMAGE: {
        "label": "画像生成",
        "description": "画像生成専用モデルのみ。参照画像は 0〜1 件。",
        "model_filter": "image_only",
        "creatable_in_wizard": True,
        "default_category": "画像",
        "default_model": "gemini-2.5-flash-image",
        "default_prompt": (
            "あなたは画像生成の専門アシスタントです。\n"
            "ユーザーが日本語で説明した内容に基づき、高品質な画像を生成してください。"
        ),
        "default_allow_empty_prompt": False,
    },
    KIND_OFFICE_OUTPUT: {
        "label": "Office ファイル出力",
        "description": "LLM 回答から Word（docx）または PowerPoint（pptx）を自動生成します。",
        "model_filter": None,
        "creatable_in_wizard": True,
        "default_category": "文書",
        "default_model": "",
        "default_prompt": (
            "あなたはビジネス文書作成の専門家です。\n"
            "ユーザーの指示に従い、指定形式で成果物の内容を作成してください。"
        ),
        "default_allow_empty_prompt": False,
        "config_fields": [
            {
                "key": "output_format",
                "label": "出力形式",
                "type": "select",
                "options": [
                    {"value": "docx", "label": "Word（docx）"},
                    {"value": "pptx", "label": "PowerPoint（pptx）"},
                ],
                "default": "docx",
            },
        ],
    },
}


def normalize_kind(kind: str | None) -> str:
    k = (kind or KIND_STANDARD).strip()
    return k if k in HANDLER_SPECS else KIND_STANDARD


def get_template_kind(tmpl: dict | None) -> str:
    if not tmpl:
        return KIND_STANDARD
    return normalize_kind(tmpl.get("template_kind"))


def kind_label(kind: str | None) -> str:
    spec = HANDLER_SPECS.get(normalize_kind(kind), {})
    return spec.get("label") or normalize_kind(kind)


def is_special_kind(kind: str | None) -> bool:
    return normalize_kind(kind) in SPECIAL_KINDS


def parse_handler_config(raw: str | dict | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def handler_config_json(config: dict[str, Any] | None) -> str:
    return json.dumps(config or {}, ensure_ascii=False)


def template_handler_config(tmpl: dict | None) -> dict[str, Any]:
    if not tmpl:
        return {}
    return parse_handler_config(tmpl.get("handler_config"))


def office_output_format(tmpl: dict | None) -> str | None:
    if get_template_kind(tmpl) != KIND_OFFICE_OUTPUT:
        return None
    fmt = (template_handler_config(tmpl).get("output_format") or "").strip().lower()
    if fmt in ("docx", "pptx"):
        return fmt
    return None


def infer_kind_from_legacy(tmpl: dict) -> str:
    """名称・カテゴリから kind を推定（マイグレーション用）"""
    name = (tmpl.get("name") or "").strip()
    category = (tmpl.get("category") or "").strip()
    if name == "ExcelExtra" or name == "Excel集計・分析":
        return KIND_EXCEL_EXTRA
    if category == "音声":
        return KIND_AUDIO
    if category == "画像":
        return KIND_IMAGE
    return KIND_STANDARD


def wizard_creatable_kinds() -> list[tuple[str, str]]:
    return [
        (k, spec["label"])
        for k, spec in HANDLER_SPECS.items()
        if spec.get("creatable_in_wizard")
    ]


def default_handler_config(kind: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    kind = normalize_kind(kind)
    config: dict[str, Any] = {}
    spec = HANDLER_SPECS.get(kind, {})
    for field in spec.get("config_fields") or []:
        config[field["key"]] = field.get("default")
    if overrides:
        config.update({k: v for k, v in overrides.items() if v is not None})
    return config


def office_output_instruction_for_kind(tmpl: dict | None) -> str:
    import office_files as office

    fmt = office_output_format(tmpl)
    if fmt == "pptx":
        return office.pptx_output_instruction()
    if fmt == "docx":
        return (
            "\n\n【出力形式】ユーザーは Word（docx）形式での成果物を求めています。"
            "見出し（##）と本文を Markdown 形式で記述してください。"
        )
    return ""
