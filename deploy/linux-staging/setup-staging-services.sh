#!/bin/bash
# 検証サーバー上で Nginx / systemd / 環境変数を本番同等に設定 (IP は検証用)
set -euo pipefail

PROD_IP="172.16.16.10"
STAGING_IP="172.16.16.13"
DEPLOY="/opt/deploy"

# --- Nginx ---
mkdir -p /etc/nginx/conf.d /etc/nginx/ssl
sed "s/${PROD_IP}/${STAGING_IP}/g" "${DEPLOY}/nginx/python_apps.conf" \
  > /etc/nginx/conf.d/python_apps.conf
sed "s/${PROD_IP}/${STAGING_IP}/g" "${DEPLOY}/nginx/python_apps-ssl.conf" \
  > /etc/nginx/conf.d/python_apps-ssl.conf

if [ -f /etc/nginx/ssl/mescheck.crt ]; then
  : # rsync でコピー済み
elif [ -f "${DEPLOY}/../mescheck/.env" ]; then
  # 本番から別途コピーされる想定
  true
fi

# --- ランチャー ---
mkdir -p /var/www/html
cp "${DEPLOY}/launcher/index.html" /var/www/html/index.html

# --- systemd ユニット (IP 置換) ---
SERVICES=(
  employee-eval.service
  exam-app.service
  faq-bot-a.service
  faqlog.service
  fback.service
  gemini-ui.service
  gemini-ui-admin.service
  gemini-ui2.service
  gemini-ui2-admin.service
  mescheck.service
  tts.service
  server-status-api.service
  pptx-gen.service
  img2pptx.service
  pptx-gen-an.service
  pptx-gen2.service
)

for svc in "${SERVICES[@]}"; do
  sed "s/${PROD_IP}/${STAGING_IP}/g" "${DEPLOY}/systemd/${svc}" \
    > "/etc/systemd/system/${svc}"
done

# --- .env のホスト名を検証 IP に (本番 DB スナップショットは独立コピー) ---
for envfile in /opt/exam/.env /opt/fback/.env /opt/mescheck/.env /opt/gemini-ui/.env /opt/gemini-ui2/.env /opt/employee_eval_tool/.env /opt/faq-bot/.env; do
  [ -f "$envfile" ] && sed -i "s/${PROD_IP}/${STAGING_IP}/g" "$envfile"
done
[ -f /opt/exam/.env ] && grep -q '^EXAM_HOST=' /opt/exam/.env || echo "EXAM_HOST=${STAGING_IP}" >> /opt/exam/.env
[ -f /opt/fback/.env ] && grep -q '^FBACK_HOST=' /opt/fback/.env || echo "FBACK_HOST=${STAGING_IP}" >> /opt/fback/.env

# mescheck REDIRECT_URI
if [ -f /opt/mescheck/.env ]; then
  sed -i "s|https://${PROD_IP}/mescheck/|https://${STAGING_IP}/mescheck/|g" /opt/mescheck/.env
  sed -i "s|http://${PROD_IP}|http://${STAGING_IP}|g" /opt/mescheck/.env
fi

# --- サービス有効化・起動 ---
systemctl daemon-reload
for svc in "${SERVICES[@]}"; do
  systemctl enable "$svc"
  systemctl restart "$svc" || systemctl start "$svc" || true
done

nginx -t
systemctl enable nginx
systemctl restart nginx

echo "Staging services configured for ${STAGING_IP}"
