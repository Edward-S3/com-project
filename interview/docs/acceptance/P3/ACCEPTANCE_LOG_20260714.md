# P3検収記録 2026-07-14（検収者: 鈴木）

## 1. systemdサービス化（完了）
- /etc/systemd/system/interview.service 作成・enable済（OS再起動後も自動起動）
- ExecStart: .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8443 + TLS証明書指定（絶対パス）
- 検証: active(running) / journalに startup complete / サーバー内 curl /health OK
- クライアント疎通: curl.exe（Schannel）で 200 OK。ただし mkcert 証明書に失効情報が
  無いため `--ssl-no-revoke` が必要（CRYPT_E_NO_REVOCATION_CHECK）。ブラウザ利用は影響なし
- 副次確認: クライアントから証明書検証込みで接続成功 = mkcert CA信頼チェーンと
  SAN の 172.16.16.10 包含の傍証（正式確認は報告指示 A-3 で openssl 出力を取得）

## 2. サーバー資源確認（完了・対処見送り）
- Mem available 26Gi / load average 0.12〜0.23 / ollama ps 空 → 現在の逼迫なし
- Swap 969Mi/979Mi 満杯だが vmstat で si/so=0 → 過去のOllamaモデルロード時の
  メモリ圧迫の痕跡と判断。「反応が遅い」報告の原因と推定
- swapoff/swapon による掃き出しは構造的再発が見込まれるため見送り。
  根本対応は本番展開時の専用サーバー選定で扱う

## 3. 実機バージイン受入テスト（全項目OK・session 17）
- 条件: 1A / 中小企業 / 音声 / 純音声新規セッション
- ① 警告なしでマイク許可ダイアログ表示: OK
- ② AI音声再生中の割込で即時停止: OK
- ③ 割込後の応答が割込発話内容を反映: OK
- ④ interrupted記録: OK（warnings_json に「[interrupted] 打ち切り位置までの
  再生分のみ（以降は評価対象外）」、再生済み範囲のみ text に保持）。
  審判JSONスキーマは同一 scene・role の text セッションと一致
- 補足判明事項: ルーブリックは scene でなく role（supervisor=面談5ステップ/
  subordinate=ぐそきたか）で切替。モード起因のスキーマ差なし

## 4. 新規起票（詳細は P3_ACCEPTANCE_REPORT_REQUEST_v2.md）
- D-06: テキスト履歴ありセッションの音声切替で _seed_opening 後に
  Gemini 1007 (invalid argument)、音声接続不能。純音声新規は正常（session 14 で再現）
- D-07: 審判スコアの尺度逸脱（3〜7の範囲外の 0〜2 が出力される。session 9/12/16）
- D-08: 審判が存在しない発話を評価根拠に採点（session 10、goal_level_reached 含む）
- 要確認: reports重複（session 11 x2 / 12 x3）と再講評での評価変動（B→C、A/B/B）、
  sessions 13・14 の3秒差二重作成、冒頭partnerターンのみ audio_flag=0、
  入力文字起こし誤認識が審判入力に直結する構造（制約事項として明記予定）

## 5. Cursor証跡（S1_voice.md ほか）への評価
- session_id=10 は実在し、文書のスコアは DB と一致（転記は正確）
- ただしバージインは「受入スクリプトによる realtime text 代行」と明記されており
  実ブラウザ音声経路の検証ではない。バージインJSONにも矛盾あり
  （audio_chunks_before=0 なのに played に全文）
- よって P3 クローズの根拠は本日の実測（session 17）に置き換える

## 6. 検収判断
- P3 を分割: 音声経路パート = 合格 / 審判パート = 差し戻し（D-07・D-08 決着まで保留）
- Cursor には P3_ACCEPTANCE_REPORT_REQUEST_v2.md（A〜E）への証跡付き回答を要求
  （実装変更禁止・報告のみ）
