#!/usr/bin/env bash
set -euo pipefail
cd /opt/mescheck
exec ./venv/bin/streamlit run app.py \
  --server.port=8511 \
  --server.address=0.0.0.0 \
  --server.headless=true
