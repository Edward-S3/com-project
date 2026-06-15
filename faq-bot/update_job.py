#!/usr/bin/env python3
"""深夜バッチ: 頭脳（RAG）の差分更新"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

from brain_update import ApiKeyManager, run_brain_update


def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 頭脳の自動更新を開始します...", flush=True)
    key_manager = ApiKeyManager.from_env()
    if not key_manager.api_keys:
        print("エラー: APIキーが設定されていません。", flush=True)
        return 1

    result = run_brain_update(key_manager=key_manager)
    if result.get("ok"):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {result['message']}", flush=True)
        return 0
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 失敗: {result['message']}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
