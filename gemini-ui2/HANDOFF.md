# 社内 AI アシスタント（Gemini Chat / NAI）— 引継ぎレポート

作成日: 2026-06-08  
最終更新: 2026-06-08（Office 添付・許可モデル連動・UI/UX・Cursor Rules 反映）

## 1. プロジェクト概要

中星工業 社内向けの Open WebUI 風 AI チャットシステム。  
Google Gemini API を中心に、OpenAI / Anthropic / ローカル LLM（Ollama / Gemma 4）を選択可能。  
利用ログ・ユーザー管理・権限制御を備えた社内専用ツール。

**プロジェクト名（社内呼称）:** NAI / Gemini Chat / 社内 AI アシスタント

**関連仕様書:** `追加機能20260608-001.md`（音声文字起こし・画像生成の参考資料）

---

## 2. ディレクトリ構成

```
/opt/gemini-ui/
├── app.py                  # 利用者向けメイン UI（Streamlit）
├── admin.py                # 管理者パネル（Streamlit）
├── db.py                   # SQLite 操作層
├── llm_providers.py        # マルチプロバイダ LLM 抽象化・リトライ・自動選択
├── office_files.py         # xlsx / docx / pptx 読込・編集・出力
├── ui_common.py            # バナー / ログイン画面 CSS 等の共通 UI
├── sync_env_job.py         # .env 再読み込み + LLM 同期バッチ（cron / 管理画面共用）
├── .cursor/
│   └── rules/
│       └── nai-handoff.mdc # Cursor Agent 共通前提（alwaysApply）
├── gemini_ui.db            # SQLite DB（本番データ）
├── .env                    # API キー・ADMIN_PASSWORD（機密・コミット禁止）
├── .env.example            # 環境変数テンプレート
├── requirements.txt        # Python 依存パッケージ
├── venv/                   # 専用 Python 仮想環境
├── .streamlit/
│   └── config.toml         # Streamlit 設定（maxUploadSize=500）
├── tmp/uploads/            # 添付ファイル一時保存（処理後削除）
├── banner.png              # 画面上部ロゴ（jpg/jpeg/png も可）
├── memo.txt                # チャット画面サイドバーのお知らせテキスト
├── HANDOFF.md              # 本引継ぎドキュメント
├── 追加機能20260608-001.md # 音声・画像生成機能の仕様メモ
├── logs/
│   └── sync_env_cron.log   # 深夜 LLM 同期バッチのログ
└── scripts/
    ├── start.sh            # 手動起動用（ポート 8507、maxUploadSize 500）
    ├── start_admin.sh      # 管理パネル手動起動（ポート 8508）
    ├── install_gemma4.sh   # Gemma 4 モデルを Ollama にインストール
    └── run_sync_env_cron.sh # 深夜 LLM 同期バッチ用ラッパー
```

**依存環境:**

- Python 3.10
- 仮想環境: `/opt/gemini-ui/venv/`（faq-bot とは分離済み）
- 主要パッケージ: streamlit, google-genai, python-dotenv, pandas, openai, anthropic, requests, **cryptography**, **pypdf**, **openpyxl**, **python-docx**, **python-pptx**

---

## 3. デプロイ・インフラ

### systemd サービス

| サービス | ポート | バインド | 備考 |
|---------|--------|---------|------|
| gemini-ui.service | 8507 | 172.16.16.10 | `--server.maxUploadSize 500` |
| gemini-ui-admin.service | 8508 | 172.16.16.10 | |

ExecStart は `/opt/gemini-ui/venv/bin/streamlit` を使用。

### appctl

| 別名 | 用途 |
|------|------|
| `nai` | チャット UI |
| `naictrl` | 管理パネル |

```bash
appctl restart nai naictrl
appctl status nai naictrl
```

### Nginx（`/etc/nginx/conf.d/python_apps.conf`）

| パス | 転送先 | 備考 |
|------|--------|------|
| `/nai/` | `http://172.16.16.10:8507/` | **`client_max_body_size 500M;`** |
| `/nai-ctrl/` | `http://172.16.16.10:8508/` | |

**重要:** 利用者は `/nai/` 経由でアクセスするため、Nginx の `client_max_body_size` が実質的なアップロード上限になる。グローバル `nginx.conf` は 20M のままだが、`/nai/` location で 500M に上書き済み。

### アップロード上限（3 層）

| 層 | 設定場所 | 現在値 |
|----|---------|--------|
| Nginx | `/etc/nginx/conf.d/python_apps.conf` → `location /nai/` | **500 MB** |
| Streamlit | `.streamlit/config.toml` + systemd `--server.maxUploadSize` | **500 MB** |
| アプリ（DB） | `settings.upload_max_mb_default` + `users.upload_max_mb` | グローバル **50 MB**（ユーザー別で上書き可） |

実効上限 = 3 層のうち**最も小さい値**。大きな音声ファイルを許可する場合は DB 設定（グローバルまたはユーザー別）も引き上げること。

### cron

| 時刻 | ジョブ | ログ |
|------|--------|------|
| 0:30 | NAI LLM 同期 | `/opt/gemini-ui/logs/sync_env_cron.log` |

手動実行: `/opt/gemini-ui/scripts/run_sync_env_cron.sh`  
管理画面: **⚙️ システム設定 → 🔄 LLM 更新**

### アクセス URL

| 対象 | URL |
|------|-----|
| チャット | `http://<サーバーIP>/nai/` |
| 管理パネル | `http://<サーバーIP>/nai-ctrl/` |

---

## 4. 環境変数（.env）

テンプレート: `/opt/gemini-ui/.env.example`

| 変数 | 用途 |
|------|------|
| `GOOGLE_API_KEY` / `GOOGLE_API_KEYS` | Gemini（複数キーはカンマ区切り・レート制限時ローテーション） |
| `OPENAI_API_KEY` / `OPENAI_API_KEYS` | OpenAI（任意） |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_API_KEYS` | Anthropic（任意） |
| `ADMIN_PASSWORD` | 初期 `admin` ユーザーのパスワード |
| `PASSWORD_REF_KEY` | 管理者参照用パスワードの暗号化キー（未設定時は `ADMIN_PASSWORD` を使用） |
| `OLLAMA_BASE_URL` | ローカル LLM（デフォルト `http://127.0.0.1:11434`） |
| `OLLAMA_ENABLED` | `0` でローカル LLM 無効化 |
| `LOCAL_LLM_ROUTER_MODEL` | 自動選択ルーター（推奨: `gemma4:e2b`） |

**2026-06-08 時点:** `GOOGLE_API_KEY` と `ADMIN_PASSWORD` のみ設定。OpenAI / Anthropic キーは未設定のため、選択肢は **Gemini + ローカル LLM** のみ表示される。

**重要:** API キーが `.env` に無いプロバイダのモデルは選択肢に表示されない。キー削除後は **サービス再起動** または **LLM 更新** で反映。

---

## 5. 認証・ユーザー

### 初期管理者（`admin`）

- 社員番号: `admin`
- パスワード: `.env` の `ADMIN_PASSWORD`
- `db.ensure_admin_user()` により **`employee_id=admin` が存在しない場合に自動作成**
- `app.py` と `admin.py` の起動時に実行

### 認証方式

- PBKDF2-SHA256（20万回）で `password_hash` を保存
- 管理者参照用パスワードは **Fernet 暗号化**（`enc:v1:...` 形式）で `plain_password` カラムに保存
- 管理画面では **「パスワードを表示」ボタン** で復号表示（常時表示しない）
- 利用者はサイドバーから自身のパスワード変更可能（**`password_change_allowed=0` の場合は不可**）
- **セッションタイムアウト:** 10 分間未操作で自動ログアウト（`SESSION_TIMEOUT_SEC = 600`）

### ユーザー別 LLM・テンプレート制御

| 設定 | 場所 | 動作 |
|------|------|------|
| 使用可能モデル | ユーザー管理 → 使用可能モデル | 空=全モデル。指定時はサイドバーのモデル選択をフィルタ |
| デフォルト LLM | ユーザー管理 → デフォルト LLM | 空=グローバル設定に従う。実効値は `db.get_effective_default_model()` |
| 表示ラベル | 管理画面「チャット画面の初期表示」/ チャット「ユーザー既定 LLM」 | `db.format_user_default_model_label()` で統一 |
| 初期選択モデル | ログイン時 | 自動選択ではなく **実効デフォルト LLM** を選択 |
| テンプレート一覧 | チャットサイドバー | **`db.get_active_templates_for_user()`** — テンプレート `default_model` が許可モデルに含まれるもののみ表示（空=常に表示） |
| 初期テンプレート | ログイン時 / 新規チャット | **ID=1「汎用アシスタント」**（許可モデルに合う場合） |
| 実行時モデル | テンプレート使用中 | `resolve_effective_model()` — テンプレート LLM → ユーザー既定 → サイドバー選択 → 許可リスト先頭 |

### パスワード変更制限（テスト公開用）

| 設定 | 場所 | 値 |
|------|------|-----|
| `password_change_allowed` | ユーザー管理（編集・新規） | `1`=利用者変更可（デフォルト） / `0`=**制限**（チャットの変更 UI 非表示） |
| 一覧 | ユーザー管理「PW変更」列 | 許可 / 制限 |

管理者によるパスワード変更（編集画面の「新しいパスワード」欄）は制限時も可能。

### 登録ユーザー（2026-06-08 時点・6 件）

| 社員番号 | 氏名 | 備考 |
|---------|------|------|
| admin | 管理者 | 管理者 |
| e.suzuki | 鈴木　悦夫 | 管理者 |
| TEST | テスト用アカウント | テスト公開用（PW 変更制限の例） |
| （他） | kondo / akahane / h.kamoda 等 | 一般ユーザー |

※ グローバルデフォルト LLM（`settings.model`）は **`local:gemma4-26b`**（2026-06-08 時点）。

### 添付ファイル容量制御

| 設定 | 場所 | 値の意味 |
|------|------|---------|
| グローバル | 管理画面 → システム設定 → 添付ファイル | `upload_max_mb_default`（0=無制限） |
| ユーザー別 | 管理画面 → ユーザー管理 → 添付ファイル設定 | `-1`=グローバル従う / `0`=無制限 / 正の数=MB |
| 一覧表示 | ユーザー管理の「添付上限」列 | `db.format_user_upload_limit()` |

許可形式（グローバルデフォルト）:  
`jpg,jpeg,png,gif,webp,pdf,txt,csv,md,xlsx,docx,pptx,mp3,wav,aac,flac,m4a,ogg`

---

## 6. データベース（gemini_ui.db）

### 主要テーブル

| テーブル | 用途 |
|---------|------|
| users | ユーザー（default_model, allowed_models, upload, **password_change_allowed**, plain_password 等） |
| chat_sessions / chat_messages | チャット履歴 |
| query_logs | 利用ログ |
| prompt_templates | プロンプトテンプレート（**default_model**, **allow_empty_prompt**, **ID は 1 始まり連番**） |
| feedback / daily_usage / settings | 評価・日次制限・システム設定 |

### マイグレーション

- `db.init_db()` 起動時に `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE`
- テンプレート ID の 1 始まり振り直しは `_migrate_template_ids_from_one()` で一度だけ実行（設定 `_template_ids_from_one=1`）
- 既存平文 `plain_password` は `_migrate_password_refs()` で暗号化形式へ移行
- 音声形式追加: `_migrate_upload_audio_types()`（設定 `_upload_audio_types_migrated=1`）
- Office 形式追加: `_migrate_upload_office_types()`（`xlsx,docx,pptx`、設定 `_upload_office_types_migrated=1`）
- 音声テンプレート空プロンプト: `_migrate_audio_template_empty_prompt()`（設定 `_audio_empty_prompt_migrated=1`）
- メディアテンプレート追加: `ensure_media_templates()`（欠落時のみ投入）

### プロンプトテンプレート（2026-06-08 時点）

| ID | 名称 | カテゴリ | デフォルト LLM | 空プロンプト |
|----|------|---------|----------------|-------------|
| 1 | 汎用アシスタント | 汎用 | （未指定） | 不可 |
| 2 | 翻訳（日本語→英語） | 翻訳 | local:gemma4-e4b | 不可 |
| 3 | 翻訳（英語→日本語） | 翻訳 | local:gemma4-e4b | 不可 |
| 4 | 文章要約(1,000文字未満) | 要約 | local:gemma4-e4b | 不可 |
| 5 | 文書要約(1,000文字以上) | 要約 | gemini-3.5-flash | 不可 |
| 6 | 文書作成支援 | 文書 | local:gemma4-e4b | 不可 |
| 7 | メール文章作成 | 文書 | local:gemma4-e4b | 不可 |
| 9 | コードレビュー | 開発 | local:gemma4-26b | 不可 |
| 13 | データ分析支援 | 分析 | local:gemma4-26b | 不可 |
| 14 | イメージデータ生成 | 画像 | gemini-2.5-flash-image | 不可 |
| 15 | 音声文字起こし | 音声 | gemini-3.5-flash | **可** |
| 16 | 議事録作成 | 音声 | gemini-3.5-flash | **可** |

- デフォルトテンプレートの再投入は **DB が空のときのみ**（削除後の自動復活はしない）
- ID=1 の「汎用アシスタント」のみ `ensure_core_templates()` で欠落時に復元
- **空プロンプト可**（`allow_empty_prompt=1`）は管理画面テンプレート管理で変更可能

---

## 7. 機能一覧（現状）

### 利用者画面（app.py）

- ログイン / 10 分セッションタイムアウト
- マルチターン会話（ストリーミング）
- チャットセッション管理（**コンパクトな履歴一覧**・スクロール `session_list`）
- モデル選択（API キー設定済み + **ユーザー許可モデルのみ**）
- **初期モデル = ユーザー/グローバル既定 LLM**（自動選択は手動選択時のみ）
- **自動選択**（テンプレート未使用時。Gemma 4 E2B ルーター + ローカル Gemma 4 も回答候補）
- プロンプトテンプレート（**許可モデルに合うもののみ表示**、初期は ID=1）
- Web 検索（Gemini のみ。音声・画像生成テンプレートでは無効）
- **ファイル添付**（画像 / PDF / テキスト / **Office（xlsx, docx, pptx）** / **音声**）
- **Excel 添付** — シート一覧表示 + **`st.data_editor` で編集** → 編集済み xlsx をダウンロード
- **Word / PowerPoint 出力** — プロンプトから docx/pptx 生成 → ダウンロード（`<!--NAI_OFFICE:...-->` マーカー）
- **音声文字起こし・議事録**（Gemini + 音声添付。20MB 超は File API）
- **画像生成**（`gemini-2.5-flash-image`、テンプレート ID=14）
- **生成画像の保存**（📥 画像を保存 — `st.download_button`）
- **回答コピー**（📋 コピー → ポップオーバー内で実行）
- **空プロンプト実行**（`allow_empty_prompt=1` のテンプレート。音声添付後に「▶ 実行（プロンプト省略）」）
- フィードバック（👍/👎）
- サイドバー: バナー + memo.txt お知らせ + **「📎 会話操作」**（エクスポート等）+ 履歴
- **パスワード変更**（`password_change_allowed=1` のユーザーのみ）

### 添付ファイルのライフサイクル

1. アップロード → `/opt/gemini-ui/tmp/uploads/` に一時保存（セッションにはパスのみ保持）
2. LLM 処理（大きな音声は Gemini File API 経由、処理後 API 側も削除）
3. 処理完了後（成功・エラーとも）ローカル一時ファイル削除
4. ログアウト・セッション切替・タイムアウト時も削除
5. 起動時に 1 時間超の古いファイルを `_cleanup_stale_uploads()` で掃除

### ログイン画面（利用者・管理者共通）

- **バナー画像のみ**表示（コンパクト、幅約 220px）。memo.txt は表示しない
- 実装: `ui_common.render_login_banner()` / `ui_common.render_login_page_style()`

### 管理者画面（admin.py）

| タブ | 機能 |
|------|------|
| ダッシュボード | 統計 |
| クエリログ | 絞り込み・CSV |
| フィードバック | 満足度 |
| ユーザー管理 | CRUD・権限・LLM・**添付上限**・**PW 変更許可**・再表示・削除確認 |
| テンプレート管理 | CRUD・ID 編集・デフォルト LLM・**空プロンプト可**・再表示・削除確認 |
| システム設定 | グローバル設定・**添付ファイル上限/形式**・LLM 更新ボタン |

**UI パターン（テンプレート / ユーザー管理共通）:**

- 一覧上部に **🔄 再表示** ボタン
- 保存・追加・削除後は `session_state` フラッシュメッセージ + ページ末尾で `st.rerun()` により一覧を更新
- 削除は **2 段階確認**（削除 → 削除を実行 / キャンセル）

---

## 8. LLM プロバイダ（llm_providers.py）

### クラウド API（.env のキーがある場合のみ表示）

| プロバイダ | 主なモデル ID |
|-----------|--------------|
| Google | gemini-3.5-flash, gemini-3.1-pro-preview, gemini-3.1-flash-lite, gemini-2.5-pro, gemini-2.5-flash, **gemini-2.5-flash-image** |
| OpenAI | gpt-5.5, gpt-5.4, gpt-5.4-mini, gpt-4.1, gpt-4o, o4-mini, o3-mini 等 |
| Anthropic | claude-sonnet-4-6, claude-opus-4-6, claude-haiku-4-5-20251001 |

### ローカル LLM（Ollama）

**インストール済み:** gemma4:e2b, gemma4:e4b, gemma4:26b, llama3.2:latest

| Ollama タグ | 用途 |
|-------------|------|
| gemma4:e2b | 自動選択ルーター専用（回答候補から除外） |
| gemma4:e4b / gemma4:26b | 通常チャット + 自動選択の回答候補 |

### 自動選択（`__auto__`）

1. テンプレート使用中は**無効**
2. 回答候補: クラウド + ローカル Gemma 4（E4B/26B 等。**E2B は除外**）
3. Gemma 4 E2B ルーターで判定、未起動時はルールベース
4. 添付ファイルあり時はマルチモーダル対応モデルを優先

### ファイル添付（`FileAttachment`）

`data`（bytes）または `path`（一時ファイルパス）のいずれかで保持。

| 種別 | 対応 | 備考 |
|------|------|------|
| 画像 | Gemini / OpenAI / Anthropic / Ollama | inline または base64 |
| PDF | 全プロバイダ（テキスト抽出） | pypdf |
| テキスト | 全プロバイダ | txt/csv/md |
| **Office** | **全プロバイダ（テキスト抽出）** | xlsx（openpyxl）/ docx / pptx。xlsx は UI で編集可 |
| **音声** | **Gemini のみ** | mp3/wav/aac/flac/m4a/ogg。≤20MB は inline、超過は File API |

### 画像生成（`gemini-2.5-flash-image`）

- `response_modalities=['IMAGE', 'TEXT']` でストリーミング
- 生成画像は `st.image` 表示 + 履歴に Markdown 埋め込み（base64）
- **📥 画像を保存** — ダウンロードボタン（履歴表示では巨大 base64 を省略表示）
- ファイル添付と同時利用不可
- **API 送信時の履歴サニタイズ** — `_sanitize_message_content()` で埋め込み画像・Office マーカーを除去（1M トークン超過防止）。画像生成時は履歴 **最大 12 件**

### レート制限リトライ

- Google / OpenAI / Anthropic で `KeyManager` によるキーローテーション + 待機リトライ

---

## 9. 改修履歴（累積）

### 基盤・インフラ（2026-06 前半）

| 項目 | 内容 |
|------|------|
| 専用 venv | `/opt/gemini-ui/venv/` |
| Nginx `/nai-ctrl/` | 管理パネル公開 |
| appctl | `nai` / `naictrl` 登録 |
| 深夜 LLM 同期 | cron 0:30 + `sync_env_job.py` |
| 管理画面 LLM 更新ボタン | システム設定タブ |
| マルチプロバイダ LLM | Gemini / OpenAI / Anthropic / Ollama |
| セッションタイムアウト | 10 分 |
| ユーザー/テンプレート別 default_model | DB + 管理画面 |
| 添付ファイル設定 | グローバル + ユーザー別 MB/形式 |

### 2026-06-08 後半（UI 安定化・セキュリティ）

| 項目 | 内容 |
|------|------|
| ファイル添付マルチプロバイダ化 | OpenAI/Anthropic/Ollama 対応 |
| 自動選択強化 | ローカル Gemma 4 を回答候補に含める |
| plain_password 暗号化 | Fernet + 管理画面 reveal-on-demand |
| テンプレート/ユーザー管理 UI | フラッシュ + 再表示 + 削除確認 |
| ログイン画面バナー | `ui_common.py` |

### 2026-06-08 追加セッション（メディア・添付・UX）

| 項目 | 内容 |
|------|------|
| **メディアテンプレート追加** | イメージデータ生成(14)・音声文字起こし(15)・議事録作成(16) |
| **音声添付対応** | Gemini File API（大容量）+ inline（≤20MB） |
| **画像生成** | `gemini-2.5-flash-image` + ストリーミング画像表示 |
| **添付一時ファイル化** | `tmp/uploads/` 保存、処理後削除、起動時掃除 |
| **アップロード上限 3 層** | Nginx 500M / Streamlit 500M / DB ユーザー別 |
| **Nginx `/nai/` 修正** | `client_max_body_size 500M`（旧 20M グローバル制限が原因で大容量音声が失敗していた） |
| **ユーザー別添付上限** | 一覧「添付上限」列、新規ユーザー作成時も設定可 |
| **回答コピー機能** | ポップオーバー + ワンクリックコピー + st.code フォールバック |
| **空プロンプト実行** | `allow_empty_prompt` カラム、管理画面で制御、音声テンプレートはデフォルト有効 |

### 2026-06-08 後続セッション（Office・権限制御・UI）

| 項目 | 内容 |
|------|------|
| **Office ファイル** | `office_files.py` — xlsx 読込/編集/出力、docx/pptx 読込・LLM 出力 |
| **添付形式拡張** | xlsx, docx, pptx（DB マイグレーション + 管理画面） |
| **画像保存** | 生成画像の 📥 ダウンロード、履歴のコンパクト表示 |
| **画像生成トークン対策** | API 履歴から base64 画像を除去、12 件上限 |
| **許可モデル連動** | テンプレート一覧フィルタ、実行時フォールバック |
| **デフォルト LLM 表示統一** | 管理「チャット画面の初期表示」= チャット「ユーザー既定 LLM」 |
| **初期テンプレート ID=1** | 新規チャットは汎用アシスタント（許可モデルに合う場合） |
| **PW 変更制限** | `password_change_allowed` カラム、管理画面チェックボックス |
| **サイドバー UX** | 履歴コンパクト化、スクロール、`📎 会話操作` セクション |
| **Cursor Project Rule** | `.cursor/rules/nai-handoff.mdc`（alwaysApply） |

### 過去に修正したバグ（引き継ぎ時の注意）

| 症状 | 原因 | 対処 |
|------|------|------|
| 大容量音声が赤エラーで添付不可 | Nginx グローバル 20M 制限 | `/nai/` location に 500M 設定済み |
| 管理画面 `format_user_upload_limit` エラー | サービス再起動前に古い db モジュール | **`nai` と `naictrl` 両方**再起動 |
| 管理画面 `format_user_default_model_label` エラー | 同上（`naictrl` のみ古い `db.py`） | **`appctl restart nai naictrl`** |
| 画像生成 `400 INVALID_ARGUMENT`（1M+ tokens） | 履歴の base64 画像を API に再送 | `_sanitize_message_content()` + 12 件上限 |
| 利用者画面 `_cleanup_stale_uploads` エラー | 関数定義前に呼び出し | 定義後に移動済み |
| コピーボタンで変化なし | iframe 内クリップボード + 非表示フィードバック | ポップオーバー方式に変更済み |

---

## 10. New Agent への引継ぎ指示

### 役割

既存 Streamlit ベース社内 AI チャットの改修・機能追加。新規実装より既存コードの拡張を優先。

**追加機能の指示は New Agent 開始後にユーザーから改めて行われる。**

### 必読ファイル

| ファイル | 内容 |
|---------|------|
| `/opt/gemini-ui/HANDOFF.md` | 本ドキュメント |
| `/opt/gemini-ui/.cursor/rules/nai-handoff.mdc` | Cursor Agent 共通前提（HANDOFF §10 要約） |
| `/opt/gemini-ui/llm_providers.py` | LLM 抽象化（音声・画像・Office 添付・履歴サニタイズ） |
| `/opt/gemini-ui/office_files.py` | xlsx / docx / pptx |
| `/opt/gemini-ui/db.py` | DB スキーマ・マイグレーション・テンプレート・許可モデル |
| `/opt/gemini-ui/app.py` | 利用者 UI（添付・Office・コピー・空プロンプト・画像保存） |
| `/opt/gemini-ui/admin.py` | 管理画面 |
| `/opt/gemini-ui/ui_common.py` | バナー・ログイン CSS |
| `/opt/gemini-ui/sync_env_job.py` | LLM 同期バッチ |
| `/opt/gemini-ui/追加機能20260608-001.md` | 音声・画像の API 参考 |

### 必守事項

1. 作業ディレクトリ: `/opt/gemini-ui`
2. Python: `/opt/gemini-ui/venv/bin/`
3. API キー・パスワードをコミット・ログ・画面に出さない
4. モデル一覧は `llm.get_available_models()` が正
5. `.env` 変更後は LLM 更新を実行
6. 変更後: `appctl restart nai naictrl`
7. **Nginx / systemd のアップロード上限変更時は nginx reload も検討**
8. **コミットはユーザー明示依頼時のみ**

### 作業前確認コマンド

```bash
appctl status nai naictrl
cd /opt/gemini-ui && venv/bin/python3 -m py_compile app.py admin.py db.py llm_providers.py office_files.py sync_env_job.py ui_common.py
venv/bin/python3 -c "
import db; db.init_db()
print(len(db.get_all_users()), 'users', len(db.get_all_templates()), 'templates')
print('upload types:', db.get_setting('upload_allowed_types_default'))
for t in db.get_all_templates():
    if t.get('allow_empty_prompt') or t['category'] in ('音声','画像'):
        print(' ', t['id'], t['name'], 'empty_ok=', t.get('allow_empty_prompt'))
"
ollama list
grep -E 'maxUploadSize|client_max_body_size' /etc/systemd/system/gemini-ui.service /etc/nginx/conf.d/python_apps.conf
```

### アーキテクチャ

```
app.py / admin.py
  ├─ llm_providers.py  … Gemini / OpenAI / Anthropic / Ollama / 音声 / 画像 / Office 添付
  ├─ office_files.py   … xlsx 編集 / docx・pptx 出力
  ├─ ui_common.py      … バナー / ログイン CSS
  ├─ db.py             … SQLite（テンプレート・許可モデル・添付・PW 制限）
  └─ sync_env_job.py   … .env 再読み込み + DB 補正

Cursor
  └─ .cursor/rules/nai-handoff.mdc … Agent 共通前提（alwaysApply）

インフラ
  ├─ Nginx /nai/       … client_max_body_size 500M
  ├─ Streamlit         … maxUploadSize 500
  └─ tmp/uploads/      … 添付一時保存（処理後削除）
```

### New Agent への推奨初回指示（コピー用）

```markdown
/opt/gemini-ui の社内 AI アシスタント（NAI）を引継ぎします。

1. まず `/opt/gemini-ui/HANDOFF.md` を全文読み、現状を把握してください。
2. 作業ディレクトリは `/opt/gemini-ui`、Python は `venv/bin/` を使用してください。
3. HANDOFF §10 の確認コマンドを実行し、結果を簡潔に報告してください。
4. コード変更は行わず、確認のみ行ってください。問題があれば報告してください。
5. 機能追加・改修の指示は、この確認完了後に別途出します。
6. git コミットは明示依頼があるまで行わないでください。
```

機能追加を依頼する際は、次の形式を推奨します。

```markdown
【対象】利用者画面 / 管理画面 / LLM / DB / インフラ のいずれかを明記

【目的】何を達成したいか（1〜2 文）

【要件】
- 必須条件を箇条書き
- 画面・操作単位で書く

【スコープ外】今回触らないもの（あれば）

【確認方法】完了後にどう確認すればよいか
```

### 音声テンプレートの動作確認手順（参考）

1. テンプレート「音声文字起こし」または「議事録作成」を選択
2. 音声ファイル（m4a/mp3 等）を添付
3. **▶ 実行（プロンプト省略）** をクリック（または追加指示を入力して Enter）
4. 文字起こし / 議事録が返ることを確認
5. **📋 コピー** で結果をクリップボードに取得できることを確認

### Office ファイルの動作確認手順（参考）

1. **xlsx 添付** — Excel を添付 → シート内容表示 → `st.data_editor` で編集 → 編集済み xlsx をダウンロード
2. **docx / pptx 添付** — 内容が LLM プロンプトにテキスト抽出されることを確認
3. **docx / pptx 出力** — 「Word で出力」「PowerPoint で作成」等の指示 → ダウンロードボタン表示
4. **許可モデル制限** — 許可外 LLM のテンプレートが一覧に出ないこと、実行時フォールバックを確認

### テスト公開ユーザー（TEST）の確認例

- 管理画面: 使用可能モデルを 2 件に限定、**PW 変更を制限**
- チャット: テンプレートが許可モデル分のみ、パスワード変更 UI 非表示、初期 LLM が管理画面表示と一致

---

## 11. 関連システム（同サーバー）

| アプリ | パス | ポート | Nginx |
|--------|------|--------|-------|
| 社内 FAQ ボット | /opt/faq-bot | 8501 | /nk-faq/ |
| 社員評価ツール | /opt/employee_eval_tool | 8502 | /rep-eva/ |
| **本システム** | /opt/gemini-ui | 8507/8508 | /nai/ , /nai-ctrl/ |

Google API キーは faq-bot と共有可能。

---

## 12. クイックリファレンス

```bash
# サービス再起動
appctl restart nai naictrl

# Nginx 設定反映（アップロード上限変更時）
sudo nginx -t && sudo systemctl reload nginx

# LLM 同期（.env 変更後）
/opt/gemini-ui/scripts/run_sync_env_cron.sh

# 構文チェック
cd /opt/gemini-ui && venv/bin/python3 -m py_compile app.py admin.py db.py llm_providers.py office_files.py sync_env_job.py ui_common.py

# DB 状態確認
cd /opt/gemini-ui && venv/bin/python3 -c "
import db; db.init_db()
print('upload global:', db.get_setting('upload_max_mb_default'), 'MB')
for u in db.get_all_users():
    print(u['employee_id'], db.format_user_upload_limit(u['employee_id'], u))
"

# 依存追加時
venv/bin/pip install -r requirements.txt

# Gemma 4 追加
bash /opt/gemini-ui/scripts/install_gemma4.sh all

# admin ユーザー復旧（admin が無い場合）
cd /opt/gemini-ui && venv/bin/python3 -c "
import os; from dotenv import load_dotenv; import db
load_dotenv('.env'); db.init_db(); db.ensure_admin_user(os.getenv('ADMIN_PASSWORD',''))
"

# 古い一時添付ファイル手動掃除
find /opt/gemini-ui/tmp/uploads -type f -mmin +60 -delete
```
