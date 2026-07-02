#!/bin/bash
cd "$(dirname "$0")/.."
set -a
# shellcheck disable=SC1091
[ -f .env ] && source .env
set +a
PORT="${LAN_PORT:-8517}"
ADDRESS="${LAN_BIND:-172.16.16.10}"
exec ./venv/bin/streamlit run app.py \
  --server.port="$PORT" \
  --server.address="$ADDRESS" \
  --server.headless=true \
  --browser.gatherUsageStats=false \
  --server.maxUploadSize=50
