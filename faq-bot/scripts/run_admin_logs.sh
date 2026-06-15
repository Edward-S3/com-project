#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${ADMIN_LOGS_PORT:-8506}"
ADDRESS="${ADMIN_LOGS_ADDRESS:-172.16.16.10}"
exec ./venv/bin/streamlit run admin_logs.py \
  --server.port "$PORT" \
  --server.address "$ADDRESS" \
  --server.headless true \
  --browser.gatherUsageStats false
