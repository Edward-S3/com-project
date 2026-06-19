#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/opt"
DEPLOY_DIR="$REPO_ROOT/deploy/github"

usage() {
  cat <<'EOF'
Usage:
  push-to-github.sh create <owner/repo> [--public]
  push-to-github.sh push

Examples:
  push-to-github.sh create Edward-S3/com-project
  push-to-github.sh create Edward-S3/com-project --public
  push-to-github.sh push
EOF
}

require_auth() {
  if ! gh auth status >/dev/null 2>&1; then
    if [ -x /opt/deploy/github/gh-auth.sh ]; then
      /opt/deploy/github/gh-auth.sh || true
    fi
  fi
  if ! gh auth status >/dev/null 2>&1; then
    echo "ERROR: gh 未認証です。/opt/deploy/github/gh-auth.sh を実行してください。"
    exit 1
  fi
}

require_git_env() {
  if [ -z "${GIT_AUTHOR_NAME:-}" ] || [ -z "${GIT_AUTHOR_EMAIL:-}" ]; then
    if [ -f "$DEPLOY_DIR/git-env.example" ]; then
      echo "WARN: GIT_AUTHOR_* 未設定。必要なら source $DEPLOY_DIR/git-env.example"
    fi
  fi
}

cmd="${1:-}"
shift || true

case "$cmd" in
  create)
    repo="${1:-}"
    visibility="--private"
    if [ "${2:-}" = "--public" ]; then
      visibility="--public"
    fi
    if [ -z "$repo" ]; then
      usage
      exit 1
    fi
    require_auth
    require_git_env
    cd "$REPO_ROOT"
    if git remote get-url origin >/dev/null 2>&1; then
      echo "ERROR: origin は既に設定されています:"
      git remote -v
      exit 1
    fi
    gh repo create "$repo" $visibility --source=. --remote=origin --push
    echo "Done: https://github.com/$repo"
    ;;
  push)
    require_auth
    require_git_env
    cd "$REPO_ROOT"
    if ! git remote get-url origin >/dev/null 2>&1; then
      echo "ERROR: origin が未設定です。先に create コマンドを実行してください。"
      exit 1
    fi
    git push -u origin main
    ;;
  *)
    usage
    exit 1
    ;;
esac
