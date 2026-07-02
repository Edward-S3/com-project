#!/bin/bash
# 検証サーバー (172.16.16.13) に必要パッケージをインストール
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq \
  nginx \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  build-essential \
  libffi-dev \
  libssl-dev \
  curl \
  rsync \
  ffmpeg \
  libsndfile1

systemctl enable nginx
systemctl start nginx || true

# Ollama (本番と同様のローカル LLM 用) — CUDA ドライバは別途管理、インストールのみ
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | OLLAMA_SKIP_CUDA=1 sh || \
    curl -fsSL https://ollama.com/install.sh | sh
fi

# Nginx デフォルトサイトを無効化 (python_apps.conf と競合するため)
rm -f /etc/nginx/sites-enabled/default

# Docker (本番で有効な場合に備え)
if ! command -v docker >/dev/null 2>&1; then
  apt-get install -y -qq docker.io
  systemctl enable docker
  systemctl start docker || true
fi

echo "Packages installed successfully."
