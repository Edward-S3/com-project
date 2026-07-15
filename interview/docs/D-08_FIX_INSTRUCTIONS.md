# D-08修正指示: interrupted発話の審判入力へのマーク付与(フィールド方式)

## 対象
app/session_service.py、app/judge.py、テスト追加
(tests/test_judge.py への新規関数追加、および必要なら
tests/test_session_service.py 等への新規関数追加のみ。
既存テストの変更・削除は不可)。
それ以外のファイル変更禁止。必要と判断した場合は着手前に報告。

## 背景(証跡確認済み)
- warnings_json(turns.warnings_json TEXT、JSON文字列)には
  interrupted 発話に「[interrupted] 打ち切り位置までの再生分のみ
  (以降は評価対象外)」が記録済み
- session_service.py 288行の transcript 組み立てが speaker/text のみ
  抽出し、この情報を破棄している
- 結果、審判は発話が途中打ち切りされた事実を知らずに採点する

## 修正1: transcript へのフィールド付与(session_service.py)
- _judge_and_save の transcript 組み立て時、各 turn の warnings_json
  をパースし、「[interrupted]」を含む警告がある発話には
  "interrupted": true フィールドを付与する
  例: {"speaker": ..., "text": ..., "interrupted": true}
- interrupted でない発話にはフィールドを付けない(無印)
- warnings_json が null/空/不正JSONの場合は無印扱いとし、
  パース例外で採点処理全体を落とさないこと

## 修正2: 審判側の整形とルール明記(judge.py)
- transcript_text 整形(現95〜97行付近)で interrupted=true の
  発話を「[speaker] (途中打ち切り) text」形式にラベル整形する
- システムプロンプトの採点ルール部に以下の趣旨を追加:
  「(途中打ち切り)ラベル付き発話は途中で遮られた発話であり、
  内容の一部は相手に聞こえていない。この発話の内容を高評価の
  根拠として引用してはならない。遮り(バージイン)の発生自体は
  面談進行の文脈として考慮してよい」

## 禁止事項
- DBスキーマ・書き込み経路の変更禁止
- live_bridge.py の変更禁止(記録側は正常動作のため)
- D-07実装済みの normalize_judge_scores / スキーマ組み立て関数への
  変更禁止

## 完了報告に含める証跡(自己申告のみは不可)
1. git diff 全文
2. pytest 実行結果全文(既存テスト全パス+新規テスト)
3. フィールド付与の実証: warnings_json に [interrupted] を含む turn
   と含まない turn の混在データでの transcript 出力提示
4. 修正後 transcript_text 整形の実出力
   ((途中打ち切り)ラベルが付く例と付かない例)
5. 修正後プロンプトの採点ルール該当箇所の実出力