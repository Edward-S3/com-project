# P3 クローズ記録

| 項目 | 内容 |
|---|---|
| 日付 | 2026-07-16 |
| 対象 | P3 音声モード完成(音声経路+審判パート)+ D-06〜D-08 決着 |
| 結果 | **クローズ**(D-06 のみ条件付き、再オープン条件あり) |

---

## 1. 検収結果サマリ

| パート | 結果 | 日付 | 証跡 |
|---|---|---|---|
| 音声経路 | 合格 | 2026-07-14 | `ACCEPTANCE_LOG_20260714.md`(バージイン①〜④、session 17) |
| 審判 | 合格(差し戻し解除) | 2026-07-15 | `ACCEPTANCE_LOG_20260715.md`(D-07/D-08 受入OK) |

---

## 2. 欠陥決着状況

| ID | 内容 | 決着 | コミット |
|---|---|---|---|
| D-07 | 審判スコア尺度矛盾(judge.py) | 修正完了・受入OK | 021074e |
| D-08 | interrupted 発話が無印で審判入力に混入 | 修正完了・受入OK | 828724c |
| D-06 | 音声切替時 Gemini エラー1007 | **再現不能・条件付きクローズ** | bf41e6a(調査記録) |

### D-07 修正内容
尺度を `rating_scale` 由来で動的化(3〜7)。`normalize_judge_scores` によりクランプ / round / 数値化不能スコアの除外 / `score_corrections` 記録 / `avg_score` 再計算を実装。

### D-08 修正内容
フィールド方式を採用: `_build_judge_transcript` + `format_transcript_for_judge` で interrupted 発話に「(途中打ち切り)」ラベルを付与し、審判プロンプトに高評価引用禁止ルールを追加。

### 実効確認
両修正は session 17 実データ再採点で確認済み: `score_corrections=null`、(途中打ち切り)ラベル付与、interrupted 発話の引用排除。

---

## 3. D-06 条件付きクローズの詳細

| 項目 | 内容 |
|---|---|
| 再現試行 | スクリプト8パターン + 実UI 2回(session 18・19)で再現せず |
| 推定原因 | preview 版モデルのサーバー側解消 |
| 再オープン条件 | 同種の 1007 エラー再発時 |
| 再発時の初手 | `live_bridge` への送信ペイロードの一時ログ追加 |
| 再現スクリプト | `docs/acceptance/P3/repro_d06/` に退避済み(repro_d06.py / repro_d06_audio.py / repro_d06_exact.py。元は /tmp に残置していたもの) |

---

## 4. クローズ時点のスナップショット(2026-07-16 取得)

| 項目 | 内容 |
|---|---|
| git | HEAD `bf41e6a` = `origin/main`(interview 配下クリーン、push 済) |
| pytest | **46 passed**(`.venv/bin/python -m pytest -q`、warning は Starlette 非推奨警告1件のみ・実害なし) |
| サービス | `interview.service` systemd 稼働中・enable 済 |

---

## 5. P4 への申し送り

| 項目 | 内容 |
|---|---|
| 同意文言改訂 | 現行同意文は実人事情報の入力を禁止しており grounded interview template 構想と矛盾。**同意書改訂で対応(仮名化は不採用)**と決定済(7月上旬)。P4 着手前に決着必須 |
| 採点中ローディング表示 | 面談終了後 数十秒無反応でスタックと誤認(7/15 実機確認)。P4 候補・優先高 |
| score_corrections の DB 永続化 | P4 候補 |
| 改行コード統一 | リポジトリ全体が CRLF(初回コミット時点から)。実害なしと判断済み。統一+`.gitattributes` 導入は別タスク |

pytest グリーン維持を前提に P3 をクローズし、P4 へ進む。
