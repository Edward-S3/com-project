#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON_CMD=python3

echo "=== AI資料生成アプリ セットアップ (Linux) ==="

if ! command -v "$PYTHON_CMD" &>/dev/null; then
  echo "エラー: python3 が見つかりません。"
  exit 1
fi

if [ ! -d venv ]; then
  echo "仮想環境を作成しています..."
  "$PYTHON_CMD" -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env を .env.example から作成しました。APIキーを記入してください。"
fi

mkdir -p outputs temp_uploads logs

check_cmd() {
  if command -v "$1" &>/dev/null; then
    echo "  OK: $1"
  else
    echo "  警告: $1 が見つかりません。$2"
  fi
}

echo ""
echo "QA用ツールの確認:"
check_cmd soffice "sudo apt install libreoffice でインストールできます"
check_cmd pdftoppm "sudo apt install poppler-utils でインストールできます"
check_cmd ffprobe "sudo apt install ffmpeg でインストールできます(音声/動画メタデータ用)"

echo ""
echo "セットアップ完了。"
echo "起動: ./scripts/start.sh"
echo "  (ポート ${LAN_PORT:-8517} / アドレス ${LAN_BIND:-172.16.16.10} — .env の LAN_PORT / LAN_BIND で変更可)"
echo "実行権限: chmod +x setup.sh"
