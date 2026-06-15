#!/usr/bin/env bash
# Gemini Chat 管理者パネル 起動スクリプト
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="${ROOT}/venv/bin"
PORT="${GEMINI_ADMIN_PORT:-8508}"
ADDRESS="${GEMINI_ADMIN_ADDRESS:-172.16.16.10}"

exec "${VENV}/streamlit" run admin.py \
  --server.port "$PORT" \
  --server.address "$ADDRESS" \
  --server.headless true \
  --browser.gatherUsageStats false
