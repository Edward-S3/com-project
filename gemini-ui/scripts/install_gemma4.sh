#!/usr/bin/env bash
# Gemma 4 モデルを Ollama にインストール（2026-04-02 リリース）
# 使い方: bash /opt/gemini-ui/scripts/install_gemma4.sh [e2b|e4b|12b|26b|31b|all]
set -euo pipefail

TARGET="${1:-all}"

pull_one() {
    local tag="$1"
    echo "▶ ollama pull gemma4:${tag}"
    ollama pull "gemma4:${tag}"
}

case "$TARGET" in
    e2b)  pull_one e2b ;;
    e4b)  pull_one e4b ;;
    12b)  pull_one 12b ;;
    26b)  pull_one 26b ;;
    31b)  pull_one 31b ;;
    all)
        echo "=== Gemma 4 推奨セット ==="
        echo "  E2B  … 自動選択ルーター（高速・約2GB）"
        echo "  E4B  … 通常チャット（バランス・約4GB）"
        echo "  12B  … ワークステーション（バランス・約8GB）"
        echo "  26B  … 高精度チャット（MoE・約16GB）"
        echo
        pull_one e2b
        pull_one e4b
        pull_one 12b
        pull_one 26b
        ;;
    *)
        echo "用法: $0 [e2b|e4b|12b|26b|31b|all]"
        exit 1
        ;;
esac

echo
echo "✅ 完了。インストール済み:"
ollama list | grep -i gemma || ollama list
