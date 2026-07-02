#!/bin/bash
# Ollama モデル rsync 完了後に検証サーバーで実行
set -euo pipefail
PASS='P@sswd#%'
STAGING="nakaboshi@172.16.16.13"
SSH="ssh -i /root/.ssh/id_ed25519_staging -o StrictHostKeyChecking=no"

$SSH "$STAGING" "echo '$PASS' | sudo -S bash -c '
  if [ -d /tmp/ollama-sync ]; then
    mkdir -p /usr/share/ollama
    rsync -a /tmp/ollama-sync/ /usr/share/ollama/
    chown -R ollama:ollama /usr/share/ollama 2>/dev/null || true
    rm -rf /tmp/ollama-sync
    systemctl restart ollama
    sleep 2
    ollama list
  else
    echo \"No /tmp/ollama-sync found — already finished or not started.\"
    ollama list
  fi
'"
