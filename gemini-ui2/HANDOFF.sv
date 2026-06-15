# 社内 AI アシスタント（Gemini Chat / NAI）— 引継ぎレポート

作成日: 2026-06-08

## 1. プロジェクト概要

中星工業 社内向けの Open WebUI 風 AI チャットシステム。
Google Gemini API（有償プラン）を使用し、社員がブラウザから汎用 AI チャットを利用できる。
利用ログ・ユーザー管理・権限制御を備えた社内専用ツール。

**プロジェクト名（社内呼称）:** NAI / Gemini Chat / 社内 AI アシスタント

---

## 2. ディレクトリ構成

```
/opt/gemini-ui/
├── app.py              # 利用者向けメイン UI（Streamlit）
├── admin.py            # 管理者パネル（Streamlit）
├── db.py               # SQLite 操作層
├── gemini_ui.db        # SQLite DB（本番データ）
├── .env                # GOOGLE_API_KEY, ADMIN_PASSWORD
├── banner.png          # 画面上部ロゴ（jpg/png も可）
├── memo.txt            # バナー下のお知らせテキスト
├── HANDOFF.md          # 本引継ぎドキュメント
└── scripts/
    ├── start.sh        # 手動起動用（ポート 8507）
    └── start_admin.sh  # 管理パネル手動起動（ポート 8508）
```

**依存環境:**

- Python 3.10
- 仮想環境: `/opt/faq-bot/venv/`（faq-bot と共有）
- 主要パッケージ: streamlit, google-genai, python-dotenv, pandas

---

## 3. デプロイ・インフラ

### systemd サービス（自動起動済み）

| サービス | ファイル | ポート | バインド |
|---------|---------|--------|---------|
| gemini-ui.service | `/etc/systemd/system/gemini-ui.service` | 8507 | 172.16.16.10 |
| gemini-ui-admin.service | `/etc/systemd/system/gemini-ui-admin.service` | 8508 | 172.16.16.10 |

```bash
systemctl restart gemini-ui.service gemini-ui-admin.service
journalctl -u gemini-ui.service -f
```

### Nginx

- 設定: `/etc/nginx/conf.d/python_apps.conf`
- パス: `/nai/` → `http://172.16.16.10:8507/`
- 管理パネルは Nginx 未登録（`:8508` 直アクセスのみ）

### ランチャーページ

- `/var/www/html/index.html` に「社内 AI アシスタント」カードあり（`/nai/`）

### アクセス URL

| 対象 | URL |
|------|-----|
| ランチャー | `http://<サーバーIP>/` |
| チャット（推奨） | `http://<サーバーIP>/nai/` |
| チャット（直接） | `http://172.16.16.10:8507/` |
| 管理パネル | `http://172.16.16.10:8508/` |

---

## 4. 認証・ユーザー

### 初期管理者

- 社員番号: `admin`
- パスワード: `/opt/gemini-ui/.env` の `ADMIN_PASSWORD`
- 初回起動時に `db.ensure_admin_user()` で自動作成

### 認証方式

- PBKDF2-SHA256（20万回）でハッシュ保存
- 管理者参照用に `plain_password` カラムへ平文も保存（意図的仕様）
- ユーザーはサイドバーから自身でパスワード変更可能

### 現在の登録ユーザー（2026-06-08 時点）

- admin（管理者）
- 0375 鈴木　悦夫（管理者）
- T001 田中 太郎
- U002 鈴木 花子

---

## 5. データベース（gemini_ui.db）

### テーブル一覧

| テーブル | 用途 |
|---------|------|
| users | ユーザー（employee_id, password_hash, plain_password, is_admin, daily_limit, web_search_enabled, allowed_models） |
| chat_sessions | チャットセッション（employee_id 紐付け） |
| chat_messages | メッセージ履歴（log_id でフィードバックと連携） |
| query_logs | 利用ログ（JST, 氏名, 部署, 問い合わせ, 回答, Token, Web検索使用等） |
| prompt_templates | プロンプトテンプレート（8種プリセット） |
| feedback | 👍/👎 評価（log_id 紐付け） |
| daily_usage | 日次利用カウント（employee_id + date_jst） |
| settings | システム設定（key-value） |

### マイグレーション

- `db.init_db()` 起動時に `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` で後方互換
- 新カラム追加時は migrations リストに追記するパターン

### 現在のシステム設定

| キー | 値 |
|------|-----|
| model | gemini-3.5-flash |
| temperature | 0.7 |
| max_output_tokens | 8192 |
| daily_limit_default | 50 |
| web_search_allowed | 1（グローバル許可） |

---

## 6. 機能一覧

### 利用者画面（app.py）

- ログイン（社員番号 + パスワード）
- マルチターン会話（ストリーミング）
- チャットセッション管理（新規・切替・削除・エクスポート）
- **モデル選択**（ユーザー許可モデルのみ、サイドバー）
- **プロンプトテンプレート**選択（翻訳・要約・コードレビュー等 8種）
- **Web検索トグル**（ユーザー権限 + グローバル設定で制御）
- **ファイル添付**（JPG/PNG/GIF/WebP/PDF/TXT/CSV/MD、最大50MB）
- **フィードバック**（👍/👎、回答ごと）
- 日次利用状況表示（プログレスバー）
- パスワード変更（サイドバー）
- バナー画像 + memo.txt 表示

### 管理者画面（admin.py）

| タブ | 機能 |
|------|------|
| ダッシュボード | 総件数・今日・モデル別・部署別・日次推移 |
| クエリログ | 絞り込み・ページネーション・CSV出力・詳細表示 |
| フィードバック | 満足度・最近の評価一覧 |
| ユーザー管理 | CRUD・Web検索可否・モデル制限・パスワード参照 |
| テンプレート管理 | CRUD・有効/無効・表示順 |
| システム設定 | モデル・Temperature・Max Token・プロンプト・日次上限・Web検索 |

### 権限制御の優先順位

**Web検索:**

- ユーザー `web_search_enabled`: 1=許可 / 0=禁止 / -1=グローバル設定に従う
- グローバル `settings.web_search_allowed`: 1/0

**モデル:**

- ユーザー `allowed_models`: 空=全モデル / カンマ区切りで制限

**日次上限:**

- ユーザー `daily_limit`: 0=無制限 / -1=グローバル / 正数=その値
- グローバル `settings.daily_limit_default`: デフォルト 50

---

## 7. 使用可能な Gemini モデル（2026-06-05 確認済み）

| モデル ID | 表示名 | 知識カットオフ（目安） |
|-----------|--------|---------------------|
| gemini-3.5-flash | 最新・推奨（デフォルト） | 2025年1月 |
| gemini-3.1-pro-preview | 最高精度 | 2024年1月頃 |
| gemini-3.1-flash-lite | 軽量・低コスト | 2024年1月 |
| gemini-2.5-pro | 安定版高精度 | 2023年初頭* |
| gemini-2.5-flash | 安定版高速 | 2023年初頭* |

**廃止済み（404、リストから削除済み）:** gemini-2.0-flash, gemini-2.0-flash-thinking-exp, gemini-1.5-flash, gemini-1.5-pro

**API:** 有償プラン確認済み。学習データへの利用なし（有償 API 規約）。

---

## 8. 技術的な実装メモ

### Gemini API 呼び出し（app.py `stream_response`）

- `google.genai.Client` + `client.chats.create()` + `send_message_stream()`
- Web検索: `types.Tool(google_search=types.GoogleSearch())`
- ファイル: `types.Part(inline_data=types.Blob(...))` でインライン送信
- システムプロンプト: テンプレート選択時はテンプレート優先、未選択時は settings

### UI

- 白背景・濃い文字色のライトテーマ（CUSTOM_CSS）
- Streamlit `st.chat_message` / `st.chat_input`
- 管理者向け設定（Temperature等）は app.py サイドバーに**無し**（admin.py のみ）

### ログ

- 日時: JST（`db.jst_now()`）
- 1問い合わせ = 1 query_log レコード + chat_messages 2件（user/assistant）

---

## 9. 既知の課題・未実装・改善候補

| 項目 | 状態 |
|------|------|
| 管理パネルの Nginx 経由アクセス | 未実装（:8508 のみ） |
| 専用 Python venv | 未作成（faq-bot venv 共有） |
| plain_password 平文保存 | 意図的（管理者参照要件）だがセキュリティリスクあり |
| 複数 API キーのローテーション | 未実装（faq-bot にはある） |
| レート制限時の自動リトライ | 未実装 |
| 添付ファイルのサイズ・種類の管理者設定 | 未実装（ハードコード 50MB） |
| ユーザーのセッションタイムアウト | 未実装 |
| HTTPS / 認証の二要素化 | 未実装 |

### 過去に修正したバグ

- `get_all_users()` が `web_search_enabled` 等を SELECT していなかった → 修正済み（admin.py ユーザー管理の AttributeError）

---

## 10. 関連システム（同サーバー）

| アプリ | パス | ポート | Nginx |
|--------|------|--------|-------|
| 社内FAQボット | /opt/faq-bot | 8501 | /nk-faq/ |
| 社員評価ツール | /opt/employee_eval_tool | 8502 | /rep-eva/ |
| アンケート | - | 8503 | /sby/ |
| 試験システム | /opt/exam | 8505 | /exam/ |
| **本システム** | /opt/gemini-ui | 8507/8508 | /nai/ |

API キーは `/opt/faq-bot/.env` の `GOOGLE_API_KEYS` と同一。

---

## 11. 改修時の作業フロー（推奨）

1. `/opt/gemini-ui` でコード確認・編集
2. `python3 -m py_compile app.py admin.py db.py` で構文チェック
3. 必要なら `db.init_db()` でマイグレーション確認
4. `systemctl restart gemini-ui.service gemini-ui-admin.service`
5. ブラウザで `/nai/` と `:8508` を確認
6. コミットはユーザー依頼時のみ

---

## 12. New Agent への引継ぎ指示（テンプレート）

```markdown
# 引継ぎ指示 — 社内 AI アシスタント（/opt/gemini-ui）

## あなたの役割
既存の Streamlit ベース社内 AI チャットシステムの改修・機能追加を行う。
新規実装より、既存コード（app.py / admin.py / db.py）の拡張を優先する。

## 必読・必守事項
1. 作業ディレクトリ: `/opt/gemini-ui`
2. Python 仮想環境: `/opt/faq-bot/venv/bin/`（専用 venv は無い。faq-bot と共有）
3. API キー: `/opt/gemini-ui/.env` の `GOOGLE_API_KEY`（faq-bot の `.env` と同一キー）
4. 機密情報（APIキー・パスワード）をコミット・ログ出力・画面表示に含めない
5. 変更後は `systemctl restart gemini-ui.service gemini-ui-admin.service` で反映
6. 既存の systemd / Nginx 設定パターンに合わせる（他アプリと同様）
7. コミットはユーザーが明示的に依頼した場合のみ
8. 詳細は `/opt/gemini-ui/HANDOFF.md` を参照すること

## 作業前の確認コマンド
systemctl status gemini-ui.service gemini-ui-admin.service
ss -tlnp | grep -E "8507|8508"
cd /opt/gemini-ui && /opt/faq-bot/venv/bin/python3 -m py_compile app.py admin.py db.py

## 今回の改修内容（ここに具体的な要望を記載）
- （例）管理パネルを Nginx `/nai-admin/` で公開したい
- （例）部署別レポートの CSV エクスポートを追加したい
```

---

## 13. 今回の改修依頼

（New Agent 起動後、ここに具体的な改修内容を追記してください）
## 主には'9. 既知の課題・未実装・改善候補'にて記載されていた項目と追加でいくつか機能実装をしたい

| 項目 | 現在の状態 |
|------|------|
| 管理パネルの Nginx 経由アクセス | 未実装（:8508 のみ）→'/nai-ctrl'でアクセスできるようにする |
| 専用 Python venv | 未作成（faq-bot venv 共有） →　専用Python venを構築する|
| plain_password 平文保存 | 意図的（管理者参照要件）だがセキュリティリスクあり →　管理者は参照できるようにする|
|
| レート制限時の自動リトライ | 未実装 →　実装してください。|
| 添付ファイルのサイズ・種類の管理者設定 | 未実装（ハードコード 50MB）→　管理者画面でユーザー毎に制限できるようにしたい |
| ユーザーのセッションタイムアウト | 未実装 →　10分間の未入力でセッションタイムアウトさせたい|
| HTTPS / 認証の二要素化 | 未実装 |