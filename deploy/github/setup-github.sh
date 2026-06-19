#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/opt"
DEPLOY_DIR="$REPO_ROOT/deploy/github"

echo "=== GitHub 連携セットアップ ==="
echo

# 1. 前提チェック
for cmd in git gh ssh; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: $cmd が見つかりません。"
    exit 1
  fi
done

if [ ! -f "$HOME/.ssh/id_ed25519_github.pub" ]; then
  echo "ERROR: SSH 公開鍵がありません。先に SSH 鍵を生成してください。"
  exit 1
fi

# 2. SSH 公開鍵の表示
echo "[1/4] GitHub に登録する SSH 公開鍵:"
echo "----------------------------------------"
cat "$HOME/.ssh/id_ed25519_github.pub"
echo "----------------------------------------"
echo
echo "GitHub → Settings → SSH and GPG keys → New SSH key"
echo "上記の鍵を追加してください。"
echo

# 3. GitHub CLI 認証
echo "[2/4] GitHub CLI 認証"
if gh auth status >/dev/null 2>&1; then
  echo "  既に認証済み:"
  gh auth status
else
  echo "  未認証です。自動ログイン:"
  echo
  echo "  # 1) PAT を作成: https://github.com/settings/tokens/new"
  echo "  #    Scopes: repo と read:org にチェック"
  echo "  # 2) トークンを保存"
  echo "  echo \"ghp_YOUR_TOKEN\" > /opt/deploy/github/.github-token"
  echo "  chmod 600 /opt/deploy/github/.github-token"
  echo "  # 3) 自動ログイン実行"
  echo "  /opt/deploy/github/gh-auth.sh"
  echo
  echo "  または手動: gh auth login"
fi
echo

# 4. Git 作者情報
echo "[3/4] Git 作者情報"
if [ -f "$DEPLOY_DIR/git-env.example" ]; then
  echo "  $DEPLOY_DIR/git-env.example を編集し、source してください。"
  echo "  例: source $DEPLOY_DIR/git-env.example"
else
  echo "  git-env.example が見つかりません。"
fi
echo

# 5. リモート接続テスト & リポジトリ作成手順
echo "[4/4] リポジトリ作成・push 手順"
echo
echo "  # SSH 接続確認（鍵登録後）"
echo "  ssh -T git@github.com"
echo
echo "  # 新規リポジトリ作成して push（個人アカウント: Edward-S3）"
echo "  cd $REPO_ROOT"
echo "  /opt/deploy/github/push-to-github.sh create Edward-S3/com-project"
echo
echo "  # 既存リポジトリに接続する場合"
echo "  cd $REPO_ROOT"
echo "  git remote add origin git@github.com:Edward-S3/com-project.git"
echo "  git push -u origin main"
echo
