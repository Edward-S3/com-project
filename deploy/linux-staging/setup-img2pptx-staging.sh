#!/bin/bash
# hp-server (172.16.16.13) 上で img2pptx を独立稼働させる
set -euo pipefail

PROD_IP="172.16.16.10"
STAGING_IP="172.16.16.13"
APP_DIR="/opt/img2pptx"
DEPLOY_TMP="/tmp/img2pptx-deploy"

mkdir -p "${APP_DIR}/output" "${APP_DIR}/temp"
chown -R root:root "${APP_DIR}"

if [ ! -f /opt/gemini-ui/.env ]; then
  echo "警告: /opt/gemini-ui/.env がありません。Gemini API キーを設定してください。"
fi

log() { echo "[setup-img2pptx] $*"; }

log "Python venv 構築..."
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip -q
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

log "systemd ユニット配置..."
sed "s/${PROD_IP}/${STAGING_IP}/g" "${DEPLOY_TMP}/img2pptx.service" \
  > /etc/systemd/system/img2pptx.service

log "Nginx 設定更新..."
CONF="/etc/nginx/conf.d/python_apps.conf"
if [ -f "${DEPLOY_TMP}/python_apps.conf" ]; then
  if grep -q 'location /img2pptx/' "${CONF}" 2>/dev/null; then
    log "Nginx: /img2pptx/ は既に存在 — IP のみ置換"
    sed -i "s/${PROD_IP}/${STAGING_IP}/g" "${CONF}"
  else
    log "Nginx: python_apps.conf をマージ（img2pptx ブロック追加）"
    cp "${CONF}" "${CONF}.bak.$(date +%Y%m%d_%H%M%S)"
    sed "s/${PROD_IP}/${STAGING_IP}/g" "${DEPLOY_TMP}/python_apps.conf" > "${CONF}.new"
    if grep -q 'location /img2pptx/' "${CONF}.new"; then
      mv "${CONF}.new" "${CONF}"
    else
      rm -f "${CONF}.new"
      awk '
        /location \/pptx\// { inpptx=1 }
        inpptx && /^[[:space:]]*}[[:space:]]*$/ && !done {
          print
          print ""
          print "    # --- 画像PPTX変換 (8515番) /img2pptx/ ---"
          print "    location /img2pptx/ {"
          print "        client_max_body_size 50M;"
          print "        proxy_pass http://'"${STAGING_IP}"':8515/;"
          print "    }"
          done=1; inpptx=0; next
        }
        { print }
      ' "${CONF}" > "${CONF}.new"
      mv "${CONF}.new" "${CONF}"
    fi
  fi
fi

log "ランチャー更新..."
if [ -f "${DEPLOY_TMP}/index.html" ]; then
  mkdir -p /var/www/html
  cp "${DEPLOY_TMP}/index.html" /var/www/html/index.html
fi

log "サービス起動..."
systemctl daemon-reload
systemctl enable img2pptx.service
systemctl restart img2pptx.service
nginx -t
systemctl reload nginx

systemctl is-active img2pptx.service
log "img2pptx ready on ${STAGING_IP}:8515 (/img2pptx/)"
