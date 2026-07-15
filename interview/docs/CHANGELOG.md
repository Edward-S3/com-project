# CHANGELOG

## P3 — 音声モード（C-09）— 2026-07-14

### Step 0（前提整備）

- `docs/notes/live_api_survey.md` — フォールバック `gemini-2.5-flash-native-audio-preview-12-2025` と 2.5→3.1 移行ガイド要点を追記（調査承認済）
- `requirements.txt` — `google-genai==2.11.0` に固定
- `docs/acceptance/P2/CLOSE.md` — P2クローズ記録（S2 session8、acknowledgmentリネームのみ、仕様書「器具高ぁぁ」引用可）

### Step 1〜5（実装）

- `scripts/live_smoke.py` — Live 接続スモーク（音声応答・sessionResumption・usage 確認。PASS）
- `app/live_bridge.py` — FastAPI WebSocket プロキシ（APIキー非露出、共通ペルソナプロンプト、16k/24k PCM、入出力書き起こし→turns、interrupted 記録、sessionResumption 再接続、トークン使用量ログ）
- 審判は既存 `judge.py` を共用（音声専用審判なし）。モデル名は `MODEL_GEMINI_LIVE` のみ
- mkcert HTTPS（`certs/`、`.gitignore`）。`README.md` に手順追記
- `app/static/audio.js` — getUserMedia / AudioWorklet / 再生キュー / バージイン即停止
- 受入証跡: `docs/acceptance/P3/`（1A/中小企業、トランスクリプト・審判JSON・バージイン）
- pytest 36件グリーン（`tests/test_live_bridge.py` 追加）

### 設定

- `.env.example` — `VOICE_*` / `SSL_*` / Live フォールバック注記

## P2.1 + P3調査 — 2026-07-13

### P2.1 修正（P2検収指摘）

- **P2.1-1**: `app/goal_readiness.py` 追加。1Bモードで承認宣言前にき・ぐ・そ（改善提案の期限含む）をコード検証。`partner.py` プロンプト強化。S2再実行（session_id=8）で「期限なし→差し戻し→期限補完→承認」を確認、`docs/acceptance/P2/S2.md` 更新
- **P2.1-2**: `judge.py` — improvements.principle にマニュアル原則のみ記載するようプロンプト修正
- **P2.1-3**: `checker.py` — 1A/1Bのみ検知、評価基準括弧内の誤検知除外、「参考警告」ラベル化。`app.js` 表示同期
- **P2.1-4**: `judge.py` — 1B/2B/1Aでは `feedback_flow_observed: null` を返すよう指示（2A専用）
- **P2.1-5**: コード/プロンプト内の「器具高そぅ」表記を「き・ぐ・た・か・そ」に修正（`rubrics.json`・仕様書は原文のため変更なし）

### P3 着手準備

- `docs/notes/live_api_survey.md` — C-09事前調査完了（公式ドキュメント出典付き）
- `.env.example` — `MODEL_GEMINI_LIVE=gemini-3.1-flash-live-preview` 追記
- **C-09実装は調査承認待ち**（Live API WebSocketプロキシ未着手）

### テスト

- pytest 30件グリーン（`test_goal_readiness.py` 追加）

## P2 — テキストモード完成（2026-07-13）

### 追加

- `app/models.py`, `app/rubrics_util.py`, `app/persona.py`, `app/partner.py`, `app/judge.py`, `app/session_service.py`
- FastAPI REST API（ユーザー/セッション/メッセージ/講評/履歴）
- フロントエンド S-01〜S-05（`app/static/index.html`, `app.js`, `style.css`, レーダーチャート）
- `app/static/rubrics_steps.json`（面談5ステップガイド）
- `scripts/run_acceptance.py`（S1〜S4 受入シナリオ・実API・`docs/acceptance/P2/` エクスポート）
- `docs/acceptance/P2/S1.md`〜`S4.md`（受入証跡の恒久化）
- `tests/test_judge.py`（審判JSONスキーマ検証）
- テスト追加: `test_persona.py`, `test_rubrics_util.py`, `test_api.py`

### 変更

- `app/db.py`: セッション取得/終了/講評保存/履歴一覧、`overall_evaluation` 列
- `app/main.py`: API ルートと静的 UI 配信
- `data/rubrics.json`: Q-08 マニュアル原文で全面更新（P1検収後）
- `app/judge.py`: `feedback_flow_observed.negligence` → `acknowledgment`（ねぎらいの正訳）
- `app/static/app.js`: S-04 フィードバックフロー表示を日本語ラベル化
- S2 受入: 期限補完発話追加後に再実行し、AI上司承認を確認（session_id=6）

### 受入基準

- [x] pytest 27件グリーン（LLMモック）
- [x] 4モード×3難易度を UI から選択可能
- [x] 禁止表現 regex 警告（S2 で4件検出確認）
- [x] hidden_facts が API 応答に含まれないことを確認
- [x] S1〜S4 実API受入スクリプト実行・トランスクリプト保存
- [x] 受入証跡を `docs/acceptance/P2/` に恒久保存（人間検収待ち）

## P1 — 基盤（2026-07-13）

### 追加

- プロジェクト骨格: `requirements.txt`, `pyproject.toml`, `.gitignore`, `.env.example`, `README.md`
- 設定: `app/config.py`（`.env` 読込、役割別プロバイダ解決）
- LLM プロバイダ: `app/providers/`（gemini / openai / anthropic / xai / mock）
- DB: `app/db.py`（users / sessions / turns / reports テーブル）
- チェッカー: `app/checker.py`（`rubrics.json` ベースの禁止表現 regex 検出）
- マスタデータ: `data/grades.json`, `data/rubrics.json`, `data/departments.json`
- モードプロンプト骨格: `app/mode_prompts.py`（1A / 1B）
- FastAPI 雛形: `app/main.py`（`/health`、静的マウント準備）
- CLI: `scripts/cli_chat.py`（1ターン対話、モック/実API両対応）
- テスト: `tests/`（19件、LLM はモック）

### ドキュメント

- `docs/QUESTIONS.md` — Q-01〜Q-07 回答確定
- `docs/PROPOSALS.md` — P-01〜P-03 承認確定
- `docs/CURSOR_INSTRUCTIONS.md` — Python 3.10+、`scripts/` 追記

### 受入基準

- [x] pytest 全件グリーン（19 passed）
- [x] CLI で 1ターン対話＋禁止表現チェッカー動作確認
- [x] `.env` / `.venv` / `sessions.db` が git 追跡対象外
