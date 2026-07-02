#!/bin/bash
cd "$(dirname "$0")/.."
exec ./venv/bin/streamlit run app.py \
  --server.port=8516 \
  --server.address=172.16.16.10 \
  --server.headless=true \
  --browser.gatherUsageStats=false \
  --server.maxUploadSize=50
