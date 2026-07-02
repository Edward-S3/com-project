# pptx-gen2 引継ぎ資料

**作成日**: 2026-07-01  
**アプリ名**: マルチLLMオーケストレーション型 AI資料生成  
**ルートパス**: `/opt/pptx-gen2`  
**仕様書**: `/opt/pptx-gen2/docs/cursor_prompt_ai_slide_generator.md`

---

## 1. プロジェクト概要

複数ソース（文書・音声・動画・YouTube・Web）から、工程ごとに最適な LLM を自動選択（または手動指定）して PowerPoint (.pptx) を生成する Streamlit アプリ。

**設計思想**: 単一 LLM に全工程を任せず、オーケストレーション方式で工程別にモデルを割り当てる。

**現在の API 利用状況**:
- **Gemini**: 有効（`/opt/gemini-ui/.env` と共通のキーを `GEMINI_API_KEY` に設定済み）
- **OpenAI / Anthropic / xAI**: `.env` 内はコメントアウト（将来併用予定）

---

## 2. デプロイ・運用

### 2.1 ネットワーク

| 項目 | 値 |
|------|-----|
| ポート | **8517** |
| バインド | **172.16.16.10** |
| 直接 URL | `http://172.16.16.10:8517/` |
| nginx パス | `http://172.16.16.10/pptx2/` |
| ランチャー | `http://172.16.16.10/` → 「PPTX マルチLLM生成」 |

**兄弟アプリのポート**: pptx-gen=8514, img2pptx=8515, pptx-gen-an=8516, **pptx-gen2=8517**

### 2.2 systemd

```bash
systemctl status pptx-gen2
systemctl restart pptx-gen2
journalctl -u pptx-gen2 -f
journalctl -u pptx-gen2 -n 50 --no-pager   # -f 不要ならこちら
```

- ユニット定義（ソース）: `/opt/deploy/systemd/pptx-gen2.service`
- インストール先: `/etc/systemd/system/pptx-gen2.service`

### 2.3 手動起動

```bash
cd /opt/pptx-gen2
./scripts/start.sh
# または
source venv/bin/activate && streamlit run app.py
```

### 2.4 セットアップ（初回）

```bash
cd /opt/pptx-gen2
bash setup.sh
# .env に GEMINI_API_KEY を記入
```

### 2.5 インフラ関連ファイル

| ファイル | 用途 |
|----------|------|
| `/opt/deploy/nginx/python_apps.conf` | `/pptx2/` プロキシ |
| `/opt/deploy/launcher/index.html` | ランチャーカード |
| `/opt/deploy/sysctl/99-inotify.conf` | inotify 上限引き上げ（journalctl 警告対策） |

---

## 3. ディレクトリ構成

```
/opt/pptx-gen2/
├── app.py                    # Streamlit UI
├── scripts/start.sh          # 起動スクリプト
├── setup.sh
├── .env / .env.example
├── config/
│   ├── task_routing.json     # 工程別 LLM 優先順位
│   ├── pricing_rates.json    # 料金（現状 0.0 プレースホルダー）
│   ├── timeouts.json         # タイムアウト
│   ├── budget_scenarios.json # コストシナリオ
│   └── design_system.py      # 配色・DesignBridge
├── core/
│   ├── llm_clients.py        # Gemini/OpenAI/Anthropic/xAI 統一ラッパー
│   ├── orchestrator.py       # ルーティング・フォールバック
│   ├── pipeline.py           # バックグラウンド生成パイプライン
│   ├── file_ingest.py        # ファイル取り込み
│   ├── web_source_ingest.py  # YouTube / Web URL
│   ├── audio_video.py        # Gemini 音声/動画
│   ├── content_synthesizer.py
│   ├── slide_planner.py
│   ├── payload_generator.py
│   ├── narrative_generator.py
│   ├── pptx_renderer.py      # 自由生成 + テンプレート fallback
│   ├── qa_checker.py
│   └── cost_estimator.py
├── templates/layouts.py      # 9種テンプレート
├── outputs/                  # 生成 pptx
├── temp_uploads/
└── logs/
```

---

## 4. 生成パイプライン（処理フロー）

```
1. ファイル / YouTube / Web 取り込み
2. content_synthesis      … 内容統合
3. slide_structure_planning … 構成設計（おすすめ構成はローカル無料推定可）
4. structured_json_payload … スライド JSON
5. japanese_narrative     … スピーカーノート
6. slide_layout_code_generation … スライド描画（自由生成→失敗時テンプレート）
7. design_visual_qa       … QA（LibreOffice + Poppler + Vision）
```

- **バックグラウンドスレッド**で実行（`core/pipeline.py`）
- UI は 1.5 秒ごとにポーリング更新
- **キャンセル**対応（`threading.Event`）

---

## 5. UI 仕様（実装済み）

### 5.1 生成開始の条件

- ファイル / YouTube URL / Web URL の**いずれか1つ以上**があれば「生成開始」有効
- 「コストを見積もる」は任意（未実行でも生成開始時に自動見積もり）

### 5.2 ステータスパネル（画面上部）

- 状態・経過時間・進捗バー・使用モデル・実コスト
- **工程別LLM（確定済み）**一覧（自動/手動選定結果）

### 5.3 サイドバー LLM 設定

- 待機中: 工程ごとにプルダウン（自動 / Gemini / Claude / GPT-4o / Grok）
- **処理中**: 確定済み工程は `✅ Gemini (gemini-2.5-flash) [自動選定]` 形式で表示（変更不可）
- 未実行工程は `⏳ 自動（未実行）`

---

## 6. 環境変数（`.env`）

```env
GEMINI_API_KEY=...          # 必須（GOOGLE_API_KEY もフォールバック参照可）
# OPENAI_API_KEY=           # 将来用（コメントアウト）
# ANTHROPIC_API_KEY=
# XAI_API_KEY=

LAN_PORT=8517
LAN_BIND=172.16.16.10
HTTP_PROXY=                 # 任意
HTTPS_PROXY=                # 任意
```

**料金テーブル**: `config/pricing_rates.json`（要ユーザー記入。0.0 のままだと警告表示）

---

## 7. 実施済み修正（2026-07-01 セッション）

### 7.1 基盤構築
- 仕様書 `cursor_prompt_ai_slide_generator.md` に基づき全モジュール構築
- systemd / nginx / ランチャー登録

### 7.2 UI 改善
- 「生成開始」をソース1つ以上で有効化（見積もり不要）
- 処理中ステータスパネル追加
- 自動選定 LLM のリアルタイム表示（サイドバー + ステータス）

### 7.3 slide_layout_code_generation 不具合対応

| 問題 | 原因 | 修正 |
|------|------|------|
| 100% fallback | 正規表現 `\brequests\b` がコメント内も誤検知 | AST ベースバリデーションに変更 |
| 100% fallback | サンドボックスで `json.dumps` の `true`/`false` が Python エラー | `json.loads()` で data 渡し |
| 100% fallback | `PYTHONPATH` 未設定で `config` import 失敗 | subprocess に PYTHONPATH 設定 |
| 100% fallback | 検証成功後も `apply_layout` のみ（生成コード未適用） | `_execute_render_code()` で本番適用 |
| 100% fallback | `design.body_bg()` が HEX 文字列、生成コードは RGBColor 期待 | `DesignBridge` 追加 |
| タイムアウト无效 | API 完了後の事後チェックのみ | `ThreadPoolExecutor` + `HttpOptions(timeout=ms)` |
| 処理が遅い | 1スライド最大3回 × 504 再試行 | 再試行2回、504 時即 fallback |
| 原因不明 | サンドボックス失敗ログなし | `logs/sandbox_errors_{timestamp}.log` 出力 |

### 7.4 インフラ
- inotify 上限引き上げ（`/etc/sysctl.d/99-inotify.conf`）

---

## 8. 既知の課題・未検証事項

### 8.1 自由生成モード（重要）

**現状**: `logs/render.log` では **mode=free が 0件、mode=fallback が 27件**（2026-07-01 時点）。

修正後のローカル単体テストでは sandbox OK を確認済みだが、**本番同等ソースでの再実行による free 成功率は未確認**。

確認コマンド:
```bash
grep "mode=" /opt/pptx-gen2/logs/render.log | tail -20
grep -c 'mode=free' /opt/pptx-gen2/logs/render.log
ls -lt /opt/pptx-gen2/logs/sandbox_errors_*.log | head
```

### 8.2 処理時間

18スライド × レイアウトコード生成（Gemini API × 最大2回/スライド）で **15〜20分** かかる場合あり。

**短時間で完成させたい場合**:
- コスト見積もり → **「レイアウトをテンプレート固定」** シナリオを選択
- または **「最安構成」**

### 8.3 未実装・弱い部分

- nginx 本番反映は済みだが、**ssl 側設定**（`python_apps-ssl.conf`）に `/pptx2/` が無い可能性 → HTTPS 経由要確認
- `pricing_rates.json` はプレースホルダー（ユーザー記入待ち）
- OpenAI / Anthropic / xAI キー未設定
- QA 工程（LibreOffice + Poppler）はツール存在確認済みだが、長時間生成時の QA 完走は未十分検証
- git リポジトリ未初期化の可能性（要確認）

### 8.4 sandbox 典型エラー（ログより）

- `NameError: name 'true' is not defined` → **修正済み**（json.loads）
- `AttributeError: 'Slide' object has no attribute 'presentation'` → プロンプトで `SLIDE_WIDTH`/`SLIDE_HEIGHT` 使用を指示済み
- `504 DEADLINE_EXCEEDED` → Gemini 側タイムアウト。30秒設定。即 fallback に変更済み

---

## 9. ログの見方

| ファイル | 内容 |
|----------|------|
| `logs/YYYYMMDD.log` | 工程別 OK/FAIL、使用モデル |
| `logs/render.log` | スライドごと `mode=free|fallback|template` |
| `logs/sandbox_errors_*.log` | サンドボックス失敗詳細（stage, traceback, コード全文） |
| `journalctl -u pptx-gen2` | サービス標準出力 |

---

## 10. 設定ファイル早見

### task_routing.json（デフォルト優先順位）

| 工程 | 優先 |
|------|------|
| content_synthesis | gemini → claude |
| slide_structure_planning | claude → gpt4o → gemini |
| structured_json_payload | gpt4o → claude → gemini |
| japanese_narrative | claude → gemini → gpt4o |
| slide_layout_code_generation | claude → gpt4o → gemini |
| design_visual_qa | claude → gpt4o |
| audio_video_understanding | gemini のみ |

### timeouts.json

| キー | 秒 |
|------|-----|
| layout_generation_sec | 30 |
| layout_execution_sec | 5 |
| text_llm_sec | 120 |
| pipeline_total_sec | 1800 |

---

## 11. 次セッションで優先すべきタスク

1. **修正後の同一ソース再実行** → `mode=free` 率の確認
2. sandbox_errors ログが出る場合、生成コード品質の改善（プロンプト or post-process）
3. `pricing_rates.json` への公式料金記入
4. OpenAI / Anthropic / xAI キー追加後のマルチ LLM 動作確認
5. HTTPS（`/pptx2/`）nginx ssl 設定の追加要否確認
6. 処理時間短縮案:
   - テンプレート固定をデフォルトオプションにする
   - 自由生成を「タイトルスライドのみ」等に限定
   - layout_generation_sec の調整（30→45秒等、504 減 vs 遅延のトレードオフ）

---

## 12. 会話履歴サマリー（ユーザー要求の経緯）

1. `/opt/pptx-gen2/docs/cursor_prompt_ai_slide_generator.md` に基づきアプリ構築
2. 配置: `/opt/pptx-gen2`、LAN: 8517 / 172.16.16.10
3. Gemini API キー設定（他 LLM は .env にコメントアウト枠のみ）
4. systemd + ランチャー + nginx 登録
5. 「生成開始」が押せない問題 → ソース1つ以上 + 見積もり不要に変更
6. 処理中か判断できない → ステータスパネル追加
7. slide_layout_code_generation 100% fallback → ログ強化・バリデーション・タイムアウト・DesignBridge 等を修正
8. 処理時間が長い → 504 即 fallback、再試行削減（根本はスライド数 × API 呼び出し）
9. 自動 LLM 選定結果の UI 反映

---

## 13. クイックリファレンス

```bash
# サービス再起動（コード変更後）
systemctl restart pptx-gen2

# ログ確認
tail -f /opt/pptx-gen2/logs/render.log
tail -f /opt/pptx-gen2/logs/$(date +%Y%m%d).log

# 依存関係
cd /opt/pptx-gen2 && source venv/bin/activate && pip install -r requirements.txt
```

**テスト用同一ソース**: 2つの Word ドキュメント（eラーニングハンドアウト + 生成AI活用プロンプト）— ユーザーが実際に使用していた入力。

---

*この資料は新しいチャットセッション開始時に最初に読み込むことを推奨します。*
