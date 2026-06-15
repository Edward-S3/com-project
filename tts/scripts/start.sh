#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec "$ROOT/venv/bin/streamlit" run app.py \
  --server.port 8509 \
  --server.address 172.16.16.10 \
  --server.headless true \
  --browser.gatherUsageStats false
