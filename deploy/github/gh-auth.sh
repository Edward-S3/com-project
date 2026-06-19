#!/usr/bin/env bash
# GitHub CLI 自動ログイン
# トークンを deploy/github/.github-token に保存するか、GH_TOKEN 環境変数を設定してください。
set -euo pipefail

TOKEN_FILE="${GITHUB_TOKEN_FILE:-/opt/deploy/github/.github-token}"

read_token() {
  local raw trimmed
  raw="$(cat "$1")"
  trimmed="$(printf '%s' "$raw" | tr -d ' \t\r\n')"
  if [ -z "$trimmed" ]; then
    echo "ERROR: トークンファイルが空です: $1" >&2
    exit 1
  fi
  case "$trimmed" in
    ghp_*|github_pat_*) ;;
    *)
      echo "ERROR: トークン形式が不正です（ghp_ または github_pat_ で始まる必要があります）" >&2
      exit 1
      ;;
  esac
  printf '%s' "$trimmed"
}

login_with_token() {
  local token="$1"
  local err
  if ! err="$(printf '%s' "$token" | gh auth login --with-token 2>&1)"; then
    if printf '%s' "$err" | grep -q "missing required scope"; then
      missing="$(printf '%s' "$err" | sed -n "s/.*missing required scope '\([^']*\)'.*/\1/p")"
      cat >&2 <<EOF

ERROR: トークンに必要なスコープが不足しています: ${missing:-unknown}

Classic PAT 作成時に次を両方チェックしてください:
  - repo
  - read:org  （組織を使わなくても gh CLI が要求します）

作成: https://github.com/settings/tokens/new

保存:
  printf '%s' 'ghp_新しいトークン' > /opt/deploy/github/.github-token
  chmod 600 /opt/deploy/github/.github-token
  /opt/deploy/github/gh-auth.sh

EOF
    else
      cat >&2 <<'EOF'

ERROR: トークン認証に失敗しました。

よくある原因:
  - トークンのコピー漏れ・余分な文字の混入
  - 期限切れ・削除済みトークン
  - 必要スコープ不足（repo, read:org）

対処:
  1. https://github.com/settings/tokens で古いトークンを削除
  2. https://github.com/settings/tokens/new で新規作成
     Scopes: repo と read:org にチェック
  3. 保存:

     printf '%s' 'ghp_ここにトークン' > /opt/deploy/github/.github-token
     chmod 600 /opt/deploy/github/.github-token
     /opt/deploy/github/gh-auth.sh

EOF
    fi
    exit 1
  fi
}

if gh auth status >/dev/null 2>&1; then
  echo "gh: 既にログイン済み ($(gh api user -q .login 2>/dev/null || echo 'unknown'))"
  exit 0
fi

if [ -n "${GH_TOKEN:-}" ]; then
  login_with_token "$(printf '%s' "$GH_TOKEN" | tr -d ' \t\r\n')"
elif [ -f "$TOKEN_FILE" ]; then
  login_with_token "$(read_token "$TOKEN_FILE")"
else
  cat <<EOF
ERROR: GitHub トークンが見つかりません。

次の手順で Personal Access Token (PAT) を作成してください:

  1. https://github.com/settings/tokens/new を開く
  2. Note: com-project-server など任意の名前
  3. Expiration: 任意
  4. Scopes: 次の2つにチェック
       - repo
       - read:org  （個人アカウントのみでも gh CLI に必要）
  5. Generate token をクリックし、表示されたトークンをコピー

トークンを保存:

  printf '%s' 'ghp_xxxxxxxx' > $TOKEN_FILE
  chmod 600 $TOKEN_FILE

再度実行:

  /opt/deploy/github/gh-auth.sh

EOF
  exit 1
fi

echo "gh: ログイン成功 ($(gh api user -q .login))"
