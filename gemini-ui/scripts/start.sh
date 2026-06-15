#!/usr/bin/env bash
# Gemini Chat UI 起動スクリプト
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="${ROOT}/venv/bin"
PORT="${GEMINI_UI_PORT:-8507}"
ADDRESS="${GEMINI_UI_ADDRESS:-172.16.16.10}"

exec "${VENV}/streamlit" run app.py \
  --server.port "$PORT" \
  --server.address "$ADDRESS" \
  --server.headless true \
  --browser.gatherUsageStats false \
  --server.maxUploadSize 500
