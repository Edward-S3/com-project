# 1on1面談シミュレーションアプリ

中星工業の人事評価マニュアルに準拠した、AIロールプレイ型1on1面談研修アプリです。

## 要件

- Python 3.10+
- Ubuntu Linux（`/opt/interview`）

## セットアップ

```bash
cd /opt/interview
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
# .env に API キーを設定
```

## P1 動作確認

### テスト

```bash
source .venv/bin/activate
pytest
```

### CLI（1ターン対話）

```bash
# .env なし → モックプロバイダ
python scripts/cli_chat.py --mode 1A -m "来期は品質向上に努めます"

# .env あり → 設定されたプロバイダ（ROLE_PARTNER）
python scripts/cli_chat.py --mode 1B -m "目標は検査ミス月3件以下です"
```

### FastAPI

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
curl http://localhost:8000/health
```

## HTTPS（音声モード用・P3）

ブラウザのマイク入力（`getUserMedia`）にはセキュアコンテキスト（HTTPS）が必要です。

### mkcert 手順

```bash
cd /opt/interview
sudo apt-get install -y mkcert libnss3-tools
mkcert -install   # ローカルCAをOS/ブラウザ信頼ストアへ
mkdir -p certs
# LAN内ホスト名 / IP を含める（例）
mkcert -cert-file certs/localhost+4.pem -key-file certs/localhost+4-key.pem \
  localhost 127.0.0.1 ::1 "$(hostname)" "$(hostname -I | awk '{print $1}')"

# .env にパスを設定
# SSL_CERTFILE=certs/localhost+4.pem
# SSL_KEYFILE=certs/localhost+4-key.pem
```

検証用クライアントPCでも `mkcert -install`（またはサーバのルートCAを配布）し、警告なしでマイク許可ダイアログが出ることを確認してください。

証明書ファイル（`certs/`・`*.pem`）は `.gitignore` 済みです。**コミットしないでください。**

### HTTPS 起動

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8443 \
  --ssl-keyfile=certs/localhost+4-key.pem \
  --ssl-certfile=certs/localhost+4.pem
# 例: https://172.16.16.10:8443/
```

### Live API スモークテスト（UI前の必須確認）

```bash
python scripts/live_smoke.py --mode text
```

### 音声受入（1A / 中小企業）

```bash
python scripts/run_voice_acceptance.py
# 証跡: docs/acceptance/P3/
```

## systemd サービス例

```ini
[Unit]
Description=1on1 Interview Simulation
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/interview
Environment="PATH=/opt/interview/.venv/bin"
ExecStart=/opt/interview/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## ディレクトリ

- `app/` — FastAPI アプリケーション
- `data/` — 等級・ルーブリック・部門マスタ、SQLite DB
- `scripts/` — CLI ツール
- `tests/` — pytest

## 仕様書

- `docs/SPECIFICATION.md`
- `docs/CURSOR_INSTRUCTIONS.md`
