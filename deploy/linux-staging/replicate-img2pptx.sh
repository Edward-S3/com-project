#!/bin/bash
# 本番 (172.16.16.10) から hp-server / 検証 Linux (172.16.16.13) へ img2pptx のみ複製
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"

STAGING_PASS='P@sswd#%'
SSH_OPTS=(-o StrictHostKeyChecking=no -i /root/.ssh/id_ed25519_staging)
remote_sudo() {
  sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
    "echo 'P@sswd#%' | sudo -S $*"
}

log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "=== Step 1: /opt/img2pptx を rsync 複製 (venv 除く) ==="
sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
  "rm -rf /tmp/img2pptx-sync && mkdir -p /tmp/img2pptx-sync"

rsync -avz --delete \
  --exclude='venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='temp/' \
  --exclude='output/*.pptx' \
  -e "sshpass -p 'P@sswd#%' ssh ${SSH_OPTS[*]}" \
  /opt/img2pptx/ "${STAGING_SSH}:/tmp/img2pptx-sync/"

sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
  "echo 'P@sswd#%' | sudo -S rsync -a --delete /tmp/img2pptx-sync/ /opt/img2pptx/ && rm -rf /tmp/img2pptx-sync"

log "=== Step 2: deploy 定義 (systemd / nginx / launcher) を同期 ==="
sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
  "rm -rf /tmp/img2pptx-deploy && mkdir -p /tmp/img2pptx-deploy"

rsync -avz \
  -e "sshpass -p 'P@sswd#%' ssh ${SSH_OPTS[*]}" \
  /opt/deploy/systemd/img2pptx.service \
  /opt/deploy/nginx/python_apps.conf \
  /opt/deploy/launcher/index.html \
  "${STAGING_SSH}:/tmp/img2pptx-deploy/"

log "=== Step 3: 検証サーバー上で venv 構築・サービス設定 ==="
sshpass -p "${STAGING_PASS}" scp "${SSH_OPTS[@]}" \
  "${SCRIPT_DIR}/setup-img2pptx-staging.sh" \
  "${STAGING_SSH}:/tmp/setup-img2pptx-staging.sh"

remote_sudo bash /tmp/setup-img2pptx-staging.sh

log "=== img2pptx 複製完了 ==="
log "アクセス: http://${STAGING_IP}/img2pptx/"
