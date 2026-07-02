# 引き継ぎ資料：Windows 11 検証機への `/opt` アプリ移行

**作成日:** 2026-06-24  
**用途:** 本番 Linux（172.16.16.10）上の Cursor から、検証機 Windows 11（172.16.16.13）上の Cursor へ作業を引き継ぐための資料

---

## 1. プロジェクトの目的

本番 Linux サーバー（172.16.16.10）のスペック不足により、以下が未達・ボトルネックになっている。

- `/opt/gemini-ui` の **ローカル LLM（Ollama / Gemma4）** が本番で展開できない
- `/opt/faq-bot` 等の **AI 処理速度** が不十分
- **10数名の同時アクセス** に耐える性能があるか未確認

**検証機（Windows 11 / 172.16.16.13）で、本番と同じアプリを Windows ネイティブ Python で動かし、性能を検証する。**

### 重要な方針（決定済み）

| 項目 | 方針 |
|------|------|
| 実行環境 | **Windows ネイティブ Python**（WSL2 は使わない） |
| 本番への影響 | **ゼロ**（読み取りコピーのみ、本番は継続稼働） |
| gemini-ui2 | **検証不要**（gemini-ui のサブセット） |
| Python 環境 | **アプリごとに独立 venv** |
| ディレクトリ | `C:\AIWork\opt\` に本番 `/opt` と同構成で配置 |
| パス互換 | `C:\opt` → `C:\AIWork\opt` のジャンクションで `/opt/...` ハードコードを吸収 |

---

## 2. 環境情報

### 検証機（作業対象）

| 項目 | 値 |
|------|-----|
| OS | Windows 11（HP 貸出 PC、**OS 再インストール不可**） |
| IP | **172.16.16.13** |
| CPU | Ryzen Threadripper PRO 9975WX（32コア / 4.0GHz） |
| RAM | 128GB |
| ストレージ | SSD 1.86TB |
| GPU | NVIDIA RTX PRO 5000 Blackwell（48GB VRAM） |
| 作業フォルダ | `C:\AIWork\`（`C:\aiwork` として既存） |

### 本番 Linux サーバー（参照・コピー元のみ）

| 項目 | 値 |
|------|-----|
| IP | **172.16.16.10** |
| ホスト名 | `nkbsystem-QandABot` |
| Python | 3.10.12 |
| アプリ配置 | `/opt/` |
| 構成 | systemd + Nginx リバースプロキシ |
| 役割 | 本番継続（触らない） |

### ネットワーク状況（本番側から確認済み）

- 本番 Linux（172.16.16.10）から検証機（172.16.16.13）への **ping / SSH(22) / SMB(445) / WinRM(5985) はすべてタイムアウト**
- OpenSSH Server のインストールを試行中だが、`sshd` サービス未作成の状態で止まっている
- **Windows 上の Cursor からローカル操作が主**となる

---

## 3. 検証対象アプリ一覧

### 検証対象（優先順）

| 優先 | アプリ | ポート | 主な AI 処理 | ローカル LLM |
|------|--------|--------|-------------|-------------|
| **1** | gemini-ui | 8507 / 8508(admin) | Ollama(Gemma4) + クラウド API | **◎ 標準対応** |
| **2** | employee_eval_tool | 8502 | Ollama / Gemini 切替 | **◎ 標準対応** |
| **3** | faq-bot | 8501 | Gemini Embedding + Chat | △ 現状クラウド固定 |
| **4** | exam | 8505 | Gemini API | △ |
| **4** | fback | 8503 | Gemini API（exam/.env 参照） | △ |
| **5** | tts | 8509 | Gemini TTS（gemini-ui の .env/DB 参照） | △ |
| 後回し | mescheck | 8511 | MS Graph（AI 以外、Azure OAuth） | — |

### 検証対象外

| アプリ | 理由 |
|--------|------|
| **gemini-ui2** | gemini-ui のサブセットのため不要 |
| containerd/ | Linux コンテナランタイム |
| google/chrome/ | Linux 用 Chrome |
| ressrv/, meschkeck/ | 空または未使用 |

### 本番ポート・Nginx パス対応表

| パス | ポート | アプリ |
|------|--------|--------|
| `/nk-faq/` | 8501 | faq-bot |
| `/rep-eva/` | 8502 | employee_eval_tool |
| `/sby/` | 8503 | fback |
| `/exam/` | 8505 | exam |
| `/nai/` | 8507 | gemini-ui |
| `/nai-ctrl/` | 8508 | gemini-ui admin |
| `/tts/` | 8509 | tts |
| `/api/server-status/` | 8510 | server_status_api (gemini-ui内) |
| `/mescheck/` | 8511 | mescheck |

---

## 4. ディレクトリ構成（目標）

```
C:\AIWork\opt\
  ├── gemini-ui\          ← venv + .env + gemini_ui.db
  ├── faq-bot\            ← venv + chroma_db\ + documents\
  ├── employee_eval_tool\ ← venv
  ├── exam\               ← venv + exam_app.db + .env
  ├── fback\              ← venv + assets\
  ├── tts\                ← venv
  ├── shared\             ← user_admin 等
  └── deploy\             ← start.bat 等（Windows 用に新規作成）

C:\opt  →  C:\AIWork\opt  （ジャンクション、管理者権限で作成）
```

### ジャンクションが必要な理由

以下のコードが `/opt/...` をハードコードしている。ジャンクション `C:\opt` を作れば **コード変更なし**で動く。

| ファイル | ハードコードパス |
|---------|----------------|
| `fback/app.py` | `/opt/exam/.env`, `/opt/fback/assets/` |
| `exam/exam_app.py` | `/opt/fback/assets/ipaexg.ttf` |
| `shared/user_admin/constants.py` | `/opt/exam/exam_app.db`, `/opt/fback/...` |
| `shared/user_admin/fback_store.py` | `/opt/exam/.env`, `/opt/fback/.env` |
| `tts/tts_engine.py` | `/opt/gemini-ui/.env` |
| `tts/db.py` | `/opt/gemini-ui/gemini_ui.db` |

```powershell
# 管理者 PowerShell で一度だけ
cmd /c mklink /J C:\opt C:\AIWork\opt
```

---

## 5. セットアップ手順（Windows 側で実施）

### フェーズ0：ベース環境

```powershell
# 1. Python 3.10 または 3.11（本番 3.10.12 に合わせる）
#    https://www.python.org/downloads/  「Add python.exe to PATH」にチェック

# 2. Git for Windows（任意、コピー管理用）

# 3. Ollama for Windows
#    https://ollama.com/download
ollama --version
nvidia-smi

# 4. フォルダ作成
New-Item -ItemType Directory -Force -Path C:\AIWork\opt

# 5. ジャンクション
cmd /c mklink /J C:\opt C:\AIWork\opt

# 6. Ollama 並列設定（10数名同時利用向け、システム環境変数）
# OLLAMA_NUM_PARALLEL=4
# OLLAMA_MAX_LOADED_MODELS=2

# 7. ファイアウォール（社内 LAN からのアクセス許可）
# 8501-8513/TCP
```

### フェーズ1：本番からファイルコピー

本番 Linux で実行（読み取りのみ、本番サービス停止不要）:

```bash
# gemini-ui（最優先）
rsync -avz --progress \
  --exclude='venv/' --exclude='__pycache__/' --exclude='.git/' \
  /opt/gemini-ui/ \
  <Windowsユーザー>@172.16.16.13:/c/AIWork/opt/gemini-ui/
```

**rsync が使えない場合の代替:**

- USB メモリ経由で `tar czf` → 持ち込み
- SMB 共有（Windows で共有フォルダを開き、本番から `scp`）
- 手動コピー（`C:\AIWork\opt\` へ）

**コピー時の除外:**

```
venv/  .venv/  __pycache__/  .git/
containerd/  google/  ressrv/  meschkeck/
```

**コピーすべきランタイムデータ:**

- `faq-bot/chroma_db/`（検証用スナップショット）
- `faq-bot/documents/`
- 各アプリの `.env`（検証用に後で編集）
- `*.db` ファイル（gemini_ui.db, exam_app.db 等）

**本番でアーカイブを作る場合:**

```bash
sudo tar czf /tmp/gemini-ui-$(date +%Y%m%d).tar.gz \
  --exclude='venv' --exclude='__pycache__' \
  -C /opt gemini-ui
```

### フェーズ2：gemini-ui 構築（最優先）

```powershell
cd C:\AIWork\opt\gemini-ui
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

**requirements.txt（本番と同じ）:**

```
streamlit>=1.40.0
google-genai>=1.0.0
python-dotenv>=1.0.0
pandas>=2.0.0
openai>=1.50.0
anthropic>=0.40.0
requests>=2.31.0
cryptography>=42.0.0
pypdf>=4.0.0
openpyxl>=3.1.0
tabulate>=0.9.0
xlwings>=0.30.0
python-docx>=1.1.0
python-pptx>=1.0.0
```

**`.env`（検証用）:**

```ini
GOOGLE_API_KEY=<検証用キー>
OLLAMA_BASE_URL=http://127.0.0.1:11434
LOCAL_LLM_ROUTER_MODEL=gemma4:e2b
ADMIN_PASSWORD=<検証用パスワード>
```

**Ollama モデル:**

```powershell
ollama pull gemma4:e2b    # ルーター用（約2GB VRAM）
ollama pull gemma4:e4b    # 通常チャット（約4GB）
ollama pull gemma4:12b    # バランス型（約8GB）
ollama pull gemma4:26b    # 高精度（約16GB）
ollama list
```

**起動スクリプト `start.bat`:**

```bat
@echo off
cd /d C:\AIWork\opt\gemini-ui
call venv\Scripts\activate.bat
streamlit run app.py ^
  --server.port 8507 ^
  --server.address 0.0.0.0 ^
  --server.headless true ^
  --browser.gatherUsageStats false ^
  --server.maxUploadSize 500
```

**管理画面 `start_admin.bat`:**

```bat
@echo off
cd /d C:\AIWork\opt\gemini-ui
call venv\Scripts\activate.bat
streamlit run admin.py ^
  --server.port 8508 ^
  --server.address 0.0.0.0 ^
  --server.headless true ^
  --browser.gatherUsageStats false
```

**確認項目:**

- [ ] `http://localhost:8507` で UI 表示
- [ ] 管理画面で `local:gemma4-*` モデルが一覧に出る
- [ ] ローカルモデルでチャット応答が返る
- [ ] 自動選択（`__auto__`）が動作する

### フェーズ3：同時アクセス性能テスト（判断の本丸）

| シナリオ | 内容 |
|---------|------|
| A | gemini-ui 単体、5→10→15→20 ユーザー、モデル e4b / 26b |
| B | gemini-ui(10人) + faq-bot(5人) 同時 |
| C | 全アプリ起動 + gemini-ui 10ユーザー |

**記録フォーマット:**

| 同時ユーザー | モデル | P50応答 | P95応答 | GPU VRAM | CPU% | RAM(GB) | エラー率 |
|------------|--------|---------|---------|----------|------|---------|---------|
| 5 | e4b | | | | | | |
| 10 | e4b | | | | | | |
| 15 | e4b | | | | | | |
| 10 | 26b | | | | | | |
| 20 | e4b | | | | | | |

**合格基準（目安）:**

| 指標 | 合格ライン |
|------|----------|
| P95 応答（通常チャット e4b） | < 10秒 |
| P95 応答（26b 高精度） | < 20秒 |
| 同時15ユーザー エラー率 | < 1% |
| GPU VRAM ピーク | < 44GB |
| 30分連続稼働 | クラッシュなし |

### フェーズ4以降：残アプリ（順次）

| 順 | アプリ | requirements | 特記事項 |
|----|--------|-------------|---------|
| 2 | employee_eval_tool | streamlit, google-generativeai, pandas, python-docx, ... | `.env` に `LLM_PROVIDER=local` |
| 3 | faq-bot | ※下記参照 | `requirements.txt` は本番が壊れている（Ubuntu pip freeze） |
| 4 | exam + fback | streamlit, pandas, google-generativeai, ... | セットで移行、`C:\opt` ジャンクション必須 |
| 5 | tts | streamlit, google-genai, ... | gemini-ui 完了後（.env/DB 参照） |

**employee_eval_tool 用 `.env` 例:**

```ini
LLM_PROVIDER=local
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:e4b
```

**faq-bot 用 requirements（Windows 向けに新規作成が必要）:**

本番の `requirements.txt` は Ubuntu システムパッケージの pip freeze で使えない。以下を基準に作成:

```
streamlit
python-dotenv
langchain-community
langchain-text-splitters
langchain-google-genai
langchain-chroma
langchain-core
langchain-classic
unstructured
```

**exam requirements.txt:**

```
streamlit
pandas
matplotlib
google-generativeai
fpdf
python-dotenv
```

**tts requirements.txt:**

```
streamlit>=1.40.0
google-genai>=1.0.0
python-dotenv>=1.0.0
pandas>=2.0.0
```

---

## 6. 本番分離チェックリスト

| 項目 | 検証機での対応 |
|------|--------------|
| IP | `172.16.16.13` または `0.0.0.0`（本番 `172.16.16.10` とは別マシン） |
| `.env` | 検証専用コピー（本番を編集しない） |
| DB | 検証用スナップショット（本番 DB を直接参照しない） |
| SMTP / メール | 無効化 or テスト用 |
| Ollama | 検証機ローカルのみ |
| 本番への書き込み | **一切なし** |
| 本番サービス停止 | **不要** |

---

## 7. OpenSSH Server の状況（未完了）

本番 Linux からのリモート操作を試みたが、現状未接続。

**発生したエラー:**

```
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
→ Path が空（インストール未完了）

Start-Service sshd
→ サービス 'sshd' が見つかりません

New-NetFirewallRule -Name "OpenSSH-Server-In-TCP"
→ 既に存在（ルールのみ先に作成済み）
```

**Windows 側で続きのトラブルシュート:**

```powershell
# 状態確認
Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH*'
Get-Service *ssh* -ErrorAction SilentlyContinue
sc.exe query sshd
where.exe sshd

# 再インストール
Remove-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
# State が Installed になること

# ダメなら winget
winget install --id Microsoft.OpenSSH.Beta -e --accept-source-agreements --accept-package-agreements

# または 設定 → システム → オプション機能 → 「OpenSSH サーバー」を追加

# System32 にある場合の手動登録
cd C:\Windows\System32\OpenSSH
.\install-sshd.ps1

# 成功後
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
Get-Service sshd

# ローカル確認
ssh localhost "hostname"
```

SSH は必須ではない。**Windows 上の Cursor からローカル作業が主**。

---

## 8. gemini-ui ローカル LLM の技術詳細

- ローカル LLM は **Ollama** 経由（`llm_providers.py`）
- モデルは `local:gemma4-e4b` 等として UI に表示
- ルーター: `gemma4:e2b`（`LOCAL_LLM_ROUTER_MODEL`）
- API: `OLLAMA_BASE_URL=http://127.0.0.1:11434`
- 無効化: `OLLAMA_ENABLED=0`
- DB: `gemini_ui.db`（アプリ直下、相対パスなので Windows でもそのまま動く）

**インストール済みモデル（本番想定）:**

| Ollama タグ | 用途 | VRAM 目安 |
|-------------|------|----------|
| gemma4:e2b | 自動選択ルーター専用 | 約2GB |
| gemma4:e4b | 通常チャット | 約4GB |
| gemma4:12b | バランス型 | 約8GB |
| gemma4:26b | 高精度チャット | 約16GB |

**自動選択（`__auto__`）の流れ:**

1. テンプレート使用中は無効
2. 回答候補: クラウド + ローカル Gemma 4（E2B は除外）
3. Gemma 4 E2B ルーターで判定、未起動時はルールベース
4. 添付ファイルあり時はマルチモーダル対応モデルを優先

---

## 9. ハードコードパス・環境変数一覧

| 環境変数 | デフォルト | 検証機での変更 |
|---------|-----------|--------------|
| `EXAM_HOST` | `172.16.16.10` | `172.16.16.13` |
| `FBACK_HOST` | `172.16.16.10` | `172.16.16.13` |
| `EXAM_DB_PATH` | `/opt/exam/exam_app.db` | ジャンクションで対応 |
| `FBACK_DB_PATH` | `/opt/fback/universal_feedback.db` | ジャンクションで対応 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | そのまま |
| `LLM_PROVIDER` (employee_eval_tool) | `local` | そのまま |
| `REDIRECT_URI` (mescheck) | `https://172.16.16.10/mescheck/` | 後回し（Azure 再登録が必要） |

---

## 10. 作業スケジュール目安

| 日 | 作業 | 成果物 |
|----|------|--------|
| 1日目 | フェーズ0 + gemini-ui 構築 | Ollama + gemini-ui 稼働 |
| 2日目 | 同時アクセス性能テスト | 性能データ・判断材料 |
| 3日目 | employee_eval_tool + faq-bot | AI 系アプリ稼働 |
| 4日目 | exam/fback + 複合負荷テスト | 検証結果レポート |

---

## 11. 検証結果の判断フロー

```
gemini-ui ローカルLLM稼働
    ↓
10名同時 P95 < 10秒？
    ├─ No → OLLAMA_NUM_PARALLEL 調整 / モデルサイズ変更
    └─ Yes
         ↓
    15名同時も合格？
         ├─ No → 「10名までが実用上限」として記録
         └─ Yes → 検証機はAIサーバーとして適正 ◎
                    ↓
              全アプリ同時起動も安定？
                    ├─ Yes → 本番→検証機への役割移行を推奨
                    └─ No → AI系と業務系の分離運用を推奨
```

---

## 12. Windows Cursor への最初の指示（コピペ用）

```
以下の引き継ぎに基づき、Windows 11 検証機（172.16.16.13）で作業を続けてください。

目的:
- 本番 Linux（172.16.16.10）の /opt アプリを Windows ネイティブで検証
- gemini-ui のローカル LLM（Ollama/Gemma4）展開と 10数名同時アクセス性能の確認
- 本番への影響はゼロ

作業フォルダ: C:\AIWork\opt\
方針: Windows ネイティブ Python、アプリごと venv、C:\opt ジャンクション
gemini-ui2 は検証不要

まず実施してほしいこと:
1. Python 3.10/3.11、Ollama、nvidia-smi の確認
2. C:\AIWork\opt\gemini-ui\ の存在確認（なければ本番からコピー手順を案内）
3. C:\opt → C:\AIWork\opt ジャンクション作成
4. gemini-ui の venv 構築と pip install
5. ollama pull gemma4:e2b, e4b, 12b, 26b
6. .env 作成と start.bat で起動確認
7. http://localhost:8507 でローカル LLM 動作確認

本番 Linux からの SSH は現在未接続。ローカル操作で進めること。
```

---

## 13. 本番リポジトリ情報

- `/opt` 直下に `.git` あり（モノレポ構成）
- `.gitignore` で `venv/`, `.env`, `chroma_db/`, `*.db` 等を除外
- デプロイ設定: `/opt/deploy/systemd/`, `/opt/deploy/nginx/`
- ランチャーページ: `/opt/deploy/launcher/index.html`

---

## 14. ファイルの所在

| ファイル | パス |
|---------|------|
| 本引き継ぎ資料 | `/opt/HANDOFF_WINDOWS_MIGRATION.md`（本番 Linux） |
| gemini-ui ソース | `/opt/gemini-ui/` |
| gemini-ui .env 例 | `/opt/gemini-ui/.env.example` |
| gemini-ui 詳細ドキュメント | `/opt/gemini-ui/HANDOFF.md` |
| systemd サービス定義 | `/opt/deploy/systemd/` |
| Nginx 設定 | `/opt/deploy/nginx/python_apps.conf` |

---

*End of document*
