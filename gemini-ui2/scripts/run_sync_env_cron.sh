#!/bin/bash
# 深夜バッチ: .env 再読み込み + 利用可能 LLM 同期
set -euo pipefail
cd /opt/gemini-ui || exit 1
mkdir -p /opt/gemini-ui/logs
exec /opt/gemini-ui/venv/bin/python3 /opt/gemini-ui/sync_env_job.py
