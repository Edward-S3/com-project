# Gemini Live API 事前調査（C-09）

| 項目 | 内容 |
|---|---|
| 調査日 | 2026-07-13（追記 2026-07-14） |
| 調査者 | 開発担当（Cursor） |
| 目的 | P3音声モード実装前の公式仕様確認（C-09） |
| 実装着手 | **承認済・実装中**（2026-07-14） |

---

## 1. 最新モデル名（音声対話・ネイティブオーディオ）

| モデル | 区分 | 推奨用途 | 備考 |
|---|---|---|---|
| `gemini-3.1-flash-live-preview` | Preview | **推奨**（低遅延リアルタイム対話） | ネイティブオーディオ出力、thinkingLevel対応、128kコンテキスト |
| `gemini-3.5-live-translate-preview` | Preview | リアルタイム翻訳専用 | 本アプリの1on1面談には非該当 |
| `gemini-2.5-flash-native-audio-preview-12-2025` | Preview（非推奨） | — | 公式ドキュメントで `gemini-3.1-flash-live-preview` への移行を推奨 |
| `gemini-live-2.5-flash-preview` | Preview（非推奨） | — | 2025-12-09 シャットダウン予定（公式） |
| `gemini-2.0-flash-live-001` | Preview（非推奨） | — | 2025-12-09 シャットダウン予定（公式） |

**本プロジェクトでの推奨設定（.env）**

```
MODEL_GEMINI_LIVE=gemini-3.1-flash-live-preview
```

**フォールバック（3.1 障害・未提供時）**

```
MODEL_GEMINI_LIVE=gemini-2.5-flash-native-audio-preview-12-2025
```

- 公式は `gemini-3.1-flash-live-preview` への移行を推奨しているが、Preview モデルのため一時的な利用不可があり得る。
- フォールバック時は下記「2.5 → 3.1 移行ガイド」の差異（特に `thinkingBudget` / `send_client_content` / proactive audio）に注意する。
- いずれのモデル名も `.env` の `MODEL_GEMINI_LIVE` 経由で読み込み、コードに直書きしない（C-09）。

**出典**
- https://ai.google.dev/gemini-api/docs/live-api （Overview / Supported models）
- https://ai.google.dev/gemini-api/docs/live-api/capabilities （モデル別機能差分表）
- https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview （Migrating from Gemini 2.5 Flash Live）
- https://github.com/google-gemini/gemini-skills/blob/main/skills/gemini-live-api-dev/SKILL.md （推奨モデル記載）

**仕様書との差分**
- SPECIFICATION.md 3.3 はモデル名を `.env` 読込としているため、コード直書きは不要。
- 初版想定の `gemini-2.0-flash-live-001` 等は非推奨。`gemini-3.1-flash-live-preview` を採用予定。

### 2.5 → 3.1 移行ガイド要点（実装時必読）

出典: https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview （Migrating from Gemini 2.5 Flash Live）

| 項目 | 2.5 (`gemini-2.5-flash-native-audio-preview-12-2025`) | 3.1 (`gemini-3.1-flash-live-preview`) |
|---|---|---|
| Thinking | `thinkingBudget` | `thinkingLevel`（`minimal` / `low` / `medium` / `high`）。低遅延向けデフォルトは `minimal` |
| Server events | 1イベント1パート想定が多い | 1つの `BidiGenerateContentServerContent` に音声＋書き起こし等の **複数 parts が同時** に入り得る。全 parts を処理すること |
| Client content | 会話中も `send_client_content` 可 | `send_client_content` は初期履歴シードのみ（`history_config.initial_history_in_client_content` 必須）。会話中のテキストは `send_realtime_input(text=...)` |
| Turn coverage | `TURN_INCLUDES_ONLY_ACTIVITY` | デフォルト `TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO`（動画を送る場合はコスト注意。本アプリは音声のみ） |
| Async function calling | 対応 | **未対応**（同期のみ） |
| Proactive audio / Affective dialogue | 対応 | **未対応**（設定を入れない） |

本アプリ（新規構築）は 3.1 前提で実装し、上記差異を前提コードに織り込む。

---

## 2. 接続プロトコル

### エンドポイント

```
wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent
```

認証はクエリパラメータ `?key=API_KEY` またはエフェメラルトークン（後述）。

### セッション確立手順

1. WebSocket接続を確立
2. **最初のクライアントメッセージ**に `setup`（`BidiGenerateContentSetup`）を送信
   - `model`, `generationConfig`, `systemInstruction`, `tools` 等を含む
3. 以降、以下いずれか1フィールドのみを持つJSONを送受信:
   - クライアント: `setup` | `clientContent` | `realtimeInput` | `toolResponse`
   - サーバー: `serverContent` | `toolCall` | `usageMetadata` 等

### setup/config スキーマ（概要）

```json
{
  "setup": {
    "model": "models/gemini-3.1-flash-live-preview",
    "generationConfig": {
      "responseModalities": ["AUDIO"],
      "speechConfig": { "voiceConfig": { "prebuiltVoiceConfig": { "voiceName": "..." } } }
    },
    "systemInstruction": { "parts": [{ "text": "..." }] },
    "inputAudioTranscription": {},
    "outputAudioTranscription": {}
  }
}
```

**`gemini-3.1-flash-live-preview` 固有注意**
- `send_client_content` は初期コンテキストのシードのみ（`initial_history_in_client_content: true` 必須）
- 会話中のテキスト更新は `send_realtime_input` の `text` フィールドを使用

**出典**
- https://ai.google.dev/api/live （WebSocket API reference）
- https://ai.google.dev/gemini-api/docs/live-api/get-started-websocket

---

## 3. 認証方式と中継構成

### 推奨構成（本アプリ）

**サーバー中継（Server-to-Server）** を採用する。

```
ブラウザ ←WSS→ FastAPI (live_bridge.py) ←WSS→ Google Live API
                APIキーはサーバー側のみ保持
```

| 方式 | 適用 | 採用 |
|---|---|---|
| サーバー中継 + APIキー | ブラウザにキーを渡さない。本番向け | **採用（主）** |
| クライアント直結 + エフェメラルトークン | 低遅延だがトークン発行APIが必要 | 将来オプション |

### エフェメラルトークン

- Live API専用（v1alpha）
- デフォルト: 新規セッション開始まで **1分**、接続後メッセージ送信 **30分**
- `client.auth_tokens.create()` で発行、WebSocket接続時にAPIキー代替として使用
- 本アプリ初版はサーバー中継で十分。クライアント直結が必要になった場合に検討

**出典**
- https://ai.google.dev/gemini-api/docs/live-api （Client-to-server vs Server-to-server）
- https://ai.google.dev/gemini-api/docs/ephemeral-tokens

---

## 4. 音声フォーマット要件

| 方向 | フォーマット | サンプルレート |
|---|---|---|
| 入力（クライアント→API） | raw 16-bit PCM, little-endian | 16kHz（任意レートはAPI側でリサンプル可。MIMEで明示） |
| 出力（API→クライアント） | raw 16-bit PCM, little-endian | **24kHz固定** |

MIMEタイプ例: `audio/pcm;rate=16000`

ブラウザ側は Web Audio API / AudioWorklet で PCM 変換が必要（P3実装タスク）。

**出典**
- https://ai.google.dev/gemini-api/docs/live-api
- https://ai.google.dev/gemini-api/docs/live-api/capabilities （Audio formats）

---

## 5. バージイン（割り込み発話）

### 公式サポート: **あり**

- デフォルト: **自動VAD**（Voice Activity Detection）
- 利用者が発話開始すると進行中の生成がキャンセルされる
- サーバーは `BidiGenerateContentServerContent` で `interrupted: true` を送信
- 割り込み時、未送信部分はセッション履歴に含まれない（実際に生成・送信された範囲のみ）

### 手動VAD（オプション）

- `realtimeInputConfig.automaticActivityDetection.disabled = true`
- `activityStart` / `activityEnd` で手動制御

### P3実装要件への対応

| 受入要件 | 対応方針 |
|---|---|
| AI再生停止 | `interrupted` 受信時に再生キューをクリア |
| 割り込み後の応答が新内容 | VADによる生成キャンセル＋新入力で再生成（公式仕様） |
| ログは再生済み範囲のみ | `outputAudioTranscription` + `interrupted` フラグで打ち切り位置を記録 |

**出典**
- https://ai.google.dev/gemini-api/docs/live-api/capabilities （Interruptions / Voice Activity Detection）

---

## 6. セッション時間・同時接続・再接続

| 制限 | 値 | 対策 |
|---|---|---|
| 音声のみセッション最大時間 | **15分** | `contextWindowCompression` で延長可能 |
| 音声+動画セッション | **2分** | 本アプリは音声のみ |
| WebSocket接続寿命 | **約10分** | `sessionResumption` で再接続・セッション継続 |
| 再開トークン有効期限 | 最終セッション終了後 **2時間** | 15分面談×1回は単一接続で完走可能。長時間は再接続設計 |
| コンテキストウィンドウ | ネイティブオーディオ: **128k tokens** | 圧縮設定推奨 |

**15分面談1回の完走**
- 音声のみ・15分以内であれば単一セッションで完走可能
- 接続10分制限があるため、長時間面談では `sessionResumption` による再接続が必要
- 本アプリ `SESSION_MAX_MINUTES=30` との整合: 実装時は15分制限＋再接続またはセッション分割を設計

**出典**
- https://ai.google.dev/gemini-api/docs/live-api/capabilities （Limitations / Session duration）
- https://ai.google.dev/gemini-api/docs/live-api/session-management

---

## 7. 料金体系

- **秒課金ではなくトークン課金**
- Live APIはWebSocketセッションを維持し、**ターンごとに累積コンテキスト全体を再課金**（コンパウンドモデル）
- ネイティブオーディオはテキスト変換せず生音声トークンとして保持（約25 tokens/秒）
- 文字起こし有効時: 音声トークン **＋** テキスト出力トークン（別途課金）
- 長時間セッションは `contextWindowCompression` でコスト抑制推奨

詳細単価は公式Pricingページを参照（モデル・モダリティ別に変動）。

**出典**
- https://ai.google.dev/gemini-api/docs/live-api/best-practices （Pricing and billing）
- https://ai.google.dev/gemini-api/docs/pricing
- https://discuss.ai.google.dev/t/pricing-of-speech-to-speech-live-model/140340 （フォーラム補足）

---

## 8. P3実装方針（調査承認後）✓ 実装済 2026-07-14

1. `app/live_bridge.py`: FastAPI WebSocketプロキシ（APIキー非露出）
2. `MODEL_GEMINI_LIVE` を `.env` から読込（デフォルト: `gemini-3.1-flash-live-preview`）
3. 入力16kHz PCM / 出力24kHz PCM 変換
4. `inputAudioTranscription` / `outputAudioTranscription` でトランスクリプト取得→既存 `turns` テーブルへ保存
5. 審判は既存 `judge.py` をそのまま使用（音声専用審判は作らない）
6. バージイン: `interrupted` イベント処理＋再生停止＋部分トランスクリプト記録
7. HTTPS: mkcert（P3受入条件）
8. フォールバックモデルと 2.5→3.1 移行差分はセクション1を参照

---

## 9. 確認チェックリスト

- [x] 最新モデル名（preview区分含む）
- [x] WebSocketエンドポイント・setup手順・メッセージスキーマ
- [x] 認証（サーバー中継 + エフェメラルトークン可否）
- [x] 音声フォーマット（入16kHz / 出24kHz PCM）
- [x] バージイン公式サポートと `interrupted` イベント
- [x] セッション15分制限・接続10分・再接続戦略
- [x] トークン課金（秒課金ではない）

**次のアクション**: 調査承認済。C-09 実装（`scripts/live_smoke.py` → `live_bridge.py` → フロント）に着手。
