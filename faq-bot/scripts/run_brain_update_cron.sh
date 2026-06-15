#!/bin/bash
# 深夜バッチ: 頭脳（RAG）差分更新
cd /opt/faq-bot || exit 1
exec /opt/faq-bot/venv/bin/python3 /opt/faq-bot/update_job.py
