#!/usr/bin/env python3
"""深夜バッチ / 管理画面: .env 再読み込み + 利用可能 LLM 同期 + DB 設定補正"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

ROOT = os.path.dirname(os.path.abspath(__file__))

import db
import llm_providers as llm


def _expand_gemma4_allowed_models(available: dict[str, str]) -> list[str]:
    """
    使用可能モデルを個別指定しているユーザーで、既に local:gemma4-* を
    許可している場合は、新しく利用可能になった gemma4 チャットモデルも追加する。
    """
    chat_gemma4 = [
        mid for mid in sorted(available)
        if mid.startswith("local:gemma4-")
        and not (llm.get_model_info(mid) or {}).get("gemma4_role") == "router"
    ]
    if not chat_gemma4:
        return []

    expanded_users: list[str] = []
    for user in db.get_all_users():
        am_raw = (user.get("allowed_models") or "").strip()
        if not am_raw:
            continue
        kept = [m.strip() for m in am_raw.split(",") if m.strip()]
        if not any(m.startswith("local:gemma4-") for m in kept):
            continue
        new_list = kept[:]
        changed = False
        for mid in chat_gemma4:
            if mid not in new_list:
                new_list.append(mid)
                changed = True
        if changed:
            db.update_user(user["employee_id"], allowed_models=",".join(new_list))
            expanded_users.append(user["employee_id"])
    return expanded_users


def run_sync() -> dict[str, Any]:
    """
    LLM 同期を実行し結果 dict を返す。
    keys: ok, available (dict), summary, messages (list[str])
    """
    messages: list[str] = []
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    messages.append(f"[{ts}] LLM 設定同期を開始")

    db.init_db()
    llm.clear_provider_caches()
    llm.reload_provider_env()

    available = llm.get_available_models()
    available_ids = set(available.keys())
    messages.append(f"利用可能モデル: {len(available_ids)} 件")

    if not available_ids:
        messages.append("警告: 利用可能なモデルがありません。")
        return {"ok": False, "available": {}, "summary": {}, "messages": messages}

    expanded = _expand_gemma4_allowed_models(available)
    if expanded:
        messages.append(
            "使用可能モデルに Gemma 4 を追加: " + ", ".join(expanded)
        )

    summary = db.sync_llm_settings_with_available(available_ids)

    if summary["users_default_reset"]:
        messages.append(
            "ユーザーデフォルト LLM → グローバル設定: "
            + ", ".join(summary["users_default_reset"])
        )
    if summary["users_allowed_trimmed"]:
        messages.append(
            "使用可能モデルリストを整理: "
            + ", ".join(summary["users_allowed_trimmed"])
        )
    if summary["templates_default_reset"]:
        messages.append(
            "テンプレートデフォルト LLM をクリア: "
            + ", ".join(summary["templates_default_reset"])
        )
    if summary["global_model_changed"]:
        ch = summary["global_model_changed"]
        messages.append(f"グローバルデフォルト LLM: {ch['from']} → {ch['to']}")

    if not any([
        expanded,
        summary["users_default_reset"],
        summary["users_allowed_trimmed"],
        summary["templates_default_reset"],
        summary["global_model_changed"],
    ]):
        messages.append("DB 設定の変更はありませんでした。")
    else:
        messages.append("DB 設定を更新しました。")

    return {
        "ok": True,
        "available": available,
        "summary": summary,
        "messages": messages,
    }


def main() -> int:
    os.chdir(ROOT)
    result = run_sync()
    for line in result["messages"]:
        print(line, flush=True)
    if result["ok"] and result.get("available"):
        for mid, label in sorted(result["available"].items()):
            print(f"  - {mid}: {label}", flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
