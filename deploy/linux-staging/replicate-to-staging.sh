#!/bin/bash
# 本番サーバー (172.16.16.10) から検証サーバー (172.16.16.13) へ複製
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"

STAGING_PASS='P@sswd#%'
SSH_OPTS=(-o StrictHostKeyChecking=no -i /root/.ssh/id_ed25519_staging)
REMOTE_SUDO=(sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
  "echo '${STAGING_PASS}' | sudo -S")

log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "=== Step 1: 検証サーバーにパッケージインストール ==="
sshpass -p "${STAGING_PASS}" scp "${SSH_OPTS[@]}" \
  "${SCRIPT_DIR}/install-packages-staging.sh" \
  "${STAGING_SSH}:/tmp/install-packages-staging.sh"
"${REMOTE_SUDO[@]}" bash /tmp/install-packages-staging.sh

log "=== Step 2: /opt を rsync 複製 (アプリ + venv + DB) ==="
sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
  "echo '${STAGING_PASS}' | sudo -S mkdir -p /opt && sudo chown -R nakaboshi:nakaboshi /opt"

rsync -avz --progress \
  "${RSYNC_EXCLUDES[@]}" \
  -e "sshpass -p '${STAGING_PASS}' ssh ${SSH_OPTS[*]}" \
  /opt/ "${STAGING_SSH}:/opt/"

log "=== Step 3: Nginx SSL 証明書コピー ==="
sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
  "echo '${STAGING_PASS}' | sudo -S mkdir -p /etc/nginx/ssl"
sshpass -p "${STAGING_PASS}" scp "${SSH_OPTS[@]}" \
  /etc/nginx/ssl/mescheck.crt /etc/nginx/ssl/mescheck.key \
  "${STAGING_SSH}:/tmp/"
sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
  "echo '${STAGING_PASS}' | sudo -S mv /tmp/mescheck.crt /tmp/mescheck.key /etc/nginx/ssl/ && sudo chmod 600 /etc/nginx/ssl/mescheck.key"

log "=== Step 4: Ollama モデル複製 (約42GB, 時間がかかります) ==="
sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
  "echo '${STAGING_PASS}' | sudo -S mkdir -p /usr/share/ollama && sudo chown -R ollama:ollama /usr/share/ollama 2>/dev/null || sudo chown -R nakaboshi:nakaboshi /usr/share/ollama"

rsync -avz --progress \
  -e "sshpass -p '${STAGING_PASS}' ssh ${SSH_OPTS[*]}" \
  /usr/share/ollama/ "${STAGING_SSH}:/tmp/ollama-sync/"

sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
  "echo '${STAGING_PASS}' | sudo -S rsync -a /tmp/ollama-sync/ /usr/share/ollama/ && sudo rm -rf /tmp/ollama-sync && (id ollama &>/dev/null && sudo chown -R ollama:ollama /usr/share/ollama || true)"

log "=== Step 5: systemd / Nginx / サービス設定 ==="
sshpass -p "${STAGING_PASS}" scp "${SSH_OPTS[@]}" \
  "${SCRIPT_DIR}/setup-staging-services.sh" \
  "${STAGING_SSH}:/tmp/setup-staging-services.sh"
"${REMOTE_SUDO[@]}" bash /tmp/setup-staging-services.sh

log "=== Step 6: Ollama サービス起動 ==="
sshpass -p "${STAGING_PASS}" ssh "${SSH_OPTS[@]}" "${STAGING_SSH}" \
  "echo '${STAGING_PASS}' | sudo -S systemctl enable ollama 2>/dev/null; echo '${STAGING_PASS}' | sudo -S systemctl restart ollama 2>/dev/null || true"

log "=== 複製完了 ==="
log "検証サーバー: http://${STAGING_IP}/"
log "各アプリは本番と同じパス (/nk-faq/, /exam/, /nai/ 等) でアクセス可能です"
